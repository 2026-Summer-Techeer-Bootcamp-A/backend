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
from app.services.job_category import resolve_job_category

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
        # position은 posting_category.category와 정확히 일치시키면 안 된다. 프론트가
        # 보내는 'backend'/'frontend'/'devops'/'data' 같은 토큰은 실제 category 값
        # ('서버/백엔드 개발자' 등)과 문자열이 달라 exact match로는 절대 안 걸린다
        # (app/crud/insight.py get_skill_share의 mv_skill_share.position 버그와 같은
        # 종류의 문제). RAG sql_tool과 동일하게 안전한 ILIKE 부분 문자열 토큰으로 해소한다.
        # 알 수 없는 position은 0건으로 단정하지 않고 필터를 걸지 않는다(정직하게 폴백).
        category_token = resolve_job_category(position)
        if category_token is not None:
            # ILIKE 토큰은 한 posting에 붙은 여러 posting_category 행 중 2개 이상과
            # 동시에 매칭될 수 있어(예: "서버/백엔드 개발자"와 "백엔드개발자"를 함께 태그한
            # 공고), exact match였던 이전 버전과 달리 distinct 없이는 posting.id가
            # 중복될 수 있다. 이 서브쿼리는 이후 COUNT/JOIN에 그대로 쓰이므로 distinct로
            # posting 단위 유일성을 보장한다.
            query = (
                query.join(PostingCategory, PostingCategory.posting_id == Posting.id)
                .where(
                    PostingCategory.category.ilike(f"%{category_token}%"),
                    PostingCategory.is_deleted.is_(False),
                )
                .distinct()
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

def _dedupe_sorted_ci(names: list[str]) -> list[str]:
    """대소문자만 다른 중복 스킬명을 하나로 합치고(먼저 나온 표기를 채택), 결과 순서가
    호출마다 흔들리지 않도록 소문자 기준으로 안정 정렬한다. DB 접근이 없는 순수 함수라
    단위테스트로 집합 연산만 따로 검증할 수 있다."""
    seen: dict[str, str] = {}
    for name in names:
        key = name.lower()
        if key not in seen:
            seen[key] = name
    return sorted(seen.values(), key=str.lower)


def get_posting_skill_names(session: Session, posting_id: int) -> tuple[str, list[str]]:
    """공고 제목과 요구 기술 정규명 목록(중복 제거·정렬)을 반환한다. posting_tech -> skill
    조인은 app/crud/posting.py get_similar_postings가 쓰는 패턴을 그대로 재사용한다.
    공고가 없거나 삭제됐으면 404 — 호출부(compare_tool)에서 그 예외를 잡아 '비교할 공고를
    찾지 못했어요' 같은 안내로 바꿔 쓴다."""
    title = session.scalar(
        select(Posting.title).where(
            Posting.id == posting_id,
            Posting.is_deleted.is_(False),
        )
    )
    if title is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="posting not found")

    rows = session.scalars(
        select(Skill.canonical)
        .join(PostingTech, PostingTech.skill_id == Skill.id)
        .where(
            PostingTech.posting_id == posting_id,
            PostingTech.is_deleted.is_(False),
            Skill.is_deleted.is_(False),
        )
    ).all()

    return title, _dedupe_sorted_ci(list(rows))


def _build_resume_posting_compare(
    *,
    resume_title: str,
    posting_title: str,
    owned_names: list[str],
    posting_skills: list[str],
) -> dict:
    """이력서 보유 기술과 공고 요구 기술의 겹침/부족/여분을 순수 집합 연산으로 계산한다.
    DB 세션이 필요 없는 형태로 분리해 단위테스트가 쉽게 만들었다."""
    owned_by_key = {name.lower(): name for name in owned_names}
    posting_by_key = {name.lower(): name for name in posting_skills}
    owned_keys = set(owned_by_key)
    posting_keys = set(posting_by_key)

    matched = _dedupe_sorted_ci([posting_by_key[k] for k in owned_keys & posting_keys])
    missing = _dedupe_sorted_ci([posting_by_key[k] for k in posting_keys - owned_keys])
    extra = _dedupe_sorted_ci([owned_by_key[k] for k in owned_keys - posting_keys])

    coverage_pct = round(100 * len(matched) / len(posting_keys), 1) if posting_keys else 0.0

    return {
        "resume_title": resume_title,
        "posting_title": posting_title,
        "coverage_pct": coverage_pct,
        "matched_skills": matched,
        "missing_skills": missing,
        "extra_skills": extra,
    }


def compare_resume_to_posting(
    session: Session,
    *,
    owned_skill_ids: set[int],
    posting_id: int,
) -> dict:
    """이력서 보유 기술 vs 공고 요구 기술 딥 비교(K2). 공고가 없으면 get_posting_skill_names가
    404를 던진다 — 여기서 잡지 않고 그대로 올려보내 호출부(compare_tool)가 '공고 없음'과
    '이력서 없음'을 구분해 처리하게 한다."""
    posting_title, posting_skills = get_posting_skill_names(session, posting_id)

    owned_names: list[str] = []
    if owned_skill_ids:
        owned_names = list(
            session.scalars(
                select(Skill.canonical).where(
                    Skill.id.in_(owned_skill_ids),
                    Skill.is_deleted.is_(False),
                )
            ).all()
        )

    return _build_resume_posting_compare(
        resume_title="내 이력서",
        posting_title=posting_title,
        owned_names=owned_names,
        posting_skills=posting_skills,
    )


def _build_posting_posting_compare(
    *,
    title_a: str,
    skills_a: list[str],
    title_b: str,
    skills_b: list[str],
) -> dict:
    """두 공고 요구 기술의 겹침/차이를 순수 집합 연산으로 계산한다(DB 세션 불필요)."""
    a_by_key = {name.lower(): name for name in skills_a}
    b_by_key = {name.lower(): name for name in skills_b}
    a_keys, b_keys = set(a_by_key), set(b_by_key)

    shared = _dedupe_sorted_ci([a_by_key[k] for k in a_keys & b_keys])
    only_a = _dedupe_sorted_ci([a_by_key[k] for k in a_keys - b_keys])
    only_b = _dedupe_sorted_ci([b_by_key[k] for k in b_keys - a_keys])

    return {
        "postingA": title_a,
        "postingB": title_b,
        "shared": shared,
        "onlyA": only_a,
        "onlyB": only_b,
    }


def compare_two_postings(
    session: Session,
    *,
    posting_id_a: int,
    posting_id_b: int,
) -> dict:
    """공고 vs 공고 요구 기술 딥 비교(K2). 둘 중 하나라도 없으면 get_posting_skill_names가
    404를 던지며 그대로 전파된다."""
    title_a, skills_a = get_posting_skill_names(session, posting_id_a)
    title_b, skills_b = get_posting_skill_names(session, posting_id_b)
    return _build_posting_posting_compare(
        title_a=title_a, skills_a=skills_a, title_b=title_b, skills_b=skills_b
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
