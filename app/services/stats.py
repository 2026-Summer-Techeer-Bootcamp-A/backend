from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.posting import Posting, PostingCategory, PostingTech
from app.models.skill import Skill
from app.schemas.match import Pool
from app.schemas.stats import (
    CooccurrenceItem,
    CooccurrenceResponse,
    SkillShareItem,
    SkillShareResponse,
)


def get_skill_share_response(
    session: Session,
    *,
    pool: Pool,
    position: str | None,
    limit: int,
) -> SkillShareResponse:
    posting_query = select(Posting.id).where(
        Posting.pool == pool,
        Posting.is_deleted.is_(False),
    )

    if position:
        posting_query = posting_query.join(
            PostingCategory, PostingCategory.posting_id == Posting.id
        ).where(
            PostingCategory.category == position,
            PostingCategory.is_deleted.is_(False),
        )

    posting_pool = posting_query.subquery()

    sample_size = session.scalar(select(func.count()).select_from(posting_pool)) or 0

    skills: list[SkillShareItem] = []
    if sample_size > 0:
        rows = session.execute(
            select(
                Skill.canonical,
                func.count(func.distinct(PostingTech.posting_id)).label("posting_count"),
            )
            .join(PostingTech, PostingTech.skill_id == Skill.id)
            .join(posting_pool, posting_pool.c.id == PostingTech.posting_id)
            .where(
                Skill.is_deleted.is_(False),
                PostingTech.is_deleted.is_(False),
            )
            .group_by(Skill.id, Skill.canonical)
            .order_by(func.count(func.distinct(PostingTech.posting_id)).desc(), Skill.canonical.asc())
            .limit(limit)
        ).all()

        skills = [
            SkillShareItem(
                canonical=row.canonical,
                share=round(row.posting_count / sample_size, 4),
                posting_count=row.posting_count,
            )
            for row in rows
        ]

    return SkillShareResponse(
        pool=pool,
        skills=skills,
        as_of=date.today().isoformat(),
        sample_size=sample_size,
    )


def get_cooccurrence_response(
    session: Session,
    *,
    skill: str,
    pool: Pool,
    limit: int,
) -> CooccurrenceResponse:
    base_skill_id = session.scalar(
        select(Skill.id).where(
            Skill.canonical == skill,
            Skill.is_deleted.is_(False),
        )
    )

    if base_skill_id is None:
        return CooccurrenceResponse(skill=skill, co_occurs=[], as_of=date.today().isoformat())

    base_posting_query = (
        select(PostingTech.posting_id)
        .join(Posting, Posting.id == PostingTech.posting_id)
        .where(
            PostingTech.skill_id == base_skill_id,
            PostingTech.is_deleted.is_(False),
            Posting.pool == pool,
            Posting.is_deleted.is_(False),
        )
    )
    base_posting_pool = base_posting_query.subquery()

    base_count = session.scalar(select(func.count()).select_from(base_posting_pool)) or 0

    co_occurs: list[CooccurrenceItem] = []
    if base_count > 0:
        rows = session.execute(
            select(
                Skill.canonical,
                func.count(func.distinct(PostingTech.posting_id)).label("co_count"),
            )
            .join(PostingTech, PostingTech.skill_id == Skill.id)
            .join(base_posting_pool, base_posting_pool.c.posting_id == PostingTech.posting_id)
            .where(
                Skill.id != base_skill_id,
                Skill.is_deleted.is_(False),
                PostingTech.is_deleted.is_(False),
            )
            .group_by(Skill.id, Skill.canonical)
            .order_by(func.count(func.distinct(PostingTech.posting_id)).desc(), Skill.canonical.asc())
            .limit(limit)
        ).all()

        co_occurs = [
            CooccurrenceItem(
                canonical=row.canonical,
                co_rate=round(row.co_count / base_count, 4),
                co_count=row.co_count,
            )
            for row in rows
        ]

    return CooccurrenceResponse(
        skill=skill,
        co_occurs=co_occurs,
        as_of=date.today().isoformat(),
    )
