"""GET /match/coverage/distribution, /match/roadmap, /match/pivot-map 테스트 (c,y1,y2)."""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.core.security import create_access_token
from app.main import app
from app.models import JobCategory, Posting, PostingCategory, PostingTech, Resume, ResumeSkill, Skill, User


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        python = Skill(canonical="Python", category="language")
        spring = Skill(canonical="Spring", category="framework")
        kubernetes = Skill(canonical="Kubernetes", category="devops")
        aws = Skill(canonical="AWS", category="cloud")
        user = User(email="pivot@example.com", password_hash="unused")
        seed.add_all([python, spring, kubernetes, aws, user])
        seed.flush()

        seed.add(JobCategory(name="backend", is_tech=True))

        resume = Resume(user_id=user.id, title="Backend", position="backend", pool="domestic")
        seed.add(resume)
        seed.commit()
        seed.add(ResumeSkill(resume_id=resume.resume_id, skill_id=python.id))
        seed.commit()

        p1 = Posting(
            source="jumpit", source_uid="j1", pool="domestic", company="Toss",
            title="Backend 1", industry="fintech", post_date=date(2026, 1, 1),
        )
        p2 = Posting(
            source="jumpit", source_uid="j2", pool="domestic", company="Kakao",
            title="Backend 2", industry="fintech", post_date=date(2026, 1, 2),
        )
        p3 = Posting(
            source="wanted", source_uid="w1", pool="domestic", company="Naver",
            title="Backend 3", industry="game", post_date=date(2026, 1, 3),
        )
        seed.add_all([p1, p2, p3])
        seed.commit()

        seed.add_all(
            [
                PostingCategory(posting_id=p1.id, category="backend"),
                PostingCategory(posting_id=p2.id, category="backend"),
                PostingCategory(posting_id=p3.id, category="backend"),
                # p1: 3개 요구, python만 보유 -> 33%
                PostingTech(posting_id=p1.id, skill_id=python.id),
                PostingTech(posting_id=p1.id, skill_id=spring.id),
                PostingTech(posting_id=p1.id, skill_id=kubernetes.id),
                # p2: 2개 요구, python 보유 -> 50%
                PostingTech(posting_id=p2.id, skill_id=python.id),
                PostingTech(posting_id=p2.id, skill_id=aws.id),
                # p3: 1개 요구(min_required_skills=3 미만이라 제외 대상)
                PostingTech(posting_id=p3.id, skill_id=aws.id),
            ]
        )
        seed.commit()
        resume_id = resume.resume_id
        user_id = user.id

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    test_client = TestClient(app)
    test_client.resume_id = resume_id  # type: ignore[attr-defined]
    test_client.token = create_access_token(str(user_id))  # type: ignore[attr-defined]
    yield test_client
    app.dependency_overrides.clear()


def test_coverage_distribution_requires_resume_or_session(client: TestClient) -> None:
    resp = client.get("/api/v1/match/coverage/distribution", params={"pool": "domestic"})
    assert resp.status_code == 400


def test_coverage_distribution_builds_histogram(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    resp = client.get(
        "/api/v1/match/coverage/distribution",
        params={"pool": "domestic", "resume_id": client.resume_id, "min_required_skills": 2},
        headers={"Authorization": f"Bearer {client.token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # p1(33%), p2(50%) 만 2개 이상 요구라 집계 대상 -> total=2
    assert body["total"] == 2
    assert sum(b["count"] for b in body["histogram"]) == 2
    assert body["matched"] == 1  # threshold=50 기본값, p2만 도달


def test_roadmap_picks_best_next_skill_first(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    resp = client.get(
        "/api/v1/match/roadmap",
        params={"pool": "domestic", "resume_id": client.resume_id, "steps": 2},
        headers={"Authorization": f"Bearer {client.token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["start_matched"] == 2  # python 보유 -> p1,p2 매칭
    assert len(body["steps"]) >= 1
    # AWS를 추가하면 p2(이미매칭)+p3(신규) = 3건으로 가장 크게 늘어남
    assert body["steps"][0]["canonical"] == "AWS"
    assert body["steps"][0]["matched_after"] == 3


def test_pivot_map_category_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    resp = client.get(
        "/api/v1/match/pivot-map",
        params={"pool": "domestic", "resume_id": client.resume_id, "kind": "category"},
        headers={"Authorization": f"Bearer {client.token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["targets"]) == 1
    assert body["targets"][0]["name"] == "backend"
    assert body["targets"][0]["kind"] == "category"


def test_pivot_map_rejects_invalid_kind(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/match/pivot-map",
        params={"pool": "domestic", "resume_id": client.resume_id, "kind": "nonsense"},
        headers={"Authorization": f"Bearer {client.token}"},
    )
    assert resp.status_code == 422
