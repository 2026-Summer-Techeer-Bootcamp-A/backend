"""vector_tool — BGE-M3 임베딩 기반 의미 유사 공고 검색(pgvector 코사인).

쿼리를 BGE-M3로 임베딩해 posting_embedding에 코사인 top-k. 저장 벡터와 쿼리 벡터가
모두 정규화되어 있으므로 코사인 거리(<=>)로 순위를 매긴다. 임베더가 비활성이거나
결과가 없으면 키워드 기반 SQL 검색으로 2차 우회(Fallback)한다.
"""

from __future__ import annotations

import time
import re
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.rag.embedder import embed_query
from app.services.rag.tools.common import norm_pool

_POOL_WHERE = (
    "(CAST(:pool AS text) IS NULL OR p.pool = CAST(:pool AS text)) "
    "AND p.is_deleted = false AND e.is_tech_posting = true"
)


def _sql_keyword_fallback(
    session: Session, query: str, pool: str | None = None, limit: int = 8, verbose: bool = False
) -> dict | None:
    """임베딩 검색 불가 또는 결과 0건 시 2차 우회: 쿼리 내 키워드로 SQL 검색"""
    # 쿼리에서 2글자 이상의 알파벳/한글 주요 키워드 추출
    raw_keywords = re.findall(r'[a-zA-Z가-힣0-9+#]{2,}', query)
    stop_words = {"공고", "추천", "해줘", "찾아", "알려", "모바일", "기준", "기술", "이상", "경력"}
    keywords = [k for k in raw_keywords if k.lower() not in stop_words]

    if not keywords:
        keywords = [query[:20]]

    # 첫번째 주요 키워드를 키워드 패러미터로 사용
    main_kw = keywords[0] if keywords else query

    sql = (
        "SELECT p.id, p.title, p.company, p.pool, p.region_city, p.region_district "
        "FROM posting p "
        "WHERE p.is_deleted = false "
        "AND (p.title ILIKE '%' || :kw || '%' OR p.description ILIKE '%' || :kw || '%') "
        "ORDER BY p.id DESC LIMIT :limit"
    )

    rows = session.execute(text(sql), {"kw": main_kw, "limit": limit}).all()

    if not rows:
        return None

    items = []
    for r in rows:
        label = r.title if not r.company else f"{r.title} ({r.company})"
        items.append({
            "name": label,
            "metric": "키워드 매칭",
            "pct": 90.0,
            "id": r.id,
            "company": r.company,
            "pool": r.pool,
            "region": r.region_district or r.region_city,
        })

    facts = "; ".join(f"{it['name']}" for it in items[:5])

    return {
        "tool": "vector",
        "tool_result": {"kind": "posting_list", "label": f"'{main_kw}' 관련 추천 공고", "items": items},
        "citation": {"type": "vector", "ref": "채용공고 검색", "label": "키워드 2차 우회 매칭"},
        "n": len(items),
        "facts": f"'{main_kw}' 키워드 관련 실시간 검색 공고 — {facts}",
    }


def semantic_search(
    session: Session, query: str, pool: str | None = None, limit: int = 8, verbose: bool = False
) -> dict | None:
    vec = embed_query(query)
    if vec is None:
        return _sql_keyword_fallback(session, query, pool=pool, limit=limit, verbose=verbose)

    qv = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
    fetch_limit = min(limit * 5, 40)
    sql = (
        f"SELECT p.id, p.title, p.company, p.pool, p.region_city, p.region_district, "
        f"(e.embedding <=> CAST(:qv AS vector)) AS dist "
        f"FROM posting_embedding e "
        f"JOIN posting p ON p.id = e.id "
        f"WHERE {_POOL_WHERE} "
        f"ORDER BY e.embedding <=> CAST(:qv AS vector) LIMIT :fetch_limit"
    )
    sql_start = time.perf_counter()
    rows = session.execute(
        text(sql),
        {"qv": qv, "pool": norm_pool(pool), "fetch_limit": fetch_limit},
    ).all()
    sql_ms = round((time.perf_counter() - sql_start) * 1000, 1)

    if not rows:
        return _sql_keyword_fallback(session, query, pool=pool, limit=limit, verbose=verbose)

    seen: set[tuple[str, str]] = set()
    deduped = []
    for r in rows:
        key = (r.title.strip().lower(), (r.company or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
        if len(deduped) >= limit:
            break

    if not deduped:
        return _sql_keyword_fallback(session, query, pool=pool, limit=limit, verbose=verbose)

    items = []
    for r in deduped:
        sim = round((1.0 - float(r.dist)) * 100, 1)
        label = r.title if not r.company else f"{r.title} ({r.company})"
        items.append(
            {
                "name": label,
                "metric": f"{sim}% 유사",
                "pct": sim,
                "id": r.id,
                "company": r.company,
                "pool": r.pool,
                "region": r.region_district or r.region_city,
            }
        )

    facts = "; ".join(f"{it['name']} {it['metric']}" for it in items[:5])
    debug = (
        {
            "embedding_model": "BGE-M3",
            "embedding_dim": len(vec),
            "embedding_preview": [round(float(x), 6) for x in vec[:8]],
            "distance_metric": "cosine (pgvector <=>)",
            "raw_cosine_distances": [round(float(r.dist), 6) for r in deduped[:5]],
            "sql": sql,
            "sql_ms": sql_ms,
        }
        if verbose
        else None
    )
    return {
        "tool": "vector",
        "tool_result": {"kind": "posting_list", "label": "의미 유사 공고", "items": items, "debug": debug},
        "citation": {"type": "vector", "ref": "채용공고 의미벡터", "label": "BGE-M3 코사인 top-k"},
        "n": len(deduped),
        "facts": f"질문과 의미가 가까운 공고(코사인 유사도순) — {facts}",
    }
