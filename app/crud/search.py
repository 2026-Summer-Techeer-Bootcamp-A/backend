"""통합 검색 — 공고 · 기술 · 기업을 한 쿼리 세트로 조회."""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.crud.skill import search_skills
from app.models import Posting, Skill


def search_postings(session: Session, q: str, limit: int) -> list[Posting]:
    pattern = f"%{q}%"
    stmt = (
        select(Posting)
        .where(
            Posting.is_deleted.is_(False),
            or_(
                Posting.title.ilike(pattern),
                Posting.company.ilike(pattern),
            ),
        )
        .order_by(Posting.post_date.is_(None), Posting.post_date.desc(), Posting.id.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def search_companies(session: Session, q: str, limit: int) -> list[dict]:
    pattern = f"%{q}%"
    stmt = (
        select(Posting.company, func.count(Posting.id).label("posting_count"))
        .where(
            Posting.is_deleted.is_(False),
            Posting.company.isnot(None),
            Posting.company.ilike(pattern),
        )
        .group_by(Posting.company)
        .order_by(func.count(Posting.id).desc(), Posting.company.asc())
        .limit(limit)
    )
    return [{"company": row.company, "posting_count": row.posting_count} for row in session.execute(stmt).all()]


def search_all(session: Session, q: str, limit: int) -> dict[str, list[Posting] | list[Skill] | list[dict]]:
    return {
        "postings": search_postings(session, q, limit),
        "skills": search_skills(session=session, q=q, limit=limit),
        "companies": search_companies(session, q, limit),
    }
