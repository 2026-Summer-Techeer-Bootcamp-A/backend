import inspect

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.crud.insight import get_global_domestic_gap
from app.main import lifespan
from app.routers.admin import run_collector_job


def test_global_domestic_gap_reads_precomputed_mv() -> None:
    engine = create_engine("sqlite://")

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
        conn.execute(
            text(
                """
                INSERT INTO mv_global_domestic_gap (
                    skill_id, canonical, category,
                    global_n, domestic_n,
                    global_pct, domestic_pct, diff,
                    global_total, domestic_total
                ) VALUES
                    (1, 'Python', 'language', 40, 4, 40.0, 20.0, 20.0, 100, 20),
                    (2, 'Spring', 'framework', 5, 10, 5.0, 50.0, -45.0, 100, 20),
                    (3, 'Rust', 'language', 10, 0, 10.0, 0.0, 10.0, 100, 20)
                """
            )
        )

    with Session(engine) as session:
        global_favored, domestic_favored, global_total, domestic_total = get_global_domestic_gap(
            session, limit=1
        )

    assert global_favored == [
        {
            "canonical": "Python",
            "category": "language",
            "global_pct": 40.0,
            "domestic_pct": 20.0,
            "diff": 20.0,
            "global_n": 40,
            "domestic_n": 4,
        }
    ]
    assert domestic_favored == [
        {
            "canonical": "Spring",
            "category": "framework",
            "global_pct": 5.0,
            "domestic_pct": 50.0,
            "diff": -45.0,
            "global_n": 5,
            "domestic_n": 10,
        }
    ]
    assert global_total == 100
    assert domestic_total == 20


def test_global_domestic_gap_mv_is_created_at_app_startup() -> None:
    source = inspect.getsource(lifespan)

    assert "CREATE MATERIALIZED VIEW IF NOT EXISTS mv_global_domestic_gap" in source


def test_global_domestic_gap_mv_is_refreshed_after_collector_job() -> None:
    source = inspect.getsource(run_collector_job)

    assert "REFRESH MATERIALIZED VIEW mv_global_domestic_gap;" in source
