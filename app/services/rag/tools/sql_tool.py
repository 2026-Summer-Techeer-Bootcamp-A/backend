"""sql_tool — 정량·랭킹·커버리지 질문에 결정론적 집계로 답한다(할루시네이션 0).

모든 쿼리는 파라미터화. pool(domestic|global) 필터와 소프트삭제(is_deleted) 제외.
"""

from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.rag.tools.common import norm_pool, resolve_skill

_POOL_WHERE = (
    "(CAST(:pool AS text) IS NULL OR p.pool = CAST(:pool AS text)) "
    "AND p.is_deleted = false"
)


def _category_join(category: str | None) -> str:
    """category가 있으면 posting_category JOIN 조각을(없으면 빈 문자열) 반환.

    주의: posting_category는 공고 1건당 여러 행을 가질 수 있어(실측 평균 1.87건,
    최대 9건) 이 JOIN을 쓰는 집계 쿼리는 COUNT(*) 대신 COUNT(DISTINCT ...)를 써야
    카운트가 부풀지 않는다.
    """
    if not category:
        return ""
    return (
        "JOIN posting_category cat ON cat.posting_id = p.id "
        "AND cat.is_deleted = false AND cat.category ILIKE :category_pattern "
    )


def _entry_level_where(entry_level: bool) -> str:
    return " AND p.career_min = 0" if entry_level else ""


def _filter_params(category: str | None) -> dict[str, str]:
    return {"category_pattern": f"%{category}%"} if category else {}


def _filter_label_suffix(category: str | None, entry_level: bool) -> str:
    """예: " (백엔드 · 신입)". 필터가 없으면 빈 문자열."""
    parts = [x for x in (category, "신입" if entry_level else None) if x]
    return f" ({' · '.join(parts)})" if parts else ""


def _filter_citation_suffix(category: str | None, entry_level: bool) -> str:
    return (f" · 직군={category}" if category else "") + (" · 신입" if entry_level else "")


def total_postings(
    session: Session,
    pool: str | None,
    category: str | None = None,
    entry_level: bool = False,
) -> int:
    pool = norm_pool(pool)
    if not category and not entry_level:
        return int(
            session.execute(
                text(f"SELECT COUNT(*) FROM posting p WHERE {_POOL_WHERE}"), {"pool": pool}
            ).scalar()
            or 0
        )
    join = _category_join(category)
    where_extra = _entry_level_where(entry_level)
    count_expr = "COUNT(DISTINCT p.id)" if category else "COUNT(*)"
    params: dict[str, object] = {"pool": pool}
    params.update(_filter_params(category))
    return int(
        session.execute(
            text(f"SELECT {count_expr} FROM posting p {join}WHERE {_POOL_WHERE}{where_extra}"),
            params,
        ).scalar()
        or 0
    )


def _top(
    session: Session,
    sql: str,
    pool: str | None,
    limit: int,
    extra_params: dict[str, object] | None = None,
) -> list[tuple[str, int]]:
    params: dict[str, object] = {"pool": norm_pool(pool), "limit": limit}
    if extra_params:
        params.update(extra_params)
    rows = session.execute(text(sql), params).all()
    return [(r[0], int(r[1])) for r in rows]


