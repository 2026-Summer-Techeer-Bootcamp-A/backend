"""vector_tool — BGE-M3 임베딩 기반 의미 유사 공고 검색(pgvector 코사인).

쿼리를 BGE-M3로 임베딩해 posting_embedding에 코사인 top-k. 저장 벡터와 쿼리 벡터가
모두 정규화되어 있으므로 코사인 거리(<=>)로 순위를 매긴다. 임베더가 비활성이면
None을 반환해 라우터가 sql/graph로 폴백한다.
"""

from __future__ import annotations

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
    sql = (
        f"SELECT p.id, p.title, p.company, p.pool, "
        f"(e.embedding <=> CAST(:qv AS vector)) AS dist "
        f"FROM posting_embedding e "
        f"JOIN posting p ON p.id = e.id "
        f"WHERE {_POOL_WHERE} "
        f"ORDER BY e.embedding <=> CAST(:qv AS vector) LIMIT :limit"
    )
    rows = session.execute(
        text(sql),
        {"qv": qv, "pool": norm_pool(pool), "limit": limit},
    ).all()
    if not rows:
        return None

    items = []
    for r in rows:
        sim = round((1.0 - float(r.dist)) * 100, 1)
        label = r.title if not r.company else f"{r.title} ({r.company})"
        items.append({"name": label, "metric": f"{sim}% 유사", "pct": sim})

    facts = "; ".join(f"{it['name']} {it['metric']}" for it in items[:5])
    debug = (
        {
            "embedding_model": "BGE-M3",
            "embedding_dim": len(vec),
            "embedding_preview": [round(float(x), 6) for x in vec[:8]],
            "distance_metric": "cosine (pgvector <=>)",
            "sql": sql,
        }
        if verbose
        else None
    )
    return {
        "tool": "vector",
        "tool_result": {"kind": "list", "label": "의미 유사 공고", "items": items, "debug": debug},
        "citation": {"type": "vector", "ref": "채용공고 의미벡터", "label": "BGE-M3 코사인 top-k"},
        "n": len(rows),
        "facts": f"질문과 의미가 가까운 공고(코사인 유사도순) — {facts}",
    }
