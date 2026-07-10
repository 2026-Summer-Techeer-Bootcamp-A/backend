"""mart.db(SQLite) → Postgres 적재(ETL). 순수 변환 함수 계층.

호스트에서 dev-compose Postgres를 대상으로 실행:
    DATABASE_URL=postgresql+psycopg://appuser:change-me@localhost:5432/appdb \
        python -m scripts.load_mart
"""

from datetime import date, datetime

DOMESTIC_SOURCES = {"wanted", "jumpit"}


def split_posting_id(posting_id: str) -> tuple[str, str]:
    """"jumpit:1268163382" → ("jumpit", "1268163382"). 첫 ':'만 분리."""
    source, sep, uid = posting_id.partition(":")
    if not sep:
        raise ValueError(f"posting_id missing ':' separator: {posting_id!r}")
    return source, uid


def derive_pool(source: str) -> str:
    return "domestic" if source in DOMESTIC_SOURCES else "global"


def region_country_for(source: str) -> str | None:
    return "KR" if source in DOMESTIC_SOURCES else None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def has_hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" for ch in text)
