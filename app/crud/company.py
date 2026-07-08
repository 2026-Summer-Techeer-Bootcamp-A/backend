"""F7+F11: 특정 기술을 요구한 기업 조회 — 과거/현재 분할 + 원티드 응답률."""

from datetime import date, timedelta

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models.posting import Posting, PostingTech
from app.models.skill import Skill, SkillAlias


def find_skill_id(session: Session, skill_name: str) -> int | None:
    """canonical 정확 매칭 → alias 정확 매칭 순으로 skill_id를 찾는다 (case-insensitive)."""
    # 1) canonical 정확 매칭
    stmt = (
        select(Skill.id)
        .where(Skill.is_deleted.is_(False))
        .where(func.lower(Skill.canonical) == func.lower(skill_name))
    )
    skill_id = session.scalar(stmt)
    if skill_id is not None:
        return skill_id

    # 2) alias 정확 매칭
    stmt = (
        select(SkillAlias.skill_id)
        .join(Skill, Skill.id == SkillAlias.skill_id)
        .where(SkillAlias.is_deleted.is_(False))
        .where(Skill.is_deleted.is_(False))
        .where(func.lower(SkillAlias.alias) == func.lower(skill_name))
    )
    return session.scalar(stmt)


def get_companies_by_skill(
    session: Session,
    skill_id: int,
    pool: str | None = None,
) -> tuple[date, date, list[dict], list[dict]]:
    """기술을 요구한 기업을 180일 기준으로 과거/현재로 나눠 반환한다.

    Returns:
        (split_date, as_of, present_companies, past_companies)
        각 company dict = {"company": str, "posting_count": int, "response_rate": float|None}
    """
    # 공통 필터 조건
    base_filters = [
        PostingTech.skill_id == skill_id,
        Posting.is_deleted.is_(False),
        PostingTech.is_deleted.is_(False),
        Posting.company.isnot(None),
        Posting.post_date.isnot(None),
    ]
    if pool is not None:
        base_filters.append(Posting.pool == pool)

    # Step 1: split_date 산출 — 해당 skill+pool 범위 내 MAX(post_date) - 180일
    max_date_stmt = (
        select(func.max(Posting.post_date))
        .select_from(Posting)
        .join(PostingTech, PostingTech.posting_id == Posting.id)
        .where(*base_filters)
    )
    as_of = session.scalar(max_date_stmt)

    if as_of is None:
        # 해당 skill+pool에 공고가 하나도 없음
        return date.today(), date.today(), [], []

    split_date = as_of - timedelta(days=180)

    # Step 2: 회사별 집계
    is_present_expr = case(
        (Posting.post_date >= split_date, True),
        else_=False,
    )

    # 원티드 공고의 response_rate만 평균 (원티드가 아닌 공고는 NULL이므로 AVG에서 자동 제외)
    avg_response_rate = func.avg(
        case(
            (Posting.source == "wanted", Posting.response_rate),
            else_=None,
        )
    )

    stmt = (
        select(
            Posting.company,
            func.count().label("posting_count"),
            avg_response_rate.label("response_rate"),
            func.bool_or(is_present_expr).label("is_present"),
        )
        .select_from(Posting)
        .join(PostingTech, PostingTech.posting_id == Posting.id)
        .where(*base_filters)
        .group_by(Posting.company)
        .order_by(func.count().desc())
    )

    rows = session.execute(stmt).all()

    present: list[dict] = []
    past: list[dict] = []

    for row in rows:
        entry = {
            "company": row.company,
            "posting_count": row.posting_count,
            "response_rate": (
                round(float(row.response_rate), 2)
                if row.response_rate is not None
                else None
            ),
        }
        if row.is_present:
            present.append(entry)
        else:
            past.append(entry)

    return split_date, as_of, present, past
