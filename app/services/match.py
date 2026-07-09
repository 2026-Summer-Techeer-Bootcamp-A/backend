from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session

from app.models.posting import Posting, PostingCategory, PostingTech
from app.models.resume import Resume, ResumeSkill
from app.models.skill import Skill
from app.models.user import User
from app.schemas.match import MatchCoverageResponse, MatchGapResponse, MatchWhatIfResponse,Pool
from app.core.redis import get_resume_confirm_session

#저장된 이력서에서 기술 가져옴
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
    session: Session,
    session_id: str,
) -> set[int]:
    payload = get_resume_confirm_session(session_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session not found",
        )

    canonicals = {
    skill.get("canonical")
    for skill in payload.get("skills", [])
    if isinstance(skill, dict)
    and skill.get("canonical")
    and skill.get("in_dict") is True
}

    if not canonicals:
        return set()

    rows = session.scalars(
        select(Skill.id).where(
            Skill.canonical.in_(canonicals),
            Skill.is_deleted.is_(False),
        )
    ).all()

    return set(rows)


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

def calculate_coverage_response(
    session: Session,
    *,
    pool: Pool,
    position: str | None,
    owned_skill_ids: set[int],
    top_k: int = 20,
) -> MatchCoverageResponse:
    market_skills, sample_size = get_market_skill_frequencies(
        session=session,
        pool=pool,
        position=position,
    )

    top_skills = market_skills[:top_k]
    total_freq = sum(skill["freq"] for skill in top_skills)
    owned_freq = sum(
        skill["freq"]
        for skill in top_skills
        if skill["skill_id"] in owned_skill_ids
    )

    coverage_score = 0.0
    if total_freq > 0:
        coverage_score = round((owned_freq / total_freq) * 100, 1)

    owned_count = sum(
        1 for skill in top_skills if skill["skill_id"] in owned_skill_ids
    )

    return MatchCoverageResponse(
        pool=pool,
        filter={
            "position": position,
            "career_min": None,
            "career_max": None,
        },
        coverage_score=coverage_score,
        top_skills=[
            {
                "canonical": skill["canonical"],
                "freq": round(skill["freq"], 4),
                "owned": skill["skill_id"] in owned_skill_ids,
            }
            for skill in top_skills
        ],
        owned_count=owned_count,
        as_of=date.today().isoformat(),
        sample_size=sample_size,
        sample_warning=sample_size < 50,
    )

def get_pool_as_of(
    session: Session,
    *,
    pool: Pool,
    position: str | None = None,
) -> str:
    posting_pool_query = build_posting_pool_query(pool=pool, position=position).subquery()

    as_of = session.scalar(
        select(func.max(Posting.post_date))
        .join(posting_pool_query, posting_pool_query.c.id == Posting.id)
    )

    return as_of.isoformat() if as_of is not None else date.today().isoformat()


def get_skill_id_by_canonical(session: Session, canonical: str) -> tuple[int, str]:
    skill = session.execute(
        select(Skill.id, Skill.canonical).where(
            func.lower(Skill.canonical) == canonical.lower(),
            Skill.is_deleted.is_(False),
        )
    ).one_or_none()

    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="add is not in taxonomy",
        )

    return skill.id, skill.canonical


def count_matched_postings(
    session: Session,
    *,
    pool: Pool,
    position: str | None,
    skill_ids: set[int],
) -> int:
    if not skill_ids:
        return 0

    posting_pool_query = build_posting_pool_query(pool=pool, position=position).subquery()

    return session.scalar(
        select(func.count(distinct(PostingTech.posting_id)))
        .join(posting_pool_query, posting_pool_query.c.id == PostingTech.posting_id)
        .where(
            PostingTech.skill_id.in_(skill_ids),
            PostingTech.is_deleted.is_(False),
        )
    ) or 0


def calculate_what_if_response(
    session: Session,
    *,
    pool: Pool,
    add: str,
    owned_skill_ids: set[int],
    position: str | None = None,
) -> MatchWhatIfResponse:
    add_skill_id, add_canonical = get_skill_id_by_canonical(session=session, canonical=add)

    posting_pool_query = build_posting_pool_query(pool=pool, position=position).subquery()
    sample_size = session.scalar(select(func.count()).select_from(posting_pool_query)) or 0

    matched_before = count_matched_postings(
        session=session,
        pool=pool,
        position=position,
        skill_ids=owned_skill_ids,
    )

    matched_after = count_matched_postings(
        session=session,
        pool=pool,
        position=position,
        skill_ids=owned_skill_ids | {add_skill_id},
    )

    return MatchWhatIfResponse(
        add=add_canonical,
        matched_before=matched_before,
        matched_after=matched_after,
        delta=matched_after - matched_before,
        as_of=get_pool_as_of(session=session, pool=pool, position=position),
        sample_size=sample_size,
        sample_warning=True if sample_size < 50 else None,
    )
