from datetime import date, datetime

from scripts.load_mart import (
    DOMESTIC_SOURCES,
    derive_pool,
    has_hangul,
    parse_date,
    parse_datetime,
    region_country_for,
    split_posting_id,
)


def test_split_posting_id_splits_on_first_colon():
    assert split_posting_id("jumpit:1268163382") == ("jumpit", "1268163382")


def test_split_posting_id_keeps_extra_colons_in_uid():
    assert split_posting_id("hn:a:b") == ("hn", "a:b")


def test_split_posting_id_without_colon_raises():
    import pytest

    with pytest.raises(ValueError):
        split_posting_id("nocolon")


def test_derive_pool_domestic_and_global():
    assert derive_pool("jumpit") == "domestic"
    assert derive_pool("wanted") == "domestic"
    assert derive_pool("himalayas") == "global"
    assert DOMESTIC_SOURCES == {"wanted", "jumpit"}


def test_region_country_for():
    assert region_country_for("jumpit") == "KR"
    assert region_country_for("hn") is None


def test_parse_date_and_none_and_bad():
    assert parse_date("2026-07-01") == date(2026, 7, 1)
    assert parse_date(None) is None
    assert parse_date("") is None
    assert parse_date("not-a-date") is None


def test_parse_datetime_iso_and_none():
    assert parse_datetime("2026-07-01T09:00:00+00:00") == datetime.fromisoformat(
        "2026-07-01T09:00:00+00:00"
    )
    assert parse_datetime(None) is None


def test_has_hangul():
    assert has_hangul("서울 강남구") is True
    assert has_hangul("Remote") is False


from tests._mart_fixture import CERTS, TAXO  # noqa: E402
from scripts.load_mart import (  # noqa: E402
    build_category_names,
    build_cert_names,
    build_skill_rows,
)


def test_build_skill_rows_categories_and_ambiguous():
    skills, aliases = build_skill_rows(TAXO, extra_techs=["Python", "Rust"])
    by_canon = {s["canonical"]: s for s in skills}
    # 실 카테고리
    assert by_canon["Python"]["category"] == "language"
    assert by_canon["Python"]["is_ambiguous"] is False
    # 실 카테고리 + ambiguous 양쪽 → 카테고리 유지, 플래그 True
    assert by_canon["React"]["category"] == "frontend"
    assert by_canon["React"]["is_ambiguous"] is True
    # ambiguous 전용
    assert by_canon["Go"]["category"] == "ambiguous"
    assert by_canon["Go"]["is_ambiguous"] is True
    # mart에만 있는 tech → uncategorized
    assert by_canon["Rust"]["category"] == "uncategorized"
    # 이미 있는 Python은 extra_techs로 중복 생성되지 않음
    assert len([s for s in skills if s["canonical"] == "Python"]) == 1


def test_build_skill_rows_alias_dedup_and_korean():
    _skills, aliases = build_skill_rows(TAXO, extra_techs=[])
    keys = [a["alias"].lower() for a in aliases]
    assert len(keys) == len(set(keys))  # 전역 dedup
    kor = {a["alias"]: a["is_korean"] for a in aliases}
    assert kor["파이썬"] is True
    assert kor["python"] is False


def test_build_cert_names_union():
    names = build_cert_names(CERTS, extra_certs=["정보처리기사", "AWS SAA"])
    assert "정보처리기사" in names
    assert "AWS SAA" in names
    assert names.count("정보처리기사") == 1  # 중복 제거


def test_build_category_names_dedup_preserves_order():
    assert build_category_names(["backend", "frontend", "backend"]) == [
        "backend",
        "frontend",
    ]