def top_skills(
    session: Session,
    pool: str | None = None,
    limit: int = 8,
    category: str | None = None,
    entry_level: bool = False,
    verbose: bool = False,
) -> dict:
    total = total_postings(session, pool, category=category, entry_level=entry_level)
    join = _category_join(category)
    where_extra = _entry_level_where(entry_level)
    count_expr = "COUNT(DISTINCT pt.posting_id)" if category else "COUNT(*)"
    extra_params = _filter_params(category)
    sql = (
        f"SELECT s.canonical, {count_expr} n FROM posting_tech pt "
        f"JOIN skill s ON s.id = pt.skill_id "
        f"JOIN posting p ON p.id = pt.posting_id "
        f"{join}"
        f"WHERE {_POOL_WHERE} AND pt.is_deleted = false{where_extra} "
        f"GROUP BY s.canonical ORDER BY n DESC LIMIT :limit"
    )
    sql_start = time.perf_counter()
    rows = _top(session, sql, pool, limit, extra_params=extra_params)
    sql_ms = round((time.perf_counter() - sql_start) * 1000, 1)
    items = [
        {"name": n, "metric": f"{c:,}건", "pct": round(100 * c / total, 1) if total else 0.0}
        for n, c in rows
    ]
    facts_body = "; ".join(
        f"{n} {c}건({round(100 * c / total, 1) if total else 0}%)" for n, c in rows
    )
    if category or entry_level:
        label = f"수요 상위 기술{_filter_label_suffix(category, entry_level)}"
        citation_label = (
            f"기술태그 집계 · 공고 {total:,}건{_filter_citation_suffix(category, entry_level)}"
        )
        filter_desc = []
        if category:
            filter_desc.append(f"{category} 직군")
        if entry_level:
            filter_desc.append("신입")
        scope_str = f" ({', '.join(filter_desc)})" if filter_desc else ""
        facts = f"전체 채용 공고{scope_str} 총 {total:,}건 기준 수요 상위 기술: {facts_body}"
    debug = (
        {"sql": sql, "params": {"pool": norm_pool(pool), "limit": limit, **extra_params}, "sql_ms": sql_ms}
        if verbose
        else None
    )
    return {
        "tool": "sql",
        "tool_result": {"kind": "list", "label": label, "items": items, "debug": debug},
        "citation": {
            "type": "sql",
            "ref": "채용공고·기술 태그",
            "label": citation_label,
        },
        "n": total,
        "facts": facts,
    }


def top_concepts(
    session: Session, pool: str | None = None, limit: int = 8, verbose: bool = False
) -> dict:
    total = total_postings(session, pool)
    sql = (
        f"SELECT c.name, COUNT(*) n FROM posting_concept pc "
        f"JOIN concept c ON c.id = pc.concept_id "
        f"JOIN posting p ON p.id = pc.posting_id "
        f"WHERE {_POOL_WHERE} AND pc.is_deleted = false "
        f"GROUP BY c.name ORDER BY n DESC LIMIT :limit"
    )
    sql_start = time.perf_counter()
    rows = _top(session, sql, pool, limit)
    sql_ms = round((time.perf_counter() - sql_start) * 1000, 1)
    items = [
        {"name": n, "metric": f"{c:,}건", "pct": round(100 * c / total, 1) if total else 0.0}
        for n, c in rows
    ]
    facts = "; ".join(f"{n} {c}건" for n, c in rows)
    debug = (
        {"sql": sql, "params": {"pool": norm_pool(pool), "limit": limit}, "sql_ms": sql_ms}
        if verbose
        else None
    )
    return {
        "tool": "sql",
        "tool_result": {"kind": "list", "label": "빈출 개념·패러다임", "items": items, "debug": debug},
        "citation": {
            "type": "sql",
            "ref": "채용공고·개념",
            "label": f"개념 집계 · 공고 {total:,}건",
        },
        "n": total,
        "facts": f"pool={pool or '전체'} 상위 개념 — {facts}",
    }


def top_certs(
    session: Session,
    pool: str | None = None,
    limit: int = 8,
    category: str | None = None,
    entry_level: bool = False,
    verbose: bool = False,
) -> dict:
    total = total_postings(session, pool, category=category, entry_level=entry_level)
    join = _category_join(category)
    where_extra = _entry_level_where(entry_level)
    count_expr = "COUNT(DISTINCT pc.posting_id)" if category else "COUNT(*)"
    extra_params = _filter_params(category)
    sql = (
        f"SELECT ct.name, {count_expr} n FROM posting_cert pc "
        f"JOIN cert ct ON ct.id = pc.cert_id "
        f"JOIN posting p ON p.id = pc.posting_id "
        f"{join}"
        f"WHERE {_POOL_WHERE} AND pc.is_deleted = false{where_extra} "
        f"GROUP BY ct.name ORDER BY n DESC LIMIT :limit"
    )
    sql_start = time.perf_counter()
    rows = _top(session, sql, pool, limit, extra_params=extra_params)
    sql_ms = round((time.perf_counter() - sql_start) * 1000, 1)
    items = [
        {"name": n, "metric": f"{c:,}건", "pct": round(100 * c / total, 1) if total else 0.0}
        for n, c in rows
    ]
    facts_body = "; ".join(f"{n} {c}건" for n, c in rows)
    if category or entry_level:
        label = f"요구 상위 자격증{_filter_label_suffix(category, entry_level)}"
        citation_label = (
            f"자격증 요구 집계 · 공고 {total:,}건{_filter_citation_suffix(category, entry_level)}"
        )
        facts = (
            f"pool={pool or '전체'} 직군={category or '전체'} "
            f"신입={'예' if entry_level else '무관'} 총 {total:,}건 기준 상위 자격증 — {facts_body}"
        )
    else:
        label = "요구 상위 자격증"
        citation_label = f"자격증 요구 집계 · 공고 {total:,}건"
        facts = f"pool={pool or '전체'} 총 {total:,}건 기준 상위 자격증 — {facts_body}"
    debug = (
        {"sql": sql, "params": {"pool": norm_pool(pool), "limit": limit, **extra_params}, "sql_ms": sql_ms}
        if verbose
        else None
    )
    return {
        "tool": "sql",
        "tool_result": {"kind": "list", "label": label, "items": items, "debug": debug},
        "citation": {
            "type": "sql",
            "ref": "채용공고·자격증",
            "label": citation_label,
        },
        "n": total,
        "facts": facts,
    }


