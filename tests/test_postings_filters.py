"""GET /postings 신규 필터 테스트 — district, deadline_within_days, min_match."""

import json
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
            industry="금융IT/핀테크",
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
        sparse_desc = Posting(
            source="wanted", source_uid="w2", pool="domestic", company="Sparse Co",
            title="Sparse Description Posting", post_date=date(2026, 7, 1),
            description=json.dumps([{"title": "x", "text": "short"}]),
        )
        rich_desc = Posting(
            source="wanted", source_uid="w3", pool="domestic", company="Rich Co",
            title="Rich Description Posting", post_date=date(2026, 7, 1),
            description=json.dumps(
                [
                    {
                        "title": "업무 소개",
                        "text": "저희 팀은 대규모 트래픽을 처리하는 백엔드 시스템을 설계하고 운영합니다. "
                        "신규 입사자는 온보딩 기간 동안 서비스 아키텍처 전반을 학습하며, 이후 주요 도메인의 "
                        "API 설계와 데이터 모델링, 성능 최적화 업무를 함께 담당하게 됩니다. 협업 문화를 "
                        "중요하게 생각하며 코드 리뷰와 페어 프로그래밍을 적극 활용합니다. 또한 장애 대응 "
                        "프로세스를 함께 만들어가며, 모니터링 지표를 기반으로 한 사전 예방적 운영을 지향합니다. "
                        "입사 후에는 사수와 함께 3개월간 온보딩 프로젝트를 진행하며 실전 감각을 익히게 됩니다.",
                    }
                ],
                ensure_ascii=False,
            ),
        )
        seed.add_all([gangnam_urgent, gangnam_far, mapo, sparse_desc, rich_desc])
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


def test_industry_filter(client: TestClient) -> None:
    resp = client.get("/api/v1/postings", params={"pool": "domestic", "industry": "핀테크"})
    assert resp.status_code == 200
    companies = {item["company"] for item in resp.json()["items"]}
    assert companies == {"Toss"}


def test_skills_filter_matches_any(client: TestClient) -> None:
    resp = client.get("/api/v1/postings", params={"pool": "domestic", "skills": "Spring"})
    assert resp.status_code == 200
    companies = {item["company"] for item in resp.json()["items"]}
    assert companies == {"Woowa", "Kakao"}


def test_rich_only_filter_excludes_sparse_descriptions(client: TestClient) -> None:
    resp = client.get("/api/v1/postings", params={"pool": "domestic", "rich_only": True})
    assert resp.status_code == 200
    companies = {item["company"] for item in resp.json()["items"]}
    assert "Rich Co" in companies
    assert "Sparse Co" not in companies


def test_rich_only_omitted_includes_both(client: TestClient) -> None:
    resp = client.get("/api/v1/postings", params={"pool": "domestic"})
    assert resp.status_code == 200
    companies = {item["company"] for item in resp.json()["items"]}
    assert {"Rich Co", "Sparse Co"} <= companies


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
