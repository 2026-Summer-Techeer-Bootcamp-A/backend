import inspect

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.crud.insight import get_skill_trend_yearly
from app.main import lifespan
from app.routers.admin import run_collector_job


def _create_seeded_mv() -> Session:
    engine = create_engine("sqlite://")
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
        conn.execute(
            text(
                """
                INSERT INTO mv_skill_trend_yearly
                    (pool, year, canonical, skill_count, skill_total, year_total)
                VALUES
                    ('global', 2023, 'Python', 2, 3, 2),
                    ('global', 2024, 'Python', 1, 3, 2),
                    ('global', 2024, 'Java', 1, 3, 2),
                    ('global', 2025, 'Java', 2, 3, 2),
                    ('global', 2025, 'Rust', 1, 1, 2),
                    ('domestic', 2025, 'Spring', 1, 1, 1)
                """
            )
        )
    return Session(engine)


def test_skill_trend_yearly_reads_precomputed_mv() -> None:
    with _create_seeded_mv() as session:
        result = get_skill_trend_yearly(session, pool="global", top_k=2)

    assert result["years"] == [2023, 2024, 2025]
    assert result["sample_size"] == 6
    series = {item["canonical"]: item for item in result["series"]}
    assert series["Python"] == {"canonical": "Python", "shares": [100.0, 50.0, 0.0], "delta": -100.0}
    assert series["Java"] == {"canonical": "Java", "shares": [0.0, 50.0, 100.0], "delta": 100.0}
    assert result["movers"]["rising"] == [{"canonical": "Java", "delta": 100.0}]
    assert result["movers"]["falling"] == [{"canonical": "Python", "delta": -100.0}]


def test_skill_trend_yearly_mv_keeps_year_without_skills_in_sample() -> None:
    with _create_seeded_mv() as session:
        session.execute(
            text(
                """
                INSERT INTO mv_skill_trend_yearly
                    (pool, year, canonical, skill_count, skill_total, year_total)
                VALUES ('global', 2022, NULL, 0, 0, 4)
                """
            )
        )
        session.commit()
        result = get_skill_trend_yearly(session, pool="global", top_k=2)

    assert result["years"] == [2022, 2023, 2024, 2025]
    assert result["sample_size"] == 10
    assert all(len(item["shares"]) == 4 for item in result["series"])


def test_skill_trend_yearly_mv_keeps_pools_separate() -> None:
    with _create_seeded_mv() as session:
        result = get_skill_trend_yearly(session, pool="domestic", top_k=5)

    assert result["years"] == [2025]
    assert result["series"] == [{"canonical": "Spring", "shares": [100.0], "delta": 0.0}]
    assert result["sample_size"] == 1


def test_skill_trend_yearly_mv_is_created_at_app_startup() -> None:
    source = inspect.getsource(lifespan)

    assert "CREATE MATERIALIZED VIEW IF NOT EXISTS mv_skill_trend_yearly" in source


def test_skill_trend_yearly_mv_is_refreshed_after_collector_job() -> None:
    source = inspect.getsource(run_collector_job)

    assert "REFRESH MATERIALIZED VIEW mv_skill_trend_yearly;" in source
