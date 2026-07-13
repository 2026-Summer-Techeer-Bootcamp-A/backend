"""sql_tool — 정량·랭킹·커버리지 질문에 결정론적 집계로 답한다(할루시네이션 0).

모든 쿼리는 파라미터화. pool(domestic|global) 필터와 소프트삭제(is_deleted) 제외.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.rag.tools.common import norm_pool, resolve_skill

_POOL_WHERE = (
    "(CAST(:pool AS text) IS NULL OR p.pool = CAST(:pool AS text)) "
    "AND p.is_deleted = false"
)


def total_postings(session: Session, pool: str | None) -> int:
    pool = norm_pool(pool)
    return int(
        session.execute(
            text(f"SELECT COUNT(*) FROM posting p WHERE {_POOL_WHERE}"), {"pool": pool}
        ).scalar()
        or 0
    )


def _top(session: Session, sql: str, pool: str | None, limit: int) -> list[tuple[str, int]]:
    rows = session.execute(text(sql), {"pool": norm_pool(pool), "limit": limit}).all()
    return [(r[0], int(r[1])) for r in rows]


def top_skills(session: Session, pool: str | None = None, limit: int = 8) -> dict:
    total = total_postings(session, pool)
    rows = _top(
        session,
        f"SELECT s.canonical, COUNT(*) n FROM posting_tech pt "
        f"JOIN skill s ON s.id = pt.skill_id "
        f"JOIN posting p ON p.id = pt.posting_id "
        f"WHERE {_POOL_WHERE} AND pt.is_deleted = false "
        f"GROUP BY s.canonical ORDER BY n DESC LIMIT :limit",
        pool,
        limit,
    )
    items = [
        {"name": n, "metric": f"{c:,}건", "pct": round(100 * c / total, 1) if total else 0.0}
        for n, c in rows
    ]
    facts = "; ".join(f"{n} {c}건({round(100 * c / total, 1) if total else 0}%)" for n, c in rows)
    return {
        "tool_result": {"kind": "list", "label": "수요 상위 기술", "items": items},
        "citation": {
            "type": "sql",
            "ref": "채용공고·기술 태그",
            "label": f"기술태그 집계 · 공고 {total:,}건",
        },
        "n": total,
        "facts": f"pool={pool or '전체'} 총 {total:,}건 기준 상위 기술 — {facts}",
    }


def top_concepts(session: Session, pool: str | None = None, limit: int = 8) -> dict:
    total = total_postings(session, pool)
    rows = _top(
        session,
        f"SELECT c.name, COUNT(*) n FROM posting_concept pc "
        f"JOIN concept c ON c.id = pc.concept_id "
        f"JOIN posting p ON p.id = pc.posting_id "
        f"WHERE {_POOL_WHERE} AND pc.is_deleted = false "
        f"GROUP BY c.name ORDER BY n DESC LIMIT :limit",
        pool,
        limit,
    )
    items = [
        {"name": n, "metric": f"{c:,}건", "pct": round(100 * c / total, 1) if total else 0.0}
        for n, c in rows
    ]
    facts = "; ".join(f"{n} {c}건" for n, c in rows)
    return {
        "tool_result": {"kind": "list", "label": "빈출 개념·패러다임", "items": items},
        "citation": {
            "type": "sql",
            "ref": "채용공고·개념",
            "label": f"개념 집계 · 공고 {total:,}건",
        },
        "n": total,
        "facts": f"pool={pool or '전체'} 상위 개념 — {facts}",
    }


def top_certs(session: Session, pool: str | None = None, limit: int = 8) -> dict:
    total = total_postings(session, pool)
    rows = _top(
        session,
        f"SELECT ct.name, COUNT(*) n FROM posting_cert pc "
        f"JOIN cert ct ON ct.id = pc.cert_id "
        f"JOIN posting p ON p.id = pc.posting_id "
        f"WHERE {_POOL_WHERE} AND pc.is_deleted = false "
        f"GROUP BY ct.name ORDER BY n DESC LIMIT :limit",
        pool,
        limit,
    )
    items = [
        {"name": n, "metric": f"{c:,}건", "pct": round(100 * c / total, 1) if total else 0.0}
        for n, c in rows
    ]
    facts = "; ".join(f"{n} {c}건" for n, c in rows)
    return {
        "tool_result": {"kind": "list", "label": "요구 상위 자격증", "items": items},
        "citation": {
            "type": "sql",
            "ref": "채용공고·자격증",
            "label": f"자격증 요구 집계 · 공고 {total:,}건",
        },
        "n": total,
        "facts": f"pool={pool or '전체'} 총 {total:,}건 기준 상위 자격증 — {facts}",
    }


def skill_demand(session: Session, skill_name: str, pool: str | None = None) -> dict | None:
    resolved = resolve_skill(session, skill_name)
    if not resolved:
        return None
    skill_id, canonical = resolved
    total = total_postings(session, pool)
    n = int(
        session.execute(
            text(
                f"SELECT COUNT(DISTINCT pt.posting_id) FROM posting_tech pt "
                f"JOIN posting p ON p.id = pt.posting_id "
                f"WHERE pt.skill_id = :sid AND pt.is_deleted = false AND {_POOL_WHERE}"
            ),
            {"sid": skill_id, "pool": norm_pool(pool)},
        ).scalar()
        or 0
    )
    pct = round(100 * n / total, 1) if total else 0.0
    return {
        "tool_result": {
            "kind": "stat",
            "label": f"{canonical} 수요",
            "value": n,
            "unit": "건",
            "items": [{"name": canonical, "metric": f"{n:,}건", "pct": pct}],
        },
        "citation": {
            "type": "sql",
            "ref": f"{canonical} 공고 매칭",
            "label": f"{canonical} 요구 공고 {n:,}건",
        },
        "n": n,
        "facts": f"{canonical}을(를) 요구하는 공고는 {n:,}건(pool={pool or '전체'} {total:,}건 중 {pct}%)",
    }
