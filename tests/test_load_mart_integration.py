from sqlalchemy import text

from scripts.load_mart import (
    build_category_names,
    build_cert_names,
    build_skill_rows,
    distinct_categories,
    distinct_certs,
    distinct_techs,
    ensure_schema,
    load_postings,  # noqa: E402
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


def test_load_postings_maps_fields_and_pool():
    engine = make_target()
    mart = make_mart()
    with engine.begin() as conn:
        id_map = load_postings(conn, mart)
    assert set(id_map) == {"jumpit:111", "himalayas:222"}
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT source, source_uid, pool, region_country, region_city, "
                "post_date, seniority_raw FROM posting WHERE source='jumpit'"
            )
        ).one()
    assert row.source_uid == "111"
    assert row.pool == "domestic"
    assert row.region_country == "KR"
    assert row.region_city == "서울 강남구 논현로65길22"
    assert str(row.post_date) == "2026-07-01"
    assert row.seniority_raw == "Senior"
    with engine.connect() as conn:
        glob = conn.execute(
            text("SELECT pool, region_country FROM posting WHERE source='himalayas'")
        ).one()
    assert glob.pool == "global"
    assert glob.region_country is None
