from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session

from app.models.job_category import JobCategory
from app.models.posting import Posting, PostingCategory, PostingTech
from app.models.resume import Resume, ResumeSkill
from app.models.skill import Skill
from app.models.user import User
from app.schemas.match import (
    MatchCoverageDistributionResponse,
    MatchCoverageResponse,
    MatchGapResponse,
    MatchPivotMapResponse,
    MatchRoadmapResponse,
    MatchWhatIfResponse,
    Pool,
)
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
        # 마감일이 지난 공고는 시장 모수에서 제외한다(마감일 자체가 없는 상시채용은 유지).
        # app/crud/posting.py의 _apply_posting_filters와 동일한 기준으로 맞춰서
        # "지원 가능 공고" 수치와 시장 통계(스킬 점유율/커버리지/gap/roadmap/pivot-map)가
        # 같은 모수를 쓰도록 한다.
        Posting.close_date.is_(None) | (Posting.close_date >= date.today()),
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


def calculate_coverage_distribution_response(
    session: Session,
    *,
    pool: Pool,
    position: str | None,
    owned_skill_ids: set[int],
    threshold: float = 50.0,
    min_required_skills: int = 3,
    bin_size: int = 5,
) -> MatchCoverageDistributionResponse:
    """공고별(요구기술 min_required_skills개 이상) 커버리지 분포 히스토그램. widgets 'c-coverage-dist' 정식화."""
    posting_pool_query = build_posting_pool_query(pool=pool, position=position).subquery()

    rows = session.execute(
        select(PostingTech.posting_id, PostingTech.skill_id)
        .join(posting_pool_query, posting_pool_query.c.id == PostingTech.posting_id)
        .where(PostingTech.is_deleted.is_(False))
    ).all()

    posting_skills: dict[int, set[int]] = {}
    for posting_id, skill_id in rows:
        posting_skills.setdefault(posting_id, set()).add(skill_id)

    eligible = {pid: skills for pid, skills in posting_skills.items() if len(skills) >= min_required_skills}

    bin_count = 100 // bin_size
    bins = [0] * bin_count
    matched = 0
    coverages: list[float] = []
    for skills in eligible.values():
        pct = len(skills & owned_skill_ids) / len(skills) * 100
        coverages.append(pct)
        bin_index = min(int(pct // bin_size), bin_count - 1)
        bins[bin_index] += 1
        if pct >= threshold:
            matched += 1

    total = len(eligible)

    coverage_score = calculate_coverage_response(
        session=session,
        pool=pool,
        position=position,
        owned_skill_ids=owned_skill_ids,
    ).coverage_score

    my_percentile = round(sum(1 for c in coverages if c <= coverage_score) / total * 100, 1) if total else 0.0

    return MatchCoverageDistributionResponse(
        pool=pool,
        coverage_score=coverage_score,
        histogram=[{"range_start": i * bin_size, "count": count} for i, count in enumerate(bins)],
        my_percentile=my_percentile,
        matched=matched,
        total=total,
        threshold=threshold,
        as_of=get_pool_as_of(session=session, pool=pool, position=position),
        sample_size=total,
        sample_warning=True if total < 50 else None,
        note=f"요구기술 {min_required_skills}개 이상 공고만 집계 · 히스토그램 bin={bin_size}%",
    )


def calculate_roadmap_response(
    session: Session,
    *,
    pool: Pool,
    position: str | None,
    owned_skill_ids: set[int],
    steps: int = 5,
    threshold: float = 50.0,
    candidate_pool_size: int = 30,
) -> MatchRoadmapResponse:
    """미보유 기술 중 매 단계 매칭 공고 수를 가장 많이 늘리는 기술을 탐욕적으로 선택. widgets 'y1-learning-path' 정식화."""
    market_skills, sample_size = get_market_skill_frequencies(session=session, pool=pool, position=position)
    candidates = {
        s["skill_id"]: s for s in market_skills if s["skill_id"] not in owned_skill_ids
    }
    candidates = dict(list(candidates.items())[:candidate_pool_size])

    current_owned = set(owned_skill_ids)
    matched_before = count_matched_postings(session=session, pool=pool, position=position, skill_ids=current_owned)
    start_matched = matched_before

    step_results = []
    for step_no in range(1, steps + 1):
        if not candidates:
            break

        best_skill_id = None
        best_matched_after = matched_before
        for skill_id in candidates:
            matched_after = count_matched_postings(
                session=session, pool=pool, position=position, skill_ids=current_owned | {skill_id}
            )
            if matched_after > best_matched_after:
                best_matched_after = matched_after
                best_skill_id = skill_id

        if best_skill_id is None:
            break

        chosen = candidates.pop(best_skill_id)
        current_owned.add(best_skill_id)
        step_results.append(
            {
                "step": step_no,
                "canonical": chosen["canonical"],
                "category": chosen["category"],
                "matched_after": best_matched_after,
                "delta": best_matched_after - matched_before,
                "freq": round(chosen["freq"], 4),
            }
        )
        matched_before = best_matched_after

    return MatchRoadmapResponse(
        pool=pool,
        start_matched=start_matched,
        total=sample_size,
        threshold=threshold,
        steps=step_results,
        as_of=get_pool_as_of(session=session, pool=pool, position=position),
        sample_size=sample_size,
        sample_warning=True if sample_size < 50 else None,
    )


def get_industry_skill_frequencies(session: Session, pool: Pool, industry: str) -> tuple[list[dict], int]:
    base_filters = [Posting.pool == pool, Posting.industry == industry, Posting.is_deleted.is_(False)]

    sample_size = session.scalar(select(func.count()).select_from(Posting).where(*base_filters)) or 0
    if sample_size == 0:
        return [], 0

    rows = session.execute(
        select(
            Skill.id.label("skill_id"),
            Skill.canonical,
            Skill.category,
            (func.count(distinct(PostingTech.posting_id)) / sample_size).label("freq"),
        )
        .select_from(Posting)
        .join(PostingTech, PostingTech.posting_id == Posting.id)
        .join(Skill, Skill.id == PostingTech.skill_id)
        .where(*base_filters, PostingTech.is_deleted.is_(False), Skill.is_deleted.is_(False))
        .group_by(Skill.id, Skill.canonical, Skill.category)
        .order_by(func.count(distinct(PostingTech.posting_id)).desc())
    ).all()

    return [
        {"skill_id": r.skill_id, "canonical": r.canonical, "category": r.category, "freq": float(r.freq)}
        for r in rows
    ], sample_size


def get_category_targets(session: Session, pool: Pool, limit: int) -> list[tuple[str, int]]:
    rows = session.execute(
        select(PostingCategory.category, func.count(distinct(Posting.id)).label("n"))
        .select_from(Posting)
        .join(PostingCategory, PostingCategory.posting_id == Posting.id)
        .join(JobCategory, JobCategory.name == PostingCategory.category)
        .where(
            Posting.pool == pool,
            Posting.is_deleted.is_(False),
            PostingCategory.is_deleted.is_(False),
            JobCategory.is_tech.is_(True),
            JobCategory.is_deleted.is_(False),
        )
        .group_by(PostingCategory.category)
        .order_by(func.count(distinct(Posting.id)).desc())
        .limit(limit)
    ).all()
    return [(row.category, row.n) for row in rows]


def get_industry_targets(session: Session, pool: Pool, limit: int) -> list[tuple[str, int]]:
    rows = session.execute(
        select(Posting.industry, func.count().label("n"))
        .where(Posting.pool == pool, Posting.industry.isnot(None), Posting.is_deleted.is_(False))
        .group_by(Posting.industry)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [(row.industry, row.n) for row in rows]


def calculate_pivot_map_response(
    session: Session,
    *,
    pool: Pool,
    owned_skill_ids: set[int],
    kind: str = "both",
    limit: int = 10,
    top_k_skills: int = 15,
) -> MatchPivotMapResponse:
    """직군·산업별 상위 요구기술 대비 내 커버리지("커리어 피벗 맵"). widgets 'y2-pivot-map' 정식화."""
    targets_out: list[dict] = []
    total_sample = 0

    def _build_target(name: str, n: int, kind_label: str, skills: list[dict]) -> dict:
        top = skills[:top_k_skills]
        owned = sum(1 for s in top if s["skill_id"] in owned_skill_ids)
        missing = [
            {"canonical": s["canonical"], "freq": round(s["freq"], 4)}
            for s in top
            if s["skill_id"] not in owned_skill_ids
        ]
        coverage = round(owned / len(top) * 100, 1) if top else 0.0
        return {"name": name, "kind": kind_label, "coverage": coverage, "missing": missing, "n": n}

    if kind in ("category", "both"):
        for name, n in get_category_targets(session, pool, limit):
            skills, _ = get_market_skill_frequencies(session=session, pool=pool, position=name)
            targets_out.append(_build_target(name, n, "category", skills))
            total_sample += n

    if kind in ("industry", "both"):
        for name, n in get_industry_targets(session, pool, limit):
            skills, _ = get_industry_skill_frequencies(session, pool, name)
            targets_out.append(_build_target(name, n, "industry", skills))
            total_sample += n

    targets_out.sort(key=lambda t: t["coverage"], reverse=True)

    return MatchPivotMapResponse(
        pool=pool,
        targets=targets_out,
        as_of=date.today().isoformat(),
        sample_size=total_sample,
    )
