"""GET /stats/posting-timeline, /stats/response-rate 테스트."""

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
from app.models import Posting, PostingCategory, PostingTech, Resume, ResumeSkill, Skill, User


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        python = Skill(canonical="Python", category="language")
        java = Skill(canonical="Java", category="language")
        user = User(email="timeline@example.com", password_hash="unused")
        seed.add_all([python, java, user])
        seed.flush()

        resume = Resume(user_id=user.id, title="Backend", position="backend", pool="domestic")
        seed.add(resume)
        seed.commit()
        seed.add(ResumeSkill(resume_id=resume.resume_id, skill_id=python.id))
        seed.commit()

        p1 = Posting(
            source="jumpit", source_uid="p1", pool="domestic", company="Toss",
            title="Backend A", post_date=date(2026, 7, 10), response_rate=92.0,
        )
        p2 = Posting(
            source="jumpit", source_uid="p2", pool="domestic", company="Kakao",
            title="Backend B", post_date=date(2026, 7, 10), response_rate=90.0,
        )
        p3 = Posting(
            source="jumpit", source_uid="p3", pool="domestic", company="Naver",
            title="Backend C", post_date=date(2026, 7, 9), response_rate=55.0,
        )
        p4 = Posting(
            source="jumpit", source_uid="p4", pool="domestic", company="Woowa",
            title="Backend D", post_date=date(2026, 7, 1), response_rate=25.0,
        )
        p5 = Posting(
            source="wanted", source_uid="p5", pool="domestic", company="Line",
            title="Backend E", post_date=date(2026, 7, 1), response_rate=10.0,
        )
        p_global = Posting(
            source="wwr", source_uid="pg1", pool="global", company="RemoteCo",
            title="Remote Backend", post_date=date(2026, 7, 10), response_rate=99.0,
        )
        seed.add_all([p1, p2, p3, p4, p5, p_global])
        seed.commit()

        seed.add_all(
            [
                PostingCategory(posting_id=p1.id, category="backend"),
                PostingCategory(posting_id=p2.id, category="frontend"),
                PostingCategory(posting_id=p3.id, category="backend"),
                PostingCategory(posting_id=p4.id, category="backend"),
                PostingCategory(posting_id=p5.id, category="frontend"),
                PostingCategory(posting_id=p_global.id, category="backend"),
            ]
        )

        seed.add_all(
            [
                PostingTech(posting_id=p1.id, skill_id=python.id),
                PostingTech(posting_id=p2.id, skill_id=java.id),
                PostingTech(posting_id=p3.id, skill_id=python.id),
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


# ---- posting-timeline ----


def test_posting_timeline_requires_pool(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/posting-timeline")
    assert resp.status_code == 422


def test_posting_timeline_totals_by_day(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/posting-timeline", params={"pool": "domestic", "days": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] == "2026-07-10"
    by_date = {d["date"]: d for d in body["daily"]}
    assert by_date["2026-07-10"]["total"] == 2
    assert by_date["2026-07-09"]["total"] == 1
    assert by_date["2026-07-01"]["total"] == 2
    assert "matched" not in by_date["2026-07-10"]


def test_posting_timeline_supports_recent_365_days(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/posting-timeline", params={"pool": "domestic", "days": 365})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["daily"]) == 365
    assert body["daily"][0]["date"] == "2025-07-11"
    assert body["daily"][-1]["date"] == body["as_of"] == "2026-07-10"


def test_posting_timeline_days_out_of_range_422(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/posting-timeline", params={"pool": "domestic", "days": 366})
    assert resp.status_code == 422


def test_posting_timeline_filters_by_position(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/stats/posting-timeline",
        params={"pool": "domestic", "days": 10, "position": "backend"},
    )
    assert resp.status_code == 200
    body = resp.json()
    by_date = {d["date"]: d for d in body["daily"]}
    assert body["as_of"] == "2026-07-10"
    assert by_date["2026-07-10"]["total"] == 1
    assert by_date["2026-07-09"]["total"] == 1
    assert by_date["2026-07-01"]["total"] == 1


def test_posting_timeline_matched_with_resume(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    resp = client.get(
        "/api/v1/stats/posting-timeline",
        params={"pool": "domestic", "days": 10, "resume_id": client.resume_id},
        headers={"Authorization": f"Bearer {client.token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    by_date = {d["date"]: d for d in body["daily"]}
    # 07-10: p1(python, owned)=matched, p2(java)=not -> matched=1
    assert by_date["2026-07-10"]["matched"] == 1
    # 07-09: p3(python)=matched -> matched=1
    assert by_date["2026-07-09"]["matched"] == 1
    # 07-01: p4,p5 no skills -> matched=0
    assert by_date["2026-07-01"]["matched"] == 0


# ---- response-rate ----


def test_response_rate_defaults_to_domestic(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/response-rate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sample_size"] == 5
    assert body["median_rate"] == 55.0


def test_response_rate_levels_bucket_into_5(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/response-rate", params={"pool": "domestic"})
    body = resp.json()
    assert len(body["levels"]) == 5
    total_n = sum(level["n"] for level in body["levels"])
    assert total_n == 5


def test_response_rate_companies_sorted_desc(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/response-rate", params={"pool": "domestic"})
    body = resp.json()
    top = body["companies"][0]
    assert top["company"] == "Toss"
    assert top["rate"] == 92.0
    assert top["n"] == 1


def test_response_rate_global_pool_excludes_domestic(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/response-rate", params={"pool": "global"})
    body = resp.json()
    assert body["sample_size"] == 1
    assert body["median_rate"] == 99.0
