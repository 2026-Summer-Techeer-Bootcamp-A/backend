from sqlalchemy import text

from scripts.load_mart import (
    build_category_names,
    build_cert_names,
    build_skill_rows,
    distinct_categories,
    distinct_certs,
    distinct_techs,
    ensure_schema,
    seed_dicts,
    wipe,
)
from tests._mart_fixture import CERTS, TAXO, make_mart, make_target


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


def test_seed_dicts_inserts_and_returns_maps():
    engine = make_target()
    skill_rows, alias_rows = build_skill_rows(TAXO, extra_techs=["AWS"])
    cert_names = build_cert_names(CERTS, extra_certs=[])
    category_names = build_category_names(["backend", "frontend"])
    with engine.begin() as conn:
        skill_id, cert_id = seed_dicts(
            conn, skill_rows, alias_rows, cert_names, category_names
        )
    assert "Python" in skill_id and "AWS" in skill_id
    assert cert_id["정보처리기사"] > 0
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM skill_alias")).scalar() == len(
            alias_rows
        )
        assert conn.execute(text("SELECT COUNT(*) FROM job_category")).scalar() == 2
        ambiguous = conn.execute(
            text("SELECT is_ambiguous FROM skill WHERE canonical='React'")
        ).scalar()
        assert bool(ambiguous) is True
