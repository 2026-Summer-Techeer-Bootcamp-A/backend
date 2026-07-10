"""mart.db(SQLite) → Postgres 적재(ETL). 순수 변환 함수 계층.

호스트에서 dev-compose Postgres를 대상으로 실행:
    DATABASE_URL=postgresql+psycopg://appuser:change-me@localhost:5432/appdb \
        python -m scripts.load_mart
"""

import sqlite3
from datetime import date, datetime

from sqlalchemy import select, text

from app.core.db import Base
from app.models import (
    Cert,
    JobCategory,
    Posting,
    PostingCategory,
    PostingCert,
    PostingTech,
    RawPosting,
    Skill,
    SkillAlias,
)

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


from collections.abc import Iterable

AMBIGUOUS_KEY = "_ambiguous_llm_fallback"


def build_skill_rows(
    taxonomy: dict, extra_techs: Iterable[str]
) -> tuple[list[dict], list[dict]]:
    skills: dict[str, dict] = {}
    aliases: list[dict] = []
    seen_alias: set[str] = set()

    def add_aliases(canonical: str, alias_list) -> None:
        if not isinstance(alias_list, list):
            return
        for a in alias_list:
            key = a.lower()
            if key in seen_alias:
                continue
            seen_alias.add(key)
            aliases.append(
                {"canonical": canonical, "alias": a, "is_korean": has_hangul(a)}
            )

    for category, entries in taxonomy.items():
        if category.startswith("_") or not isinstance(entries, dict):
            continue
        for canonical, alias_list in entries.items():
            if canonical not in skills:
                skills[canonical] = {
                    "canonical": canonical,
                    "category": category,
                    "is_ambiguous": False,
                }
            add_aliases(canonical, alias_list)

    for canonical, alias_list in taxonomy.get(AMBIGUOUS_KEY, {}).items():
        if canonical.startswith("_") or not isinstance(alias_list, list):
            continue
        if canonical in skills:
            skills[canonical]["is_ambiguous"] = True
        else:
            skills[canonical] = {
                "canonical": canonical,
                "category": "ambiguous",
                "is_ambiguous": True,
            }
        add_aliases(canonical, alias_list)

    for tech in extra_techs:
        if tech and tech not in skills:
            skills[tech] = {
                "canonical": tech,
                "category": "uncategorized",
                "is_ambiguous": False,
            }

    return list(skills.values()), aliases


def build_cert_names(cert_taxonomy: dict, extra_certs: Iterable[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for category, entries in cert_taxonomy.items():
        if category.startswith("_") or not isinstance(entries, dict):
            continue
        for name in entries:
            if name not in seen:
                seen.add(name)
                names.append(name)
    for name in extra_certs:
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def build_category_names(mart_categories: Iterable[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for name in mart_categories:
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def open_mart(path: str) -> sqlite3.Connection:
    """mart.db 파일을 열고 Row 팩토리를 설정해 반환."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(engine) -> None:
    """대상 DB에 전체 ORM 스키마를 생성(idempotent)."""
    Base.metadata.create_all(bind=engine)


def wipe(conn) -> None:
    """대상 DB의 모든 애플리케이션 테이블을 비운다(스키마 유지)."""
    if conn.dialect.name == "postgresql":
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    else:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


def _insert_chunked(conn, table, rows: list[dict], size: int = 5000) -> None:
    """행 리스트를 size별로 청크로 나눠 테이블에 삽입."""
    for i in range(0, len(rows), size):
        conn.execute(table.insert(), rows[i : i + size])


def distinct_techs(mart: sqlite3.Connection) -> list[str]:
    """mart에서 모든 고유한 기술 목록을 추출."""
    return [r[0] for r in mart.execute("SELECT DISTINCT tech FROM fact_posting_tech")]


def distinct_certs(mart: sqlite3.Connection) -> list[str]:
    """mart에서 모든 고유한 자격증 목록을 추출."""
    return [r[0] for r in mart.execute("SELECT DISTINCT cert FROM fact_posting_cert")]


def distinct_categories(mart: sqlite3.Connection) -> list[str]:
    """mart에서 모든 고유한 카테고리 목록을 추출."""
    return [
        r[0] for r in mart.execute("SELECT DISTINCT category FROM fact_posting_category")
    ]


def seed_dicts(
    conn,
    skill_rows: list[dict],
    alias_rows: list[dict],
    cert_names: list[str],
    category_names: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    _insert_chunked(conn, Skill.__table__, skill_rows)
    skill_id = {
        row.canonical: row.id
        for row in conn.execute(select(Skill.id, Skill.canonical))
    }
    alias_params = [
        {
            "skill_id": skill_id[a["canonical"]],
            "alias": a["alias"],
            "is_korean": a["is_korean"],
        }
        for a in alias_rows
        if a["canonical"] in skill_id
    ]
    _insert_chunked(conn, SkillAlias.__table__, alias_params)

    _insert_chunked(conn, Cert.__table__, [{"name": n} for n in cert_names])
    cert_id = {row.name: row.id for row in conn.execute(select(Cert.id, Cert.name))}

    _insert_chunked(
        conn,
        JobCategory.__table__,
        [{"name": n, "is_tech": False} for n in category_names],
    )
    return skill_id, cert_id
