from sqlalchemy import text

from scripts.load_mart import (
    distinct_categories,
    distinct_certs,
    distinct_techs,
    ensure_schema,
    wipe,
)
from tests._mart_fixture import make_mart, make_target


def test_distinct_extractors():
    mart = make_mart()
    assert sorted(distinct_techs(mart)) == ["AWS", "Python"]
    assert distinct_certs(mart) == ["정보처리기사"]
    assert distinct_categories(mart) == ["backend"]


def test_ensure_schema_idempotent_and_wipe():
    engine = make_target()
    ensure_schema(engine)  # 재실행해도 예외 없어야 함
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO job_category (name, is_tech) VALUES ('x', 0)"))
    with engine.begin() as conn:
        wipe(conn)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM job_category")).scalar() == 0
