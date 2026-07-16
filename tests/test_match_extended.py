"""GET /match/coverage/distribution, /match/roadmap, /match/pivot-map 테스트 (c,y1,y2)."""

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.core.security import create_access_token
from app.main import app
from app.models import JobCategory, Posting, PostingCategory, PostingTech, Resume, ResumeSkill, Skill, User
from app.services.match import build_posting_pool_query


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


def test_build_posting_pool_query_includes_recent_closed_excludes_old() -> None:
    """시장 모수 헬퍼는 더 이상 "마감 전 공고만"이 아니라 "최근 3년 이내 게시(마감
    포함)"를 기준으로 삼는다 — 국내 시장은 마감된 공고가 압도적으로 많아 예전 기준으로는
    표본이 너무 작았다(약 347건). 대신 3년보다 오래된 공고는 트렌드가 바뀌었을 수 있어
    마감 여부와 무관하게 제외하고, post_date가 없는 공고는 조용히 잃지 않도록 포함한다."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as seed:
        open_posting = Posting(
            source="jumpit", source_uid="open1", pool="domestic", company="Open Co",
            title="Open Posting", post_date=date(2026, 1, 1),
            close_date=date.today() + timedelta(days=7),
        )
        evergreen_posting = Posting(
            source="jumpit", source_uid="evergreen1", pool="domestic", company="Evergreen Co",
            title="Evergreen Posting", post_date=date(2026, 1, 1),
            close_date=None,
        )
        # 마감은 됐지만 게시일은 최근(3년 이내) — 이제는 시장 모수에 포함되어야 한다.
        recent_closed_posting = Posting(
            source="jumpit", source_uid="recent_closed1", pool="domestic", company="Recent Closed Co",
            title="Recent Closed Posting", post_date=date(2026, 1, 1),
            close_date=date(2026, 2, 1),
        )
        # post_date가 없는 공고 — 3년 필터로 조용히 빠지면 안 된다.
        no_post_date_posting = Posting(
            source="jumpit", source_uid="noDate1", pool="domestic", company="No Date Co",
            title="No Post Date Posting", post_date=None, close_date=None,
        )
        # 3년보다 오래전에 게시된 공고 — 마감 여부와 무관하게 제외되어야 한다.
        old_posting = Posting(
            source="jumpit", source_uid="old1", pool="domestic", company="Old Co",
            title="Old Posting (2020)", post_date=date(2020, 1, 1),
            close_date=None,
        )
        seed.add_all(
            [open_posting, evergreen_posting, recent_closed_posting, no_post_date_posting, old_posting]
        )
        seed.commit()

    with testing_session() as session:
        pool_query = build_posting_pool_query(pool="domestic", position=None).subquery()
        posting_ids = set(session.scalars(select(pool_query.c.id)).all())

    assert posting_ids == {
        open_posting.id,
        evergreen_posting.id,
        recent_closed_posting.id,
        no_post_date_posting.id,
    }
    assert old_posting.id not in posting_ids
