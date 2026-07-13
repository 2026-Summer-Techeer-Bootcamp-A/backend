"""도구 공통 헬퍼 — 기술명 해소, pool 파라미터 정규화."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def norm_pool(pool: str | None) -> str | None:
    return pool if pool in ("domestic", "global") else None


def resolve_skill(session: Session, name: str) -> tuple[int, str] | None:
    """기술명/별칭 -> (skill_id, canonical). 정규명 우선, 없으면 별칭 조회."""
    if not name:
        return None
    row = session.execute(
        text("SELECT id, canonical FROM skill WHERE lower(canonical)=lower(:n) LIMIT 1"),
        {"n": name.strip()},
    ).first()
    if row:
        return int(row.id), row.canonical
    row = session.execute(
        text(
            "SELECT s.id, s.canonical FROM skill s "
            "JOIN skill_alias a ON a.skill_id = s.id "
            "WHERE lower(a.alias)=lower(:n) LIMIT 1"
        ),
        {"n": name.strip()},
    ).first()
    return (int(row.id), row.canonical) if row else None