def top_locations(
    session: Session,
    pool: str | None = None,
    limit: int = 8,
    category: str | None = None,
    verbose: bool = False,
) -> dict:
    total = total_postings(session, pool, category=category)
    join = _category_join(category)
    count_expr = "COUNT(DISTINCT p.id)" if category else "COUNT(*)"
    extra_params = _filter_params(category)
    sql = (
        f"SELECT p.region_district, {count_expr} n FROM posting p "
        f"{join}"
        f"WHERE {_POOL_WHERE} AND p.region_district IS NOT NULL "
        f"GROUP BY p.region_district ORDER BY n DESC LIMIT :limit"
    )
    sql_start = time.perf_counter()
    rows = _top(session, sql, pool, limit, extra_params=extra_params)
    sql_ms = round((time.perf_counter() - sql_start) * 1000, 1)
    items = [
        {"name": n, "metric": f"{c:,}건", "pct": round(100 * c / total, 1) if total else 0.0}
        for n, c in rows
    ]
    facts_body = "; ".join(f"{n} {c}건({round(100 * c / total, 1) if total else 0}%)" for n, c in rows)
    if category:
        label = f"지역별 공고 분포{_filter_label_suffix(category, False)}"
        citation_label = f"지역별 집계 · 공고 {total:,}건{_filter_citation_suffix(category, False)}"
        facts = (
            f"pool={pool or '전체'} 직군={category} 기준(지역 정보는 국내 공고에만 있음) "
            f"지역별 공고 분포 — {facts_body}"
        )
    else:
        label = "지역별 공고 분포"
        citation_label = f"지역별 집계 · 공고 {total:,}건"
        facts = (
            f"pool={pool or '전체'} 기준(지역 정보는 국내 공고에만 있음) "
            f"지역별 공고 분포 — {facts_body}"
        )
    debug = (
        {"sql": sql, "params": {"pool": norm_pool(pool), "limit": limit, **extra_params}, "sql_ms": sql_ms}
        if verbose
        else None
    )
    return {
        "tool": "sql",
        "tool_result": {"kind": "list", "label": label, "items": items, "debug": debug},
        "citation": {
            "type": "sql",
            "ref": "채용공고·지역",
            "label": citation_label,
        },
        "n": total,
        "facts": facts,
    }


