from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session

from app.models.posting import Posting, PostingCategory, PostingTech
from app.models.resume import Resume, ResumeSkill
from app.models.skill import Skill
from app.models.user import User
from app.schemas.match import MatchGapResponse, Pool


def get_skill_ids_from_resume(
    session: Session,
    resume_id: int,
    current_user: User,
) -> set[int]:
    resume = session.scalar(
        select(Resume).where(
            Resume.resume_id == resume_id,
            Resume.user_id == current_user.id,
            Resume.is_deleted.is_(False),
        )
    )
    if resume is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="resume not found",
        )

    rows = session.scalars(
        select(ResumeSkill.skill_id).where(
            ResumeSkill.resume_id == resume_id,
            ResumeSkill.skill_id.is_not(None),
            ResumeSkill.is_deleted.is_(False),
        )
    ).all()

    return set(rows)


def get_skill_ids_from_session(
    session_id: str,
) -> set[int]:
    # TODO: /resume/confirm 구현 후 연결 부분
    
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="session not found",
    )


def build_posting_pool_query(pool: Pool, position: str | None) -> Select:
    query = select(Posting.id).where(
        Posting.pool == pool,
        Posting.is_deleted.is_(False),
    )

    if position:
        query = (
            query.join(PostingCategory, PostingCategory.posting_id == Posting.id)
            .where(
                PostingCategory.category == position,
                PostingCategory.is_deleted.is_(False),
            )
        )

    return query


def get_market_skill_frequencies(
    session: Session,
    pool: Pool,
    position: str | None,
) -> tuple[list[dict], int]:
    posting_pool_query = build_posting_pool_query(pool=pool, position=position).subquery()

    sample_size = session.scalar(
        select(func.count()).select_from(posting_pool_query)
    ) or 0

    if sample_size == 0:
        return [], 0

    rows = session.execute(
        select(
            Skill.id.label("skill_id"),
            Skill.canonical,
            Skill.category,
            (func.count(distinct(PostingTech.posting_id)) / sample_size).label("freq"),
        )
        .join(PostingTech, PostingTech.skill_id == Skill.id)
        .join(posting_pool_query, posting_pool_query.c.id == PostingTech.posting_id)
        .where(
            Skill.is_deleted.is_(False),
            PostingTech.is_deleted.is_(False),
        )
        .group_by(Skill.id, Skill.canonical, Skill.category)
        .order_by(func.count(distinct(PostingTech.posting_id)).desc(), Skill.canonical.asc())
    ).all()

    return [
        {
            "skill_id": row.skill_id,
            "canonical": row.canonical,
            "category": row.category,
            "freq": float(row.freq),
        }
        for row in rows
    ], sample_size


def calculate_gap_response(
    session: Session,
    *,
    pool: Pool,
    position: str | None,
    owned_skill_ids: set[int],
) -> MatchGapResponse:
    market_skills, sample_size = get_market_skill_frequencies(
        session=session,
        pool=pool,
        position=position,
    )

    gap_top5 = [
        {
            "canonical": skill["canonical"],
            "freq": round(skill["freq"], 4),
            "category": skill["category"],
        }
        for skill in market_skills
        if skill["skill_id"] not in owned_skill_ids
    ][:5]

    category_totals: dict[str, float] = {}
    category_owned: dict[str, float] = {}

    for skill in market_skills:
        category = skill["category"]
        freq = skill["freq"]

        category_totals[category] = category_totals.get(category, 0.0) + freq

        if skill["skill_id"] in owned_skill_ids:
            category_owned[category] = category_owned.get(category, 0.0) + freq

    radar = []
    for category in sorted(category_totals.keys()):
        total = category_totals[category]
        owned = category_owned.get(category, 0.0)
        coverage = owned / total if total > 0 else 0.0

        radar.append(
            {
                "category": category,
                "coverage": round(coverage, 4),
            }
        )

    return MatchGapResponse(
        gap_top5=gap_top5,
        radar=radar,
        as_of=date.today().isoformat(),
        sample_size=sample_size,
        sample_warning=True if sample_size < 50 else None,
    )