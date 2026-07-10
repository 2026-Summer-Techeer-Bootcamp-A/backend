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
