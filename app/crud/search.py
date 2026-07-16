"""통합 검색 — 공고 · 기술 · 기업을 한 쿼리 세트로 조회."""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.crud.skill import search_skills
from app.models import Posting, Skill


def _tokenize(q: str) -> list[str]:
    """공백 기준으로 토큰화한다. 빈 토큰(연속 공백)은 걸러낸다."""
    return [t for t in q.split() if t]


def search_postings(session: Session, q: str, limit: int) -> list[Posting]:
    # 예전엔 쿼리 전체를 하나의 ILIKE 패턴으로 묶어서, "React backend"처럼 두 단어를
    # 함께 검색하면 제목에 그 문구가 그대로 들어있는 공고만 찾아 0건이 나왔다(단어 각각은
    # 매치되는데도). 이제 공백으로 토큰화해 각 토큰이 (제목 또는 회사명에) 매치되는 것을
    # AND로 요구한다 — 단일 토큰 쿼리는 조건이 하나뿐이라 기존 동작과 완전히 동일하다.
    tokens = _tokenize(q)
    if not tokens:
        return []
    token_conditions = [
        or_(Posting.title.ilike(f"%{t}%"), Posting.company.ilike(f"%{t}%")) for t in tokens
    ]
    stmt = (
        select(Posting)
        .where(
            Posting.is_deleted.is_(False),
            and_(*token_conditions),
        )
        .order_by(Posting.post_date.is_(None), Posting.post_date.desc(), Posting.id.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def search_companies(session: Session, q: str, limit: int) -> list[dict]:
    tokens = _tokenize(q)
    if not tokens:
        return []
    token_conditions = [Posting.company.ilike(f"%{t}%") for t in tokens]
    stmt = (
        select(Posting.company, func.count(Posting.id).label("posting_count"))
        .where(
            Posting.is_deleted.is_(False),
            Posting.company.isnot(None),
            and_(*token_conditions),
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