def skill_demand(
    session: Session,
    skill_name: str,
    pool: str | None = None,
    category: str | None = None,
    entry_level: bool = False,
    verbose: bool = False,
) -> dict | None:
    resolved = resolve_skill(session, skill_name)
    if not resolved:
        return None
    skill_id, canonical = resolved
    total = total_postings(session, pool, category=category, entry_level=entry_level)
    join = _category_join(category)
    where_extra = _entry_level_where(entry_level)
    params: dict[str, object] = {"sid": skill_id, "pool": norm_pool(pool)}
    params.update(_filter_params(category))
    sql = (
        f"SELECT COUNT(DISTINCT pt.posting_id) FROM posting_tech pt "
        f"JOIN posting p ON p.id = pt.posting_id "
        f"{join}"
        f"WHERE pt.skill_id = :sid AND pt.is_deleted = false AND {_POOL_WHERE}{where_extra}"
    )
    sql_start = time.perf_counter()
    n = int(session.execute(text(sql), params).scalar() or 0)
    sql_ms = round((time.perf_counter() - sql_start) * 1000, 1)
    pct = round(100 * n / total, 1) if total else 0.0
    if category or entry_level:
        label = f"{canonical} 수요{_filter_label_suffix(category, entry_level)}"
        citation_label = (
            f"{canonical} 요구 공고 {n:,}건{_filter_citation_suffix(category, entry_level)}"
        )
        facts = (
            f"{canonical}을(를) 요구하는 공고는 {n:,}건(pool={pool or '전체'} "
            f"직군={category or '전체'} 신입={'예' if entry_level else '무관'} "
            f"{total:,}건 중 {pct}%)"
        )
    else:
        label = f"{canonical} 수요"
        citation_label = f"{canonical} 요구 공고 {n:,}건"
        facts = f"{canonical}을(를) 요구하는 공고는 {n:,}건(pool={pool or '전체'} {total:,}건 중 {pct}%)"
    debug = {"sql": sql, "params": params, "sql_ms": sql_ms} if verbose else None
    return {
        "tool": "sql",
        "tool_result": {
            "kind": "stat",
            "label": label,
            "value": n,
            "unit": "건",
            "items": [{"name": canonical, "metric": f"{n:,}건", "pct": pct}],
            "debug": debug,
        },
        "citation": {
            "type": "sql",
            "ref": f"{canonical} 공고 매칭",
            "label": citation_label,
        },
        "n": n,
        "facts": facts,
    }


def multi_skill_compare(
    session: Session,
    skill_names: list[str],
    pool: str | None = None,
    category: str | None = None,
    entry_level: bool = False,
    verbose: bool = False,
) -> dict | None:
    """여러 기술의 수요를 한 번에 비교한다 (compare 결과 kind=list 반환).

    각 기술을 skill_demand로 개별 조회한 뒤 하나의 compare 결과로 합산한다.
    하나도 해소 못 하면 None 반환.
    """
    total = total_postings(session, pool, category=category, entry_level=entry_level)
    items = []
    resolved_names = []
    last_sql: str | None = None
    sql_ms_total = 0.0

    for name in skill_names:
        resolved = resolve_skill(session, name)
        if not resolved:
            continue
        skill_id, canonical = resolved
        join = _category_join(category)
        where_extra = _entry_level_where(entry_level)
        params: dict[str, object] = {"sid": skill_id, "pool": norm_pool(pool)}
        params.update(_filter_params(category))
        sql = (
            f"SELECT COUNT(DISTINCT pt.posting_id) FROM posting_tech pt "
            f"JOIN posting p ON p.id = pt.posting_id "
            f"{join}"
            f"WHERE pt.skill_id = :sid AND pt.is_deleted = false AND {_POOL_WHERE}{where_extra}"
        )
        last_sql = sql
        sql_start = time.perf_counter()
        n = int(session.execute(text(sql), params).scalar() or 0)
        sql_ms_total += (time.perf_counter() - sql_start) * 1000
        pct = round(100 * n / total, 1) if total else 0.0
        items.append({"name": canonical, "metric": f"{n:,}건", "pct": pct})
        resolved_names.append(canonical)

    if not items:
        return None

    skills_joined = "·".join(resolved_names)
    filter_suffix = _filter_label_suffix(category, entry_level)
    facts_body = "; ".join(f"{it['name']} {it['metric']}({it['pct']}%)" for it in items)
    facts = (
        f"국내 채용 공고 {total:,}건 기준 {skills_joined} 비교 결과{filter_suffix} — {facts_body}"
    )
    debug = (
        {
            "sql": last_sql,
            "note": "동일 SQL을 기술마다 :sid만 바꿔 재실행(위는 마지막 실행분)",
            "sql_ms": round(sql_ms_total, 1),
        }
        if verbose
        else None
    )
    return {
        "tool": "sql",
        "tool_result": {
            "kind": "compare",
            "label": f"{skills_joined} 수요 비교{filter_suffix}",
            "items": items,
            "debug": debug,
        },
        "citation": {
            "type": "sql",
            "ref": f"{skills_joined} 비교",
            "label": f"{skills_joined} 요구 공고 비교 · 공고 {total:,}건",
        },
        "n": total,
        "facts": facts,
    }

