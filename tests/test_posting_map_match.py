"""GET /postings/map — resume_id/session_id를 넘겼을 때 matchPct/clusters 검증."""

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
from app.models import Posting, PostingTech, Resume, ResumeSkill, Skill, User


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        python = Skill(canonical="Python", category="language")
        spring = Skill(canonical="Spring", category="framework")
        aws = Skill(canonical="AWS", category="cloud")
        user = User(email="map@example.com", password_hash="unused")
        seed.add_all([python, spring, aws, user])
        seed.flush()

        resume = Resume(user_id=user.id, title="Backend", position="backend", pool="domestic")
        seed.add(resume)
        seed.commit()
        seed.add(ResumeSkill(resume_id=resume.resume_id, skill_id=python.id))
        seed.commit()

        p1 = Posting(
            source="jumpit", source_uid="j1", pool="domestic", company="Toss",
            title="Backend Engineer", post_date=date(2026, 7, 1),
            lat=37.50, lng=127.03, region_district="강남구",
        )
        p2 = Posting(
            source="jumpit", source_uid="j2", pool="domestic", company="Kakao",
            title="Server Engineer", post_date=date(2026, 7, 2),
            lat=37.52, lng=127.05, region_district="강남구",
        )
        seed.add_all([p1, p2])
        seed.commit()

        seed.add_all(
            [
                # p1: python+spring 요구, python만 보유 -> 50%
                PostingTech(posting_id=p1.id, skill_id=python.id),
                PostingTech(posting_id=p1.id, skill_id=spring.id),
                # p2: aws만 요구, 미보유 -> 0%
                PostingTech(posting_id=p2.id, skill_id=aws.id),
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


def test_map_without_resume_has_no_match_fields(client: TestClient) -> None:
    resp = client.get("/api/v1/postings/map")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pins"][0]["match_pct"] is None
    assert body["clusters"][0]["avg_match_pct"] is None


def test_map_with_resume_fills_match_pct_and_clusters(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    resp = client.get(
        "/api/v1/postings/map",
        params={"resume_id": client.resume_id},
        headers={"Authorization": f"Bearer {client.token}"},
    )
    assert resp.status_code == 200
    body = resp.json()

    by_company = {pin["company"]: pin for pin in body["pins"]}
    assert by_company["Toss"]["match_pct"] == 50.0
    assert by_company["Kakao"]["match_pct"] == 0.0

    assert len(body["clusters"]) == 1
    cluster = body["clusters"][0]
    assert cluster["district"] == "강남구"
    assert cluster["count"] == 2
    assert cluster["avg_match_pct"] == 25.0  # (50+0)/2
