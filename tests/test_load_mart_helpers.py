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
