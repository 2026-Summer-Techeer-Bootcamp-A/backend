"""GET /postings 신규 필터 테스트 — district, deadline_within_days, min_match."""

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.core.security import create_access_token
from app.main import app
from app.models import Posting, PostingTech, Resume, ResumeSkill, Skill, User

TODAY = date.today()


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        python = Skill(canonical="Python", category="language")
        spring = Skill(canonical="Spring", category="framework")
        user = User(email="filters@example.com", password_hash="unused")
        seed.add_all([python, spring, user])
        seed.flush()

        resume = Resume(user_id=user.id, title="Backend", position="backend", pool="domestic")
        seed.add(resume)
        seed.commit()
        seed.add(ResumeSkill(resume_id=resume.resume_id, skill_id=python.id))
        seed.commit()

        gangnam_urgent = Posting(
            source="jumpit", source_uid="j1", pool="domestic", company="Toss",
            title="Backend Engineer", post_date=date(2026, 7, 1),
            close_date=TODAY + timedelta(days=3), region_district="강남구",
        )
        gangnam_far = Posting(
            source="jumpit", source_uid="j2", pool="domestic", company="Woowa",
            title="Server Engineer", post_date=date(2026, 7, 1),
            close_date=TODAY + timedelta(days=60), region_district="강남구",
        )
        mapo = Posting(
            source="wanted", source_uid="w1", pool="domestic", company="Kakao",
            title="Backend Platform", post_date=date(2026, 7, 1),
            close_date=TODAY + timedelta(days=5), region_district="마포구",
        )
        seed.add_all([gangnam_urgent, gangnam_far, mapo])
        seed.commit()

        seed.add_all(
            [
                # gangnam_urgent: python 1개 요구, 보유 -> 100%
                PostingTech(posting_id=gangnam_urgent.id, skill_id=python.id),
                # gangnam_far: python+spring 요구, python만 보유 -> 50%
                PostingTech(posting_id=gangnam_far.id, skill_id=python.id),
                PostingTech(posting_id=gangnam_far.id, skill_id=spring.id),
                # mapo: spring만 요구, 미보유 -> 0%
                PostingTech(posting_id=mapo.id, skill_id=spring.id),
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


def test_district_filter(client: TestClient) -> None:
    resp = client.get("/api/v1/postings", params={"pool": "domestic", "district": "강남"})
    assert resp.status_code == 200
    companies = {item["company"] for item in resp.json()["items"]}
    assert companies == {"Toss", "Woowa"}


def test_deadline_within_days_filter(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/postings", params={"pool": "domestic", "deadline_within_days": 7}
    )
    assert resp.status_code == 200
    companies = {item["company"] for item in resp.json()["items"]}
    assert companies == {"Toss", "Kakao"}


def test_min_match_requires_resume_id(client: TestClient) -> None:
    resp = client.get("/api/v1/postings", params={"pool": "domestic", "min_match": 50})
    assert resp.status_code == 422


def test_min_match_filters_by_coverage_ratio(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    resp = client.get(
        "/api/v1/postings",
        params={"pool": "domestic", "resume_id": client.resume_id, "min_match": 60},
        headers={"Authorization": f"Bearer {client.token}"},
    )
    assert resp.status_code == 200
    companies = {item["company"] for item in resp.json()["items"]}
    # Toss(100%)만 60% 이상. Woowa(50%), Kakao(0%)는 제외.
    assert companies == {"Toss"}
    assert resp.json()["items"][0]["matched_count"] == 1


def test_sort_match_orders_by_matched_count_desc(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routers.match.is_token_blocklisted", lambda token: False)
    resp = client.get(
        "/api/v1/postings",
        params={"pool": "domestic", "resume_id": client.resume_id, "sort": "match"},
        headers={"Authorization": f"Bearer {client.token}"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    matched_counts = [item["matched_count"] for item in items]
    # Toss(1/1), Woowa(1/2)가 matched_count=1로 Kakao(0/1)보다 앞에 와야 한다.
    assert matched_counts == sorted(matched_counts, reverse=True)
    assert {item["company"] for item in items[:2]} == {"Toss", "Woowa"}
    assert items[-1]["company"] == "Kakao"
    assert items[-1]["matched_count"] == 0


def test_sort_match_without_resume_context_falls_back_to_latest(client: TestClient) -> None:
    # resume_id/인증이 없으면 sort=match는 422 없이 최신순으로 안전하게 폴백한다.
    resp = client.get("/api/v1/postings", params={"pool": "domestic", "sort": "match"})
    assert resp.status_code == 200
    companies = {item["company"] for item in resp.json()["items"]}
    assert companies == {"Toss", "Woowa", "Kakao"}
    assert all(item.get("matched_count") is None for item in resp.json()["items"])


# skills 필터: gangnam_urgent(Toss)=python만, gangnam_far(Woowa)=python+spring,
# mapo(Kakao)=spring만 요구한다.


def test_skills_filter_single_skill_matches_only_postings_with_that_skill(client: TestClient) -> None:
    resp = client.get("/api/v1/postings", params={"pool": "domestic", "skills": "Python"})
    assert resp.status_code == 200
    body = resp.json()
    companies = {item["company"] for item in body["items"]}
    assert companies == {"Toss", "Woowa"}
    assert body["total"] == len(body["items"]) == 2


def test_skills_filter_or_matches_posting_with_only_one_requested_skill(client: TestClient) -> None:
    # mapo(Kakao)는 Spring만 요구하는데, Python도 함께 요청해도(OR) 포함돼야 한다.
    resp = client.get("/api/v1/postings", params={"pool": "domestic", "skills": "Python,Spring"})
    assert resp.status_code == 200
    body = resp.json()
    companies = {item["company"] for item in body["items"]}
    assert companies == {"Toss", "Woowa", "Kakao"}
    assert body["total"] == len(body["items"]) == 3

    posting_ids = [item["id"] for item in body["items"]]
    assert len(posting_ids) == len(set(posting_ids))  # 중복 없음 (Woowa는 두 스킬 다 매칭)


def test_skills_filter_no_match_returns_empty(client: TestClient) -> None:
    resp = client.get("/api/v1/postings", params={"pool": "domestic", "skills": "Rust"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_skills_filter_ignores_blank_entries_and_whitespace(client: TestClient) -> None:
    resp = client.get("/api/v1/postings", params={"pool": "domestic", "skills": " Python , , "})
    assert resp.status_code == 200
    body = resp.json()
    companies = {item["company"] for item in body["items"]}
    assert companies == {"Toss", "Woowa"}
