"""Stats/Trend 확장 인사이트 엔드포인트 테스트 (a,h,o,p,r,x)."""

import json
from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import InterestSignal, JobCategory, Posting, PostingCategory, PostingTech, Skill
from app.routers import insight as insight_router
from app.schemas.insight import HiringSeasonResponse, NewcomerGateResponse


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value
        self.setex_calls.append((key, ttl, value))


class BrokenRedis:
    def get(self, key: str) -> str | None:
        raise RedisError("redis unavailable")

    def setex(self, key: str, ttl: int, value: str) -> None:
        raise RedisError("redis unavailable")


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(insight_router, "redis_client", fake, raising=False)
    return fake


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sql_statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        sql_statements.append(statement)

    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE mv_global_domestic_gap (
                    skill_id INTEGER,
                    canonical TEXT,
                    category TEXT,
                    global_n INTEGER,
                    domestic_n INTEGER,
                    global_pct FLOAT,
                    domestic_pct FLOAT,
                    diff FLOAT,
                    global_total INTEGER,
                    domestic_total INTEGER
                )
                """
            )
        )

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

        seed.execute(
            text(
                """
                INSERT INTO mv_global_domestic_gap (
                    skill_id, canonical, category,
                    global_n, domestic_n,
                    global_pct, domestic_pct, diff,
                    global_total, domestic_total
                ) VALUES
                    (:python_id, 'Python', 'language', 2, 1, 100.0, 33.33, 66.67, 2, 3),
                    (:java_id, 'Java', 'language', 0, 1, 0.0, 33.33, -33.33, 2, 3),
                    (:spring_id, 'Spring', 'framework', 0, 2, 0.0, 66.67, -66.67, 2, 3),
                    (:aws_id, 'AWS', 'cloud', 1, 1, 50.0, 33.33, 16.67, 2, 3)
                """
            ),
            {
                "python_id": python.id,
                "java_id": java.id,
                "spring_id": spring.id,
                "aws_id": aws.id,
            },
        )
        seed.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    test_client = TestClient(app)
    test_client.sql_statements = sql_statements
    yield test_client
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


def test_hiring_season_excludes_himalayas_and_current_year(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    resp = client.get("/api/v1/stats/hiring-season")

    assert resp.status_code == 200
    body = resp.json()
    assert body["sample_size"] == {"global": 1, "domestic": 3}
    assert body["months"] == [
        {"month": 1, "global_idx": 0.0, "domestic_idx": 4.0, "global_n": 0, "domestic_n": 1},
        {"month": 2, "global_idx": 0.0, "domestic_idx": 0.0, "global_n": 0, "domestic_n": 0},
        {"month": 3, "global_idx": 0.0, "domestic_idx": 4.0, "global_n": 0, "domestic_n": 1},
        {"month": 4, "global_idx": 0.0, "domestic_idx": 0.0, "global_n": 0, "domestic_n": 0},
        {"month": 5, "global_idx": 12.0, "domestic_idx": 0.0, "global_n": 1, "domestic_n": 0},
        {"month": 6, "global_idx": 0.0, "domestic_idx": 0.0, "global_n": 0, "domestic_n": 0},
        {"month": 7, "global_idx": 0.0, "domestic_idx": 0.0, "global_n": 0, "domestic_n": 0},
        {"month": 8, "global_idx": 0.0, "domestic_idx": 4.0, "global_n": 0, "domestic_n": 1},
        {"month": 9, "global_idx": 0.0, "domestic_idx": 0.0, "global_n": 0, "domestic_n": 0},
        {"month": 10, "global_idx": 0.0, "domestic_idx": 0.0, "global_n": 0, "domestic_n": 0},
        {"month": 11, "global_idx": 0.0, "domestic_idx": 0.0, "global_n": 0, "domestic_n": 0},
        {"month": 12, "global_idx": 0.0, "domestic_idx": 0.0, "global_n": 0, "domestic_n": 0},
    ]


def test_hiring_season_uses_database_group_by(client: TestClient, fake_redis: FakeRedis) -> None:
    resp = client.get("/api/v1/stats/hiring-season")

    assert resp.status_code == 200
    posting_selects = [
        statement
        for statement in client.sql_statements
        if statement.lstrip().upper().startswith("SELECT") and "FROM posting" in statement
    ]
    assert any("GROUP BY" in statement.upper() for statement in posting_selects)


def test_hiring_season_cache_miss_sets_six_hour_ttl(client: TestClient, fake_redis: FakeRedis) -> None:
    resp = client.get("/api/v1/stats/hiring-season")

    assert resp.status_code == 200
    assert len(fake_redis.setex_calls) == 1
    key, ttl, cached_json = fake_redis.setex_calls[0]
    assert key == "stats:hiring-season:v1"
    assert ttl == 21_600
    assert HiringSeasonResponse.model_validate_json(cached_json).model_dump(mode="json") == resp.json()


def test_hiring_season_cache_hit_skips_database(
    client: TestClient,
    fake_redis: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached_payload = {
        "months": [
            {
                "month": month,
                "global_idx": 1.0,
                "domestic_idx": 1.0,
                "global_n": 7,
                "domestic_n": 9,
            }
            for month in range(1, 13)
        ],
        "as_of": "2026-07-14",
        "sample_size": {"global": 84, "domestic": 108},
        "note": "cached response",
    }
    fake_redis.store["stats:hiring-season:v1"] = json.dumps(cached_payload)

    def fail_if_called(*, session: Session) -> tuple[list[dict], dict[str, int]]:
        raise AssertionError("database must not be queried on cache hit")

    monkeypatch.setattr(insight_router, "get_hiring_season", fail_if_called)

    resp = client.get("/api/v1/stats/hiring-season")

    assert resp.status_code == 200
    assert resp.json() == cached_payload


def test_hiring_season_redis_failure_falls_back_to_database(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(insight_router, "redis_client", BrokenRedis(), raising=False)

    resp = client.get("/api/v1/stats/hiring-season")

    assert resp.status_code == 200
    assert resp.json()["sample_size"] == {"global": 1, "domestic": 3}


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


def test_newcomer_gate_cache_miss_sets_six_hour_ttl(client: TestClient, fake_redis: FakeRedis) -> None:
    resp = client.get("/api/v1/stats/newcomer-gate", params={"limit": 15})

    assert resp.status_code == 200
    assert len(fake_redis.setex_calls) == 1
    key, ttl, cached_json = fake_redis.setex_calls[0]
    assert key == "stats:newcomer-gate:v1:15"
    assert ttl == 21_600
    assert NewcomerGateResponse.model_validate_json(cached_json).model_dump(mode="json") == resp.json()


def test_newcomer_gate_cache_hit_skips_database(
    client: TestClient,
    fake_redis: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached_payload = {
        "items": [
            {
                "canonical": "Python",
                "postings": 10,
                "newcomer_postings": 5,
                "open_rate": 50.0,
            }
        ],
        "pool": "domestic",
        "as_of": "2026-07-14",
        "sample_size": 10,
        "sample_warning": True,
        "note": "cached response",
    }
    fake_redis.store["stats:newcomer-gate:v1:15"] = json.dumps(cached_payload)

    def fail_if_called(*, session: Session, limit: int) -> tuple[list, int]:
        raise AssertionError("database must not be queried on cache hit")

    monkeypatch.setattr(insight_router, "get_newcomer_gate", fail_if_called)

    resp = client.get("/api/v1/stats/newcomer-gate", params={"limit": 15})

    assert resp.status_code == 200
    assert resp.json() == cached_payload


def test_newcomer_gate_redis_failure_falls_back_to_database(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(insight_router, "redis_client", BrokenRedis(), raising=False)

    resp = client.get("/api/v1/stats/newcomer-gate", params={"limit": 15})

    assert resp.status_code == 200
    body = resp.json()
    python_item = next(item for item in body["items"] if item["canonical"] == "Python")
    assert python_item["postings"] == 1
    assert python_item["open_rate"] == 100.0
