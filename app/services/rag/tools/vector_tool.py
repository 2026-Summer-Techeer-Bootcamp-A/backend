"""vector_tool — BGE-M3 임베딩 기반 의미 유사 공고 검색(pgvector 코사인).

쿼리를 BGE-M3로 임베딩해 posting_embedding에 코사인 top-k. 저장 벡터와 쿼리 벡터가
모두 정규화되어 있으므로 코사인 거리(<=>)로 순위를 매긴다. 임베더가 비활성이면
None을 반환해 라우터가 sql/graph로 폴백한다.
"""

from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.rag.embedder import embed_query
from app.services.rag.tools.common import norm_pool

# 코퍼스의 78%가 비개발 공고라(부동산 사무직, 간호사 등) 필터 없이는 제목만으로 만든
# 임베딩의 글자 유사도 때문에 무관한 공고가 상위에 올라온다(예: "머신러닝" 질의에 "머시닝"
# 기계가공 공고가 매칭). e.is_tech_posting = true 조건은 부분 HNSW 인덱스
# (ix_posting_embedding_hnsw_tech, app/main.py)의 조건과 정확히 일치해야 플래너가
# 그 인덱스를 탄다.
_POOL_WHERE = (
    "(CAST(:pool AS text) IS NULL OR p.pool = CAST(:pool AS text)) "
    "AND p.is_deleted = false AND e.is_tech_posting = true"
)


def semantic_search(
    session: Session, query: str, pool: str | None = None, limit: int = 8, verbose: bool = False
) -> dict | None:
    vec = embed_query(query)
    if vec is None:
        return None

    qv = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
    # 코퍼스에는 (title, company)가 완전히 같은 중복 공고 그룹이 약 14,058개 있다.
    # LIMIT을 그대로 걸면 top-k가 같은 공고 4~6개로 채워지는 일이 흔하다(실측: "오케스트로
    # Back-end Developer"가 4번 연속 노출). SQL에서 DISTINCT ON (title, company)로 정리하고
    # 싶지만, 그러면 ORDER BY를 title/company 기준으로 바꿔야 해서 ix_posting_embedding_hnsw_tech
    # 인덱스(ORDER BY embedding <=> qv 전제)를 못 타고 122K행 seq scan으로 떨어진다. 그래서
    # 인덱스는 그대로 거리순으로 태우되, 여유분을 더 뽑아(fetch_limit) 파이썬에서 (title,
    # company) 중복을 걸러내는 방식으로 우회한다.
    fetch_limit = min(limit * 5, 40)
    sql = (
        f"SELECT p.id, p.title, p.company, p.pool, "
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
        return None

    # 거리(가까운 순)로 이미 정렬된 rows를 그대로 순회하며 (title, company) 조합이
    # 처음 나온 행만 채택한다. 대소문자/앞뒤 공백만 다른 경우도 같은 공고로 취급하고,
    # 가장 가까운(=가장 먼저 나온) 쪽을 남긴다. limit개를 채우면 즉시 중단한다.
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

    items = []
    for r in deduped:
        sim = round((1.0 - float(r.dist)) * 100, 1)
        label = r.title if not r.company else f"{r.title} ({r.company})"
        # K3: 실제 공고 목록이라 id/company/pool을 함께 실어 프론트가 클릭 가능한
        # 공고 카드(상세보기, 북마크)로 렌더링할 수 있게 한다. 유사도(sim)는 기존과
        # 동일하게 pct/metric에 그대로 둔다.
        items.append(
            {
                "name": label,
                "metric": f"{sim}% 유사",
                "pct": sim,
                "id": r.id,
                "company": r.company,
                "pool": r.pool,
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
        # K3: 실제 공고 목록이라 kind="posting_list"로 표시해 프론트가 카드로 렌더링한다.
        "tool_result": {"kind": "posting_list", "label": "의미 유사 공고", "items": items, "debug": debug},
        "citation": {"type": "vector", "ref": "채용공고 의미벡터", "label": "BGE-M3 코사인 top-k"},
        "n": len(deduped),
        "facts": f"질문과 의미가 가까운 공고(코사인 유사도순) — {facts}",
    }
