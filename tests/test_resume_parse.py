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
from app.models.cert import Cert
from app.models.resume import Resume, ResumeCert, ResumeSkill
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
        seed.add(Cert(name="AWS Certified Solutions Architect"))
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
        lambda _: (
            "Backend developer with 3-5 years using Python, AWS, and 리액트. "
            "Also used MysteryTool. Certified: AWS Certified Solutions Architect."
        ),
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
        "certs": [
            {"name": "AWS Certified Solutions Architect", "in_dict": True},
        ],
        "position": "backend",
        "career_min": 3,
        "career_max": 5,
        "resume_text": (
            "Backend developer with 3-5 years using Python, AWS, and 리액트. "
            "Also used MysteryTool. Certified: AWS Certified Solutions Architect."
        ),
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
            "memo": None,
            "resume_text": None,
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


def test_resume_feedback_uses_confirmed_session_without_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.resume.get_resume_confirm_session",
        lambda session_id: {
            "skills": [
                {"canonical": "Python", "category": "language", "in_dict": True},
            ],
            "position": "backend",
            "pool": "global",
        },
    )
    monkeypatch.setattr(
        "app.routers.resume.generate_resume_feedback",
        lambda *, skills, position, session, pool, memo=None: {
            "feedback": ["Docker 경험을 프로젝트 설명에 보강해보세요."],
            "questions": ["Python을 선택한 이유를 설명해주세요."],
            "model": "primary",
            "degraded": False,
        },
    )

    response = TestClient(app).post(
        "/api/v1/resume/feedback",
        json={"session_id": "b1f9c0e2", "position": "backend"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "feedback": ["Docker 경험을 프로젝트 설명에 보강해보세요."],
        "questions": ["Python을 선택한 이유를 설명해주세요."],
        "model": "primary",
        "degraded": False,
    }


def test_resume_feedback_returns_404_for_missing_session(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.resume.get_resume_confirm_session",
        lambda session_id: None,
    )

    response = TestClient(app).post(
        "/api/v1/resume/feedback",
        json={"session_id": "missing", "position": "backend"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "resume session not found"


def test_resume_feedback_falls_back_when_gemini_is_unavailable(monkeypatch) -> None:
    from app.services import resume_feedback

    monkeypatch.setattr(resume_feedback.settings, "gemini_api_key", None)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        response = resume_feedback.generate_resume_feedback(
            skills=[
                {"canonical": "Python", "category": "language", "in_dict": True},
            ],
            position="backend",
            session=session,
            pool=None,
        )

    assert response.degraded is True
    assert response.model == "fallback"
    assert response.feedback
    assert response.questions


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


def test_get_resume_confirm_session_reads_prefixed_redis_key(monkeypatch) -> None:
    from app.core import redis as redis_module

    captured: dict[str, str] = {}

    class FakeRedis:
        def get(self, key: str) -> str:
            captured["get_key"] = key
            return '{"skills": [{"canonical": "Python", "category": "language", "in_dict": true}]}'

    monkeypatch.setattr(redis_module, "redis_client", FakeRedis())

    assert redis_module.get_resume_confirm_session("b1f9c0e2") == {
        "skills": [
            {"canonical": "Python", "category": "language", "in_dict": True},
        ]
    }
    assert captured == {"get_key": "resume_confirm:b1f9c0e2"}


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
        aws_cert = Cert(name="AWS Certified Solutions Architect")
        seed.add_all([user, python, aws_cert])
        seed.commit()
        user_id = user.id
        python_id = python.id
        aws_cert_id = aws_cert.id

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
                "certs": [
                    {"name": "AWS Certified Solutions Architect", "in_dict": True},
                    {"name": "MysteryCert", "in_dict": False},
                ],
                "position": "backend",
                "career_min": 3,
                "career_max": 5,
                "pool": "global",
            },
        )

        assert response.status_code == 201
        assert response.json() == {"resume_id": 1}

        detail_response = client.get(
            "/api/v1/resume/1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert detail_response.status_code == 200
        assert detail_response.json()["certs"] == [
            {"name": "AWS Certified Solutions Architect", "in_dict": True},
            {"name": "MysteryCert", "in_dict": False},
        ]
        assert detail_response.json()["is_primary"] is True

        with testing_session() as session:
            resume = session.get(Resume, 1)
            assert resume is not None
            assert resume.user_id == user_id
            assert resume.title == "Backend resume v2"
            assert resume.position == "backend"
            assert resume.career_min == 3
            assert resume.career_max == 5
            assert resume.pool == "global"
            # 유저의 첫 이력서이므로 자동으로 기본 이력서가 되어야 한다.
            assert resume.is_primary is True

            stored_skills = session.query(ResumeSkill).order_by(ResumeSkill.id).all()
            assert len(stored_skills) == 2
            assert stored_skills[0].skill_id == python_id
            assert stored_skills[0].raw_label is None
            assert stored_skills[0].is_out_of_dict is False
            assert stored_skills[1].skill_id is None
            assert stored_skills[1].raw_label == "MysteryTool"
            assert stored_skills[1].is_out_of_dict is True

            stored_certs = session.query(ResumeCert).order_by(ResumeCert.id).all()
            assert len(stored_certs) == 2
            assert stored_certs[0].cert_id == aws_cert_id
            assert stored_certs[0].raw_label is None
            assert stored_certs[0].is_out_of_dict is False
            assert stored_certs[1].cert_id is None
            assert stored_certs[1].raw_label == "MysteryCert"
            assert stored_certs[1].is_out_of_dict is True
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
            "certs": [],
            "position": "backend",
            "career_min": 3,
            "career_max": 5,
            "pool": "global",
            "memo": None,
            "is_primary": False,
        }
    finally:
        app.dependency_overrides.clear()


def test_update_resume_replaces_meta_and_skills_for_owner(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        user = User(email="update@example.com", password_hash="unused")
        python = Skill(canonical="Python", category="language")
        kubernetes = Skill(canonical="Kubernetes", category="devops")
        seed.add_all([user, python, kubernetes])
        seed.flush()
        resume = Resume(
            user_id=user.id,
            title="Backend resume v2",
            position="backend",
            career_min=3,
            career_max=5,
            pool="domestic",
        )
        seed.add(resume)
        seed.flush()
        seed.add_all(
            [
                ResumeSkill(resume_id=resume.resume_id, skill_id=python.id),
                ResumeSkill(
                    resume_id=resume.resume_id,
                    raw_label="OldTool",
                    is_out_of_dict=True,
                ),
            ]
        )
        seed.commit()
        user_id = user.id
        resume_id = resume.resume_id
        kubernetes_id = kubernetes.id

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    monkeypatch.setattr("app.core.deps.is_token_blocklisted", lambda token: False)
    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        response = client.put(
            f"/api/v1/resume/{resume_id}",
            headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
            json={
                "title": "Backend resume v3",
                "skills": [
                    {"canonical": "Kubernetes", "category": "devops", "in_dict": True},
                    {"canonical": "NewTool", "category": "unknown", "in_dict": False},
                ],
                "position": "backend",
                "career_min": 4,
                "career_max": 6,
                "pool": "global",
            },
        )

        assert response.status_code == 200
        assert response.json() == {"resume_id": resume_id}

        detail_response = client.get(
            f"/api/v1/resume/{resume_id}",
            headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
        )
        assert detail_response.status_code == 200
        assert detail_response.json() == {
            "resume_id": resume_id,
            "title": "Backend resume v3",
            "skills": [
                {"canonical": "Kubernetes", "category": "devops", "in_dict": True},
                {"canonical": "NewTool", "category": "unknown", "in_dict": False},
            ],
            "certs": [],
            "position": "backend",
            "career_min": 4,
            "career_max": 6,
            "pool": "global",
            "memo": None,
            "is_primary": False,
        }

        with testing_session() as session:
            active_skills = (
                session.query(ResumeSkill)
                .filter(
                    ResumeSkill.resume_id == resume_id,
                    ResumeSkill.is_deleted.is_(False),
                )
                .order_by(ResumeSkill.id)
                .all()
            )
            deleted_skills_count = (
                session.query(ResumeSkill)
                .filter(
                    ResumeSkill.resume_id == resume_id,
                    ResumeSkill.is_deleted.is_(True),
                )
                .count()
            )
            assert deleted_skills_count == 2
            assert len(active_skills) == 2
            assert active_skills[0].skill_id == kubernetes_id
            assert active_skills[0].raw_label is None
            assert active_skills[1].skill_id is None
            assert active_skills[1].raw_label == "NewTool"
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
                {"resume_id": 2, "title": "Data resume", "position": "data", "is_primary": False},
                {"resume_id": 1, "title": "Backend resume v1", "position": "backend", "is_primary": False},
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


def test_update_resume_returns_404_for_missing_or_other_user(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        owner = User(email="update-owner@example.com", password_hash="unused")
        requester = User(email="update-requester@example.com", password_hash="unused")
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
        payload = {
            "title": "Backend resume v3",
            "skills": [
                {"canonical": "Python", "category": "language", "in_dict": True},
            ],
            "position": "backend",
            "career_min": 4,
            "career_max": 6,
            "pool": "global",
        }

        other_user_response = client.put(
            f"/api/v1/resume/{private_resume_id}",
            headers=headers,
            json=payload,
        )
        missing_response = client.put("/api/v1/resume/9999", headers=headers, json=payload)

        assert other_user_response.status_code == 404
        assert other_user_response.json()["detail"] == "resume not found"
        assert missing_response.status_code == 404
        assert missing_response.json()["detail"] == "resume not found"
    finally:
        app.dependency_overrides.clear()


def test_delete_resume_soft_deletes_owner_resume_and_children(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        user = User(email="delete-owner@example.com", password_hash="unused")
        seed.add(user)
        seed.flush()
        resume = Resume(
            user_id=user.id,
            title="Delete me",
            position="backend",
            career_min=1,
            career_max=2,
            pool="domestic",
        )
        seed.add(resume)
        seed.flush()
        seed.add_all(
            [
                ResumeSkill(resume_id=resume.resume_id, raw_label="LegacyTool"),
                ResumeCert(resume_id=resume.resume_id, raw_label="LegacyCert"),
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
        headers = {"Authorization": f"Bearer {create_access_token(user_id)}"}

        response = client.delete(f"/api/v1/resume/{resume_id}", headers=headers)

        assert response.status_code == 204
        assert response.content == b""
        assert client.get(f"/api/v1/resume/{resume_id}", headers=headers).status_code == 404
        assert client.get("/api/v1/resume", headers=headers).json() == {"items": []}

        with testing_session() as session:
            resume = session.get(Resume, resume_id)
            skill = session.query(ResumeSkill).filter_by(resume_id=resume_id).one()
            cert = session.query(ResumeCert).filter_by(resume_id=resume_id).one()

            assert resume is not None
            assert resume.is_deleted is True
            assert resume.deleted_at is not None
            assert skill.is_deleted is True
            assert skill.deleted_at is not None
            assert cert.is_deleted is True
            assert cert.deleted_at is not None
    finally:
        app.dependency_overrides.clear()


def test_delete_resume_returns_404_for_missing_or_other_user(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        owner = User(email="delete-private-owner@example.com", password_hash="unused")
        requester = User(email="delete-private-requester@example.com", password_hash="unused")
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

        other_user_response = client.delete(
            f"/api/v1/resume/{private_resume_id}",
            headers=headers,
        )
        missing_response = client.delete("/api/v1/resume/9999", headers=headers)

        assert other_user_response.status_code == 404
        assert other_user_response.json()["detail"] == "resume not found"
        assert missing_response.status_code == 404
        assert missing_response.json()["detail"] == "resume not found"

        with testing_session() as session:
            resume = session.get(Resume, private_resume_id)
            assert resume is not None
            assert resume.is_deleted is False
            assert resume.deleted_at is None
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


def test_resume_primary_unique_index_rejects_second_primary_per_user() -> None:
    from sqlalchemy.exc import IntegrityError

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as session:
        user = User(email="primary@example.com", password_hash="unused")
        session.add(user)
        session.flush()
        session.add(
            Resume(
                user_id=user.id, title="A", position="backend",
                career_min=0, career_max=1, pool="domestic", is_primary=True,
            )
        )
        session.commit()

        session.add(
            Resume(
                user_id=user.id, title="B", position="backend",
                career_min=0, career_max=1, pool="domestic", is_primary=True,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    # Verify that multiple non-primary resumes per user are still allowed
    # (this ensures the index is truly partial, not a full unique on user_id)
    with testing_session() as assertion_session:
        user = assertion_session.query(User).filter_by(email="primary@example.com").one()
        assertion_session.add(
            Resume(
                user_id=user.id, title="C", position="backend",
                career_min=0, career_max=1, pool="domestic", is_primary=False,
            )
        )
        assertion_session.commit()  # Should succeed without IntegrityError


def test_set_primary_resume_switches_flag_and_returns_updated_list(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        user = User(email="setprimary@example.com", password_hash="unused")
        seed.add(user)
        seed.flush()
        first = Resume(
            user_id=user.id, title="First", position="backend",
            career_min=0, career_max=1, pool="domestic", is_primary=True,
        )
        second = Resume(
            user_id=user.id, title="Second", position="backend",
            career_min=0, career_max=1, pool="domestic", is_primary=False,
        )
        seed.add_all([first, second])
        seed.commit()
        user_id = user.id
        first_id, second_id = first.resume_id, second.resume_id

    def override_get_session():
        with testing_session() as session:
            yield session

    monkeypatch.setattr("app.core.deps.is_token_blocklisted", lambda token: False)
    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        response = client.post(
            f"/api/v1/resume/{second_id}/primary",
            headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
        )

        assert response.status_code == 200
        items = {item["resume_id"]: item["is_primary"] for item in response.json()["items"]}
        assert items[first_id] is False
        assert items[second_id] is True
    finally:
        app.dependency_overrides.clear()


def test_delete_primary_resume_promotes_most_recently_updated_remaining(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        user = User(email="deleteprimary@example.com", password_hash="unused")
        seed.add(user)
        seed.flush()
        primary = Resume(
            user_id=user.id, title="Primary", position="backend",
            career_min=0, career_max=1, pool="domestic", is_primary=True,
        )
        other = Resume(
            user_id=user.id, title="Other", position="backend",
            career_min=0, career_max=1, pool="domestic", is_primary=False,
        )
        seed.add_all([primary, other])
        seed.commit()
        user_id = user.id
        primary_id, other_id = primary.resume_id, other.resume_id

    def override_get_session():
        with testing_session() as session:
            yield session

    monkeypatch.setattr("app.core.deps.is_token_blocklisted", lambda token: False)
    app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(app)
        response = client.delete(
            f"/api/v1/resume/{primary_id}",
            headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
        )
        assert response.status_code == 204

        with testing_session() as session:
            remaining = session.get(Resume, other_id)
            assert remaining.is_primary is True
    finally:
        app.dependency_overrides.clear()


def test_generate_resume_feedback_includes_memo_in_prompt(monkeypatch) -> None:
    from app.services import resume_feedback

    captured_prompts = []

    def fake_generate_with_gemini(*, skills, position, market_skills, memo=None):
        captured_prompts.append(
            resume_feedback._build_prompt(
                skills=skills, position=position, market_skills=market_skills, memo=memo
            )
        )
        return (["feedback"], ["question"])

    monkeypatch.setattr(resume_feedback, "_generate_with_gemini", fake_generate_with_gemini)
    monkeypatch.setattr(resume_feedback, "_get_market_demand_skills", lambda **kwargs: ["Docker"])

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        resume_feedback.generate_resume_feedback(
            skills=[{"canonical": "Python", "category": "language", "in_dict": True}],
            position="backend",
            session=session,
            pool="domestic",
            memo="3년차 백엔드, 대용량 트래픽 프로젝트 경험 위주로 봐주세요.",
        )

    assert "3년차 백엔드" in captured_prompts[0]
