"""GET /stats/skill-trend-yearly, /stats/hot-companies, /stats/region-density 테스트."""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_session
from app.main import app
from app.models import Posting, PostingTech, Skill


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE mv_skill_trend_yearly (
                    pool TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    canonical TEXT,
                    skill_count INTEGER NOT NULL,
                    skill_total INTEGER NOT NULL,
                    year_total INTEGER NOT NULL
                )
                """
            )
        )

    with testing_session() as seed:
        python = Skill(canonical="Python", category="language")
        java = Skill(canonical="Java", category="language")
        seed.add_all([python, java])
        seed.flush()

        # skill-trend-yearly fixtures: 2023 all python, 2024 half/half, 2025 all java.
        # pool=global so these don't leak into the domestic-scoped hot-companies/region-density years below.
        postings_2023 = [
            Posting(source="himalayas", source_uid=f"t23-{i}", pool="global", company=f"C23-{i}",
                    title="X", post_date=date(2023, 3, 1))
            for i in range(2)
        ]
        postings_2024 = [
            Posting(source="himalayas", source_uid=f"t24-{i}", pool="global", company=f"C24-{i}",
                    title="X", post_date=date(2024, 3, 1))
            for i in range(2)
        ]
        postings_2025 = [
            Posting(source="himalayas", source_uid=f"t25-{i}", pool="global", company=f"C25-{i}",
                    title="X", post_date=date(2025, 3, 1))
            for i in range(2)
        ]
        seed.add_all(postings_2023 + postings_2024 + postings_2025)
        seed.commit()

        seed.add_all(
            [
                PostingTech(posting_id=postings_2023[0].id, skill_id=python.id),
                PostingTech(posting_id=postings_2023[1].id, skill_id=python.id),
                PostingTech(posting_id=postings_2024[0].id, skill_id=python.id),
                PostingTech(posting_id=postings_2024[1].id, skill_id=java.id),
                PostingTech(posting_id=postings_2025[0].id, skill_id=java.id),
                PostingTech(posting_id=postings_2025[1].id, skill_id=java.id),
            ]
        )

        # hot-companies fixtures: as_of = 2026-07-10 (max post_date domestic)
        hot_a = [
            Posting(source="jumpit", source_uid=f"hot-a-{i}", pool="domestic", company="HotA",
                    title="X", post_date=date(2026, 7, 5 + i))
            for i in range(3)
        ]
        hot_b = Posting(source="jumpit", source_uid="hot-b", pool="domestic", company="HotB",
                         title="X", post_date=date(2026, 7, 10))
        hot_c_old = Posting(source="jumpit", source_uid="hot-c", pool="domestic", company="HotC",
                             title="X", post_date=date(2025, 1, 1))
        seed.add_all(hot_a + [hot_b, hot_c_old])
        seed.commit()

        # region-density fixtures
        gangnam = [
            Posting(source="jumpit", source_uid=f"gn-{i}", pool="domestic", company=f"GN{i}",
                    title="X", post_date=date(2026, 7, 1), region_district="강남구")
            for i in range(3)
        ]
        mapo = Posting(source="jumpit", source_uid="mp-1", pool="domestic", company="MP",
                        title="X", post_date=date(2026, 7, 1), region_district="마포구")
        # post_date=None so this doesn't leak an extra year into the global-pool skill-trend-yearly fixture above.
        global_district = Posting(source="wwr", source_uid="gd-1", pool="global", company="GD",
                                   title="X", post_date=None, region_district="홍대")
        seed.add_all(gangnam + [mapo, global_district])
        seed.execute(
            text(
                """
                INSERT INTO mv_skill_trend_yearly
                    (pool, year, canonical, skill_count, skill_total, year_total)
                VALUES
                    ('global', 2023, 'Python', 2, 3, 2),
                    ('global', 2024, 'Java', 1, 3, 2),
                    ('global', 2024, 'Python', 1, 3, 2),
                    ('global', 2025, 'Java', 2, 3, 2)
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


# ---- skill-trend-yearly ----


def test_skill_trend_yearly_requires_pool(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/skill-trend-yearly")
    assert resp.status_code == 422


def test_skill_trend_yearly_years_and_shares(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/skill-trend-yearly", params={"pool": "global", "top_k": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["years"] == [2023, 2024, 2025]
    python_series = next(s for s in body["series"] if s["canonical"] == "Python")
    assert python_series["shares"] == [100.0, 50.0, 0.0]
    java_series = next(s for s in body["series"] if s["canonical"] == "Java")
    assert java_series["shares"] == [0.0, 50.0, 100.0]


def test_skill_trend_yearly_movers_rising_falling(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/skill-trend-yearly", params={"pool": "global", "top_k": 5})
    body = resp.json()
    rising_names = [m["canonical"] for m in body["movers"]["rising"]]
    falling_names = [m["canonical"] for m in body["movers"]["falling"]]
    assert "Java" in rising_names
    assert "Python" in falling_names


# ---- hot-companies ----


def test_hot_companies_requires_pool(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/hot-companies")
    assert resp.status_code == 422


def test_hot_companies_within_window(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/hot-companies", params={"pool": "domestic", "days": 30, "limit": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] == "2026-07-10"
    companies = {item["company"]: item["posting_count"] for item in body["items"]}
    assert companies["HotA"] == 3
    assert companies["HotB"] == 1
    assert "HotC" not in companies  # outside 30-day window


# ---- region-density ----


def test_region_density_defaults_to_domestic(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/region-density")
    assert resp.status_code == 200
    body = resp.json()
    items = {item["region_district"]: item["posting_count"] for item in body["items"]}
    assert items["강남구"] == 3
    assert items["마포구"] == 1
    assert "홍대" not in items  # global pool excluded by default


def test_region_density_respects_limit(client: TestClient) -> None:
    resp = client.get("/api/v1/stats/region-density", params={"pool": "domestic", "limit": 1})
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["region_district"] == "강남구"
