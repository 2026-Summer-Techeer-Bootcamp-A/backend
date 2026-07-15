"""graph_tool — 지식그래프 local search(공동출현 순회).

"React 배우면 뭘 같이?" 류 관계 질문에 정확한 수치로 답한다.
엣지 = 같은 공고에서 함께 요구된 기술 쌍. strength = 대상 기술 공고 중 동반 비율.
서브그래프(nodes/edges)를 tool_result.graph 로 반환해 프론트 네트워크 위젯이 렌더.
2-hop 크로스엣지: 1-hop 이웃들끼리의 공동출현도 함께 반환해 2단 네트워크를 구성한다.
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
    session: Session, skill_name: str, pool: str | None = None, limit: int = 8, verbose: bool = False
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

    # 1-hop: 루트 기술과 직접 공동출현하는 이웃 기술 목록 (skill_id도 함께 조회)
    sql_1hop = (
        f"SELECT s2.canonical, s2.id, COUNT(DISTINCT pt2.posting_id) n "
        f"FROM posting_tech pt1 "
        f"JOIN posting_tech pt2 ON pt1.posting_id = pt2.posting_id "
        f"  AND pt2.skill_id <> pt1.skill_id AND pt2.is_deleted = false "
        f"JOIN skill s2 ON s2.id = pt2.skill_id "
        f"JOIN posting p ON p.id = pt1.posting_id "
        f"WHERE pt1.skill_id = :sid AND pt1.is_deleted = false AND {_POOL_WHERE} "
        f"GROUP BY s2.canonical, s2.id ORDER BY n DESC LIMIT :limit"
    )
    rows = session.execute(
        text(sql_1hop),
        {"sid": skill_id, "pool": pool, "limit": limit},
    ).all()

    items, edges, nodes = [], [], [{"id": canonical, "root": True, "hop": 0}]
    neighbor_ids: list[int] = []
    sql_cross: str | None = None
    neighbor_names: set[str] = set()

    for name, skill_id_2, n in rows:
        pct = round(100 * int(n) / base, 1)
        items.append({"name": name, "metric": f"{pct}%", "pct": pct})
        nodes.append({"id": name, "hop": 1})
        edges.append({"source": canonical, "target": name, "strength": pct, "n": int(n), "hop": 1})
        neighbor_ids.append(int(skill_id_2))
        neighbor_names.add(name)

    # 2-hop 크로스엣지: 이웃 기술들끼리의 공동출현 (이웃 IN × 이웃 IN, pt2.skill_id > pt1.skill_id 로 중복 제거)
    if len(neighbor_ids) >= 2:
        id_list = ",".join(str(i) for i in neighbor_ids)
        sql_cross = (
            f"SELECT s1.canonical sa, s2.canonical sb, COUNT(DISTINCT pt1.posting_id) n "
            f"FROM posting_tech pt1 "
            f"JOIN posting_tech pt2 ON pt1.posting_id = pt2.posting_id "
            f"  AND pt2.skill_id > pt1.skill_id AND pt2.is_deleted = false "
            f"JOIN skill s1 ON s1.id = pt1.skill_id "
            f"JOIN skill s2 ON s2.id = pt2.skill_id "
            f"JOIN posting p ON p.id = pt1.posting_id "
            f"WHERE pt1.skill_id IN ({id_list}) "
            f"  AND pt2.skill_id IN ({id_list}) "
            f"  AND pt1.is_deleted = false AND {_POOL_WHERE} "
            f"GROUP BY s1.canonical, s2.canonical "
            f"ORDER BY n DESC LIMIT 20"
        )
        cross_rows = session.execute(
            text(sql_cross),
            {"pool": pool},
        ).all()

        for sa, sb, n_cross in cross_rows:
            if sa in neighbor_names and sb in neighbor_names:
                pct_cross = round(100 * int(n_cross) / base, 1)
                edges.append({
                    "source": sa,
                    "target": sb,
                    "strength": pct_cross,
                    "n": int(n_cross),
                    "hop": 2,
                })

    facts = "; ".join(f"{it['name']} {it['pct']}%" for it in items)
    debug = (
        {
            "strength_formula": "strength = (동반 공고 n건 / 대상 기술 기준 공고 base건) x 100",
            "base_postings": base,
            "sql_1hop": sql_1hop,
            "sql_2hop_cross": sql_cross,
        }
        if verbose
        else None
    )
    return {
        "tool": "graph",
        "tool_result": {
            "kind": "graph",
            "label": f"{canonical} 동반 기술(공동출현)",
            "items": items,
            "nodes": nodes,
            "edges": edges,
            "debug": debug,
        },
        "citation": {
            "type": "graph",
            "ref": f"{canonical} 동반출현",
            "label": f"{canonical} 요구 공고 {base:,}건의 동반 기술",
        },
        "n": base,
        "facts": f"{canonical}을(를) 요구하는 공고 {base:,}건 기준 동반 기술 비율 — {facts}",
    }
