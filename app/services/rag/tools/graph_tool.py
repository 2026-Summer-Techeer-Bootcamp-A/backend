"""graph_tool — 지식그래프 local search(공동출현 순회).

"React 배우면 뭘 같이?" 류 관계 질문에 정확한 수치로 답한다.
엣지 = 같은 공고에서 함께 요구된 기술 쌍. strength = 대상 기술 공고 중 동반 비율.
서브그래프(nodes/edges)를 tool_result.graph 로 반환해 프론트 네트워크 위젯이 렌더.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.rag.tools.common import norm_pool, resolve_skill

_POOL_WHERE = (
    "(CAST(:pool AS text) IS NULL OR p.pool = CAST(:pool AS text)) "
    "AND p.is_deleted = false"
)


def co_occurring_skills(
    session: Session, skill_name: str, pool: str | None = None, limit: int = 8
) -> dict | None:
    resolved = resolve_skill(session, skill_name)
    if not resolved:
        return None
    skill_id, canonical = resolved
    pool = norm_pool(pool)

    base = int(
        session.execute(
            text(
                f"SELECT COUNT(DISTINCT pt.posting_id) FROM posting_tech pt "
                f"JOIN posting p ON p.id = pt.posting_id "
                f"WHERE pt.skill_id = :sid AND pt.is_deleted = false AND {_POOL_WHERE}"
            ),
            {"sid": skill_id, "pool": pool},
        ).scalar()
        or 0
    )
    if base == 0:
        return None

    rows = session.execute(
        text(
            f"SELECT s2.canonical, COUNT(DISTINCT pt2.posting_id) n "
            f"FROM posting_tech pt1 "
            f"JOIN posting_tech pt2 ON pt1.posting_id = pt2.posting_id "
            f"  AND pt2.skill_id <> pt1.skill_id AND pt2.is_deleted = false "
            f"JOIN skill s2 ON s2.id = pt2.skill_id "
            f"JOIN posting p ON p.id = pt1.posting_id "
            f"WHERE pt1.skill_id = :sid AND pt1.is_deleted = false AND {_POOL_WHERE} "
            f"GROUP BY s2.canonical ORDER BY n DESC LIMIT :limit"
        ),
        {"sid": skill_id, "pool": pool, "limit": limit},
    ).all()

    items, edges, nodes = [], [], [{"id": canonical, "root": True}]
    for name, n in rows:
        pct = round(100 * int(n) / base, 1)
        items.append({"name": name, "metric": f"{pct}%", "pct": pct})
        nodes.append({"id": name})
        edges.append({"source": canonical, "target": name, "strength": pct, "n": int(n)})

    facts = "; ".join(f"{it['name']} {it['pct']}%" for it in items)
    return {
        "tool_result": {
            "kind": "graph",
            "label": f"{canonical} 동반 기술(공동출현)",
            "items": items,
            "nodes": nodes,
            "edges": edges,
        },
        "citation": {
            "type": "graph",
            "ref": f"{canonical} 동반출현",
            "label": f"{canonical} 요구 공고 {base:,}건의 동반 기술",
        },
        "n": base,
        "facts": f"{canonical}을(를) 요구하는 공고 {base:,}건 기준 동반 기술 비율 — {facts}",
    }
