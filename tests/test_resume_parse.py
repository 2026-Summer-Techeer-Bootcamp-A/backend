from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.core.security import create_access_token
from app.main import app
from app.models.user import User
from app.models.resume import Resume, ResumeSkill
from app.models.skill import Skill, SkillAlias


@pytest.fixture
def client_with_skill_dictionary() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        python = Skill(canonical="Python", category="language")
        react = Skill(canonical="React", category="frontend")
        seed.add_all([python, react])
        seed.flush()
        seed.add_all(
            [
                SkillAlias(skill_id=python.id, alias="파이썬", is_korean=True),
                SkillAlias(skill_id=react.id, alias="리액트", is_korean=True),
            ]
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_parse_resume_rejects_non_pdf_upload() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/v1/resume/parse",
        files={"file": ("resume.pdf", b"Python AWS", "application/pdf")},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "unsupported media type"


def test_parse_resume_returns_skills_position_and_career(
    monkeypatch, client_with_skill_dictionary: TestClient
) -> None:
    from app.services import resume as resume_service

    monkeypatch.setattr(
        resume_service,
        "extract_pdf_text",
        lambda _: "Backend developer with 3-5 years using Python, AWS, and 리액트. Also used MysteryTool.",
    )

    response = client_with_skill_dictionary.post(
        "/api/v1/resume/parse",
        files={"file": ("resume.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "skills": [
            {"canonical": "Python", "category": "language", "in_dict": True},
            {"canonical": "AWS", "category": "unknown", "in_dict": False},
            {"canonical": "React", "category": "frontend", "in_dict": True},
            {"canonical": "MysteryTool", "category": "unknown", "in_dict": False},
        ],
        "position": "backend",
        "career_min": 3,
        "career_max": 5,
    }


def test_confirm_resume_stores_confirmed_input_in_session(
    monkeypatch, client_with_skill_dictionary: TestClient
) -> None:
    captured: dict[str, object] = {}

    def fake_create_resume_confirm_session(payload: dict[str, object], ttl: int) -> str:
        captured["payload"] = payload
        captured["ttl"] = ttl
        return "b1f9c0e2"

    monkeypatch.setattr(
        "app.routers.resume.create_resume_confirm_session",
        fake_create_resume_confirm_session,
    )

    response = client_with_skill_dictionary.post(
        "/api/v1/resume/confirm",
        json={
            "skills": [
                {"canonical": "Python", "category": "language", "in_dict": True},
                {"canonical": "AWS", "category": "devops", "in_dict": True},
            ],
            "position": "backend",
            "career_min": 3,
            "career_max": 5,
            "pool": "global",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"session_id": "b1f9c0e2", "ttl": 3600}
    assert captured == {
        "payload": {
            "skills": [
                {"canonical": "Python", "category": "language", "in_dict": True},
                {"canonical": "AWS", "category": "devops", "in_dict": True},
            ],
            "position": "backend",
            "career_min": 3,
            "career_max": 5,
            "pool": "global",
        },
        "ttl": 3600,
    }


def test_confirm_resume_rejects_invalid_pool(
    monkeypatch, client_with_skill_dictionary: TestClient
) -> None:
    monkeypatch.setattr(
        "app.routers.resume.create_resume_confirm_session",
        lambda payload, ttl: "unused",
    )

    response = client_with_skill_dictionary.post(
        "/api/v1/resume/confirm",
        json={
            "skills": [
                {"canonical": "Python", "category": "language", "in_dict": True},
            ],
            "pool": "mixed",
        },
    )

    assert response.status_code == 422


def test_create_resume_confirm_session_uses_prefixed_redis_key(monkeypatch) -> None:
    from app.core import redis as redis_module

    captured: dict[str, object] = {}

    class FakeRedis:
        def exists(self, key: str) -> int:
            captured["exists_key"] = key
            return 0

        def setex(self, key: str, ttl: int, value: str) -> None:
            captured["setex_key"] = key
            captured["ttl"] = ttl
            captured["value"] = value

    monkeypatch.setattr(redis_module, "redis_client", FakeRedis())
    monkeypatch.setattr(
        redis_module.secrets,
        "token_hex",
        lambda bytes_count: "a" * (bytes_count * 2),
    )

    session_id = redis_module.create_resume_confirm_session(
        {"pool": "global"},
        ttl_seconds=3600,
    )

    expected_session_id = "a" * 32
    expected_key = f"resume_confirm:{expected_session_id}"
    assert session_id == expected_session_id
    assert captured == {
        "exists_key": expected_key,
        "setex_key": expected_key,
        "ttl": 3600,
        "value": '{"pool": "global"}',
    }


def test_extract_pdf_text_falls_back_to_pdftotext(monkeypatch) -> None:
    from app.services import resume as resume_service

    monkeypatch.setattr(resume_service, "extract_pdf_text_with_pypdf", lambda _: "")
    monkeypatch.setattr(resume_service, "extract_pdf_text_with_pdftotext", lambda _: "Python")

    assert resume_service.extract_pdf_text(b"%PDF-1.4 fake") == "Python"


def test_create_resume_stores_meta_and_skills_for_authenticated_user(
    monkeypatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        user = User(
            email="resume@example.com",
            password_hash="unused",
            nickname="resume-user",
        )
        python = Skill(canonical="Python", category="language")
        seed.add_all([user, python])
        seed.commit()
        user_id = user.id
        python_id = python.id

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    monkeypatch.setattr("app.core.deps.is_token_blocklisted", lambda token: False)
    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        token = create_access_token(user_id)
        response = client.post(
            "/api/v1/resume",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Backend resume v2",
                "skills": [
                    {"canonical": "Python", "category": "language", "in_dict": True},
                    {"canonical": "MysteryTool", "category": "unknown", "in_dict": False},
                ],
                "position": "backend",
                "career_min": 3,
                "career_max": 5,
                "pool": "global",
            },
        )

        assert response.status_code == 201
        assert response.json() == {"resume_id": 1}

        with testing_session() as session:
            resume = session.get(Resume, 1)
            assert resume is not None
            assert resume.user_id == user_id
            assert resume.title == "Backend resume v2"
            assert resume.position == "backend"
            assert resume.career_min == 3
            assert resume.career_max == 5
            assert resume.pool == "global"

            stored_skills = session.query(ResumeSkill).order_by(ResumeSkill.id).all()
            assert len(stored_skills) == 2
            assert stored_skills[0].skill_id == python_id
            assert stored_skills[0].raw_label is None
            assert stored_skills[0].is_out_of_dict is False
            assert stored_skills[1].skill_id is None
            assert stored_skills[1].raw_label == "MysteryTool"
            assert stored_skills[1].is_out_of_dict is True
    finally:
        app.dependency_overrides.clear()


def test_get_resume_returns_detail_for_owner(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        user = User(
            email="detail@example.com",
            password_hash="unused",
            nickname="detail-user",
        )
        python = Skill(canonical="Python", category="language")
        seed.add_all([user, python])
        seed.flush()
        resume = Resume(
            user_id=user.id,
            title="Backend resume v2",
            position="backend",
            career_min=3,
            career_max=5,
            pool="global",
        )
        seed.add(resume)
        seed.flush()
        seed.add_all(
            [
                ResumeSkill(resume_id=resume.resume_id, skill_id=python.id),
                ResumeSkill(
                    resume_id=resume.resume_id,
                    raw_label="MysteryTool",
                    is_out_of_dict=True,
                ),
            ]
        )
        seed.commit()
        user_id = user.id
        resume_id = resume.resume_id

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    monkeypatch.setattr("app.core.deps.is_token_blocklisted", lambda token: False)
    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        response = client.get(
            f"/api/v1/resume/{resume_id}",
            headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "resume_id": resume_id,
            "title": "Backend resume v2",
            "skills": [
                {"canonical": "Python", "category": "language", "in_dict": True},
                {"canonical": "MysteryTool", "category": "unknown", "in_dict": False},
            ],
            "position": "backend",
            "career_min": 3,
            "career_max": 5,
            "pool": "global",
        }
    finally:
        app.dependency_overrides.clear()


def test_get_resume_list_returns_owned_active_resume_summaries(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        owner = User(email="list-owner@example.com", password_hash="unused")
        other = User(email="list-other@example.com", password_hash="unused")
        seed.add_all([owner, other])
        seed.flush()
        seed.add_all(
            [
                Resume(
                    user_id=owner.id,
                    title="Backend resume v1",
                    position="backend",
                    career_min=1,
                    career_max=3,
                    pool="global",
                ),
                Resume(
                    user_id=owner.id,
                    title="Data resume",
                    position="data",
                    career_min=2,
                    career_max=4,
                    pool="domestic",
                ),
                Resume(
                    user_id=other.id,
                    title="Other user's resume",
                    position="frontend",
                    career_min=1,
                    career_max=2,
                    pool="global",
                ),
                Resume(
                    user_id=owner.id,
                    title="Deleted resume",
                    position="devops",
                    career_min=5,
                    career_max=7,
                    pool="global",
                    is_deleted=True,
                ),
            ]
        )
        seed.commit()
        owner_id = owner.id

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    monkeypatch.setattr("app.core.deps.is_token_blocklisted", lambda token: False)
    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        response = client.get(
            "/api/v1/resume",
            headers={"Authorization": f"Bearer {create_access_token(owner_id)}"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "items": [
                {"resume_id": 2, "title": "Data resume", "position": "data"},
                {"resume_id": 1, "title": "Backend resume v1", "position": "backend"},
            ]
        }
    finally:
        app.dependency_overrides.clear()


def test_get_resume_returns_404_for_missing_or_other_user(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        owner = User(email="owner@example.com", password_hash="unused")
        requester = User(email="requester@example.com", password_hash="unused")
        seed.add_all([owner, requester])
        seed.flush()
        resume = Resume(
            user_id=owner.id,
            title="Private resume",
            position="backend",
            career_min=1,
            career_max=2,
            pool="domestic",
        )
        seed.add(resume)
        seed.commit()
        requester_id = requester.id
        private_resume_id = resume.resume_id

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    monkeypatch.setattr("app.core.deps.is_token_blocklisted", lambda token: False)
    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {create_access_token(requester_id)}"}

        other_user_response = client.get(
            f"/api/v1/resume/{private_resume_id}",
            headers=headers,
        )
        missing_response = client.get("/api/v1/resume/9999", headers=headers)

        assert other_user_response.status_code == 404
        assert other_user_response.json()["detail"] == "resume not found"
        assert missing_response.status_code == 404
        assert missing_response.json()["detail"] == "resume not found"
    finally:
        app.dependency_overrides.clear()


def test_create_resume_requires_authentication() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/v1/resume",
        json={
            "title": "Backend resume v2",
            "skills": [
                {"canonical": "Python", "category": "language", "in_dict": True},
            ],
            "position": "backend",
            "career_min": 3,
            "career_max": 5,
            "pool": "global",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


def test_create_resume_rejects_invalid_pool(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        user = User(email="invalid@example.com", password_hash="unused")
        seed.add(user)
        seed.commit()
        user_id = user.id

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    monkeypatch.setattr("app.core.deps.is_token_blocklisted", lambda token: False)
    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/resume",
            headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
            json={
                "title": "Backend resume v2",
                "skills": [
                    {"canonical": "Python", "category": "language", "in_dict": True},
                ],
                "position": "backend",
                "career_min": 3,
                "career_max": 5,
                "pool": "mixed",
            },
        )

        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
