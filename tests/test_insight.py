"""Stats/Trend 확장 인사이트 엔드포인트 테스트 (a,h,o,p,r,x)."""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import InterestSignal, JobCategory, Posting, PostingCategory, PostingTech, Skill


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
        java = Skill(canonical="Java", category="language")
        spring = Skill(canonical="Spring", category="framework")
        aws = Skill(canonical="AWS", category="cloud")
        seed.add_all([python, java, spring, aws])
        seed.flush()

        seed.add_all(
            [
                JobCategory(name="backend", is_tech=True),
                JobCategory(name="frontend", is_tech=True),
                JobCategory(name="sales", is_tech=False),
            ]
        )

        toss = Posting(
            source="jumpit",
            source_uid="jumpit-1",
            pool="domestic",
            company="Toss",
            title="Backend Engineer",
            industry="fintech",
            career_min=0,
            career_max=3,
            post_date=date(2024, 3, 15),
        )
        kakao = Posting(
            source="jumpit",
            source_uid="jumpit-2",
            pool="domestic",
            company="Kakao",
            title="Senior Backend Engineer",
            industry="fintech",
            career_min=3,
            career_max=6,
            post_date=date(2023, 8, 10),
        )
        naver = Posting(
            source="wanted",
            source_uid="wanted-1",
            pool="domestic",
            company="Naver",
            title="Cloud Engineer",
            industry="game",
            career_min=0,
            career_max=2,
            post_date=date(2024, 1, 5),
        )
        stripe = Posting(
            source="himalayas",
            source_uid="himalayas-1",
            pool="global",
            company="Stripe",
            title="Remote Backend Engineer",
            post_date=date(2026, 6, 1),
        )
        remote_co = Posting(
            source="wwr",
            source_uid="wwr-1",
            pool="global",
            company="RemoteCo",
            title="Backend Engineer",
            post_date=date(2023, 5, 1),
        )
        seed.add_all([toss, kakao, naver, stripe, remote_co])
        seed.commit()

        seed.add_all(
            [
                PostingCategory(posting_id=toss.id, category="backend"),
                PostingCategory(posting_id=kakao.id, category="backend"),
                PostingCategory(posting_id=naver.id, category="frontend"),
                PostingCategory(posting_id=remote_co.id, category="backend"),
                PostingTech(posting_id=toss.id, skill_id=python.id),
                PostingTech(posting_id=toss.id, skill_id=spring.id),
                PostingTech(posting_id=kakao.id, skill_id=java.id),
                PostingTech(posting_id=kakao.id, skill_id=spring.id),
                PostingTech(posting_id=naver.id, skill_id=aws.id),
                PostingTech(posting_id=stripe.id, skill_id=python.id),
                PostingTech(posting_id=remote_co.id, skill_id=python.id),
                PostingTech(posting_id=remote_co.id, skill_id=aws.id),
                InterestSignal(skill_id=python.id, source="hn", month=date(2024, 1, 1), value=100),
                InterestSignal(skill_id=python.id, source="hn", month=date(2024, 5, 1), value=50),
            ]
        )
        seed.commit()

        # SQLite stands in for the Postgres materialized view in fast tests.
        seed.execute(
            text(
                """
                CREATE TABLE mv_industry_fingerprint (
                    industry TEXT NOT NULL,
                    skill_canonical TEXT NOT NULL,
                    posting_count INTEGER NOT NULL,
                    industry_total INTEGER NOT NULL,
                    share FLOAT NOT NULL,
                    avg_share FLOAT NOT NULL
                )
                """
            )
        )
        seed.execute(
            text(
                """
                INSERT INTO mv_industry_fingerprint
                    (industry, skill_canonical, posting_count, industry_total, share, avg_share)
                VALUES
                    ('fintech', 'Python', 1, 2, 0.5, 0.5),
                    ('fintech', 'Java', 1, 2, 0.5, 0.5),
                    ('fintech', 'Spring', 2, 2, 1.0, 1.0),
                    ('game', 'AWS', 1, 1, 1.0, 1.0)
                """
            )
        )
        seed.execute(
            text(
                """
                CREATE TABLE mv_role_stack_fit (
                    pool TEXT NOT NULL,
                    category TEXT NOT NULL,
                    skill_canonical TEXT,
                    posting_count INTEGER NOT NULL,
                    category_total INTEGER NOT NULL
                )
                """
            )
        )
        seed.execute(
            text(
                """
                INSERT INTO mv_role_stack_fit
                    (pool, category, skill_canonical, posting_count, category_total)
                VALUES
                    ('domestic', 'backend', 'Python', 1, 2),
                    ('domestic', 'backend', 'Java', 1, 2),
                    ('domestic', 'backend', 'Spring', 2, 2),
                    ('domestic', 'frontend', 'AWS', 1, 1),
                    ('global', 'backend', 'Python', 1, 1),
                    ('global', 'backend', 'AWS', 1, 1)
                """
            )
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_hype_vs_hire_buckets_by_quarter(client: TestClient) -> None:
    resp = client.get("/api/v1/trend/hype-vs-hire", params={"skill": "Python"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["skill"] == "Python"
    q1_2024 = next(q for q in body["quarters"] if q["quarter"] == "2024Q1")
    assert q1_2024["interest_value"] == 100
    # remote_co(2023) + toss(2024Q1) 만 himalayas 아닌 posting 이라 posting_count 집계됨
    assert q1_2024["posting_count"] == 1


def test_hype_vs_hire_unknown_skill_returns_422(client: TestClient) -> None:
    resp = client.get("/api/v1/trend/hype-vs-hire", params={"skill": "NotARealSkill"})

    assert resp.status_code == 422


def test_newcomer_gate_computes_open_rate(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/newcomer-gate")

    assert resp.status_code == 200
    body = resp.json()
    python_item = next(item for item in body["items"] if item["canonical"] == "Python")
    # domestic Python 요구 공고: toss(career_min=0) 1건 뿐 -> open_rate 100
    assert python_item["postings"] == 1
    assert python_item["open_rate"] == 100.0


def test_global_domestic_gap_never_mixes_pools(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/global-domestic-gap")

    assert resp.status_code == 200
    body = resp.json()
    assert body["sample_size"] == {"global": 2, "domestic": 3}
    python_entry = next(
        e for e in body["global_favored"] + body["domestic_favored"] if e["canonical"] == "Python"
    )
    assert python_entry["global_n"] == 2
    assert python_entry["domestic_n"] == 1


def test_hiring_season_excludes_himalayas_and_current_year(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/hiring-season")

    assert resp.status_code == 200
    body = resp.json()
    # stripe(himalayas, 2026-06)는 제외돼야 하므로 6월 global_n에 안 잡힘
    june = next(m for m in body["months"] if m["month"] == 6)
    assert june["global_n"] == 0


def test_industry_fingerprint_scoped_to_domestic(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/industry-fingerprint")

    assert resp.status_code == 200
    body = resp.json()
    names = {entry["name"] for entry in body["industries"]}
    assert names == {"fintech", "game"}
    assert body["sample_size"] == 3
    fintech = next(entry for entry in body["industries"] if entry["name"] == "fintech")
    assert fintech["n"] == 2
    assert {item["canonical"] for item in fintech["signature"]} == {"Python", "Java", "Spring"}


def test_role_stack_fit_excludes_non_tech_categories(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/role-stack-fit")

    assert resp.status_code == 200
    body = resp.json()
    names = {c["name"] for c in body["categories"]}
    assert "sales" not in names
    assert names == {"backend", "frontend"}
    assert body["categories"] == [{"name": "backend", "n": 3}, {"name": "frontend", "n": 1}]
    assert body["matrix"] == [[100.0, 16.7], [16.7, 100.0]]
    assert body["sample_size"] == 4


def test_role_stack_fit_filters_materialized_view_by_pool(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/role-stack-fit", params={"pool": "global"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["categories"] == [{"name": "backend", "n": 1}]
    assert body["matrix"] == [[100.0]]
    assert body["sample_size"] == 1
