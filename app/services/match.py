from datetime import date, timedelta
from math import ceil, log1p

from fastapi import HTTPException, status
from pydantic import BaseModel
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
from app.core.config import settings
from app.core.redis import get_resume_confirm_session
from app.services.job_category import resolve_job_category
from app.services.reference_cache import get_cached, make_reference_cache_key, set_cached

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


# 시장 통계(스킬 점유율/커버리지/gap/roadmap/pivot-map/resume_market/resume_recommend)의
# 모수 정의. 예전에는 "마감 전 공고만"이었는데, 국내 백엔드 시장에서 마감된 공고가
# 압도적으로 많아 그 기준으로는 표본이 300여 건까지 쪼그라들어 통계적 대표성이 없었다.
# 그래서 마감 여부와 무관하게 "최근 N년 이내 게시"로만 모수를 다시 정의한다 — 대신
# 너무 오래된(트렌드가 바뀐 스킬을 담고 있을) 공고는 배제한다.
MARKET_WINDOW_DAYS = 365 * 3


def market_pool_cutoff_date() -> date:
    """시장 모수(coverage/gap/skill-share/resume 매칭 계열)의 3년 컷오프 날짜.

    윤년 경계에서 date(year-3, month, day)가 존재하지 않는 경우(2/29)를 피하려고
    relativedelta 대신 고정 일수(365*3) timedelta를 쓴다."""
    return date.today() - timedelta(days=MARKET_WINDOW_DAYS)


def build_posting_pool_query(
    pool: Pool,
    position: str | None,
    *,
    only_open: bool = False,
    company: str | None = None,
) -> Select:
    query = select(Posting.id).where(
        Posting.pool == pool,
        Posting.is_deleted.is_(False),
        # "마감 전 공고만"에서 "최근 3년 이내 게시(마감 포함)"로 시장 모수를 바꿨다.
        # app/crud/posting.py의 _apply_posting_filters(공고 목록/지원 가능 여부)와는
        # 이제 기준이 다르다 — 그쪽은 여전히 "지금 지원 가능한 공고"를 뜻하는 별개의
        # 개념이라 건드리지 않는다. post_date가 없는 공고(수집 당시 날짜 파싱 실패 등)를
        # 이 필터로 조용히 잃지 않도록 NULL은 포함한다.
        Posting.post_date.is_(None) | (Posting.post_date >= market_pool_cutoff_date()),
    )

    if only_open:
        # 전략 엔진(coverage/gap/roadmap/what-if/pivot/distribution)은 "시장에 어떤 기술이
        # 얼마나 흔한가"가 아니라 "지금 지원 가능한 공고 중 몇 건이 나에게 걸리는가"를
        # 답해야 하는데, 위의 3년 윈도우만으로는 이미 마감된 공고까지 모수에 섞여 매칭
        # 건수가 실제 지원 가능 건수보다 크게 부풀려진다. app/crud/posting.py의 공고
        # 목록 조회(_apply_posting_filters)와 동일하게 마감일이 없거나 오늘 이후인
        # 공고만 남긴다.
        query = query.where(
            Posting.close_date.is_(None) | (Posting.close_date >= date.today())
        )

    if company:
        # 목표 기업 갭 분석(A-1)용. 회사명은 수집 소스마다 표기가 갈릴 수 있어(예: "네이버"
        # vs "NAVER") 느슨한 ILIKE로 부분 일치시킨다. position의 category_token과 달리
        # posting_category 조인이 끼지 않아 distinct가 별도로 필요하지 않다.
        query = query.where(Posting.company.ilike(f"%{company}%"))

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


class _MarketSkillFrequenciesCache(BaseModel):
    """get_market_skill_frequencies 결과를 Redis에 담기 위한 얇은 래퍼.

    반환값이 (list[dict], int) 튜플이라 reference_cache의 get_cached/set_cached가
    요구하는 pydantic 모델 형태로 감싸야 한다. 이 스킬은 이력서와 무관하게 pool과
    position, only_open 조합에만 의존해 roadmap/coverage/gap/distribution/pivot-map이
    모두 같은 값을 공유하므로 요청마다 재계산하지 않고 캐시 하나를 나눠 쓴다."""

    skills: list[dict]
    sample_size: int


def get_market_skill_frequencies(
    session: Session,
    pool: Pool,
    position: str | None,
    *,
    only_open: bool = False,
    company: str | None = None,
) -> tuple[list[dict], int]:
    cache_key = make_reference_cache_key(
        "match_market_skill_frequencies",
        {"pool": pool, "position": position, "only_open": only_open, "company": company},
    )
    cached = get_cached(cache_key, _MarketSkillFrequenciesCache)
    if cached is not None:
        return cached.skills, cached.sample_size

    posting_pool_query = build_posting_pool_query(
        pool=pool, position=position, only_open=only_open, company=company
    ).subquery()

    sample_size = session.scalar(
        select(func.count()).select_from(posting_pool_query)
    ) or 0

    if sample_size == 0:
        set_cached(cache_key, _MarketSkillFrequenciesCache(skills=[], sample_size=0), settings.stats_cache_ttl_seconds)
        return [], 0

    rows = session.execute(
        select(
            Skill.id.label("skill_id"),
            Skill.canonical,
            Skill.category,
            func.count(distinct(PostingTech.posting_id)).label("posting_count"),
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

    result_skills = [
        {
            "skill_id": row.skill_id,
            "canonical": row.canonical,
            "category": row.category,
            "posting_count": row.posting_count,
            "freq": float(row.freq),
        }
        for row in rows
    ]

    set_cached(
        cache_key,
        _MarketSkillFrequenciesCache(skills=result_skills, sample_size=sample_size),
        settings.stats_cache_ttl_seconds,
    )
    return result_skills, sample_size


FORMULA_VERSION = "weighted-v1"
CORE_RATIO = 0.2
PENALTY_STRENGTH = 0.4
DEFAULT_TARGET_SKILL_LIMIT = 20


def select_target_skills(
    market_skills: list[dict],
    limit: int = DEFAULT_TARGET_SKILL_LIMIT,
) -> list[dict]:
    """Select the shared, frequency-ranked skill universe for coverage and gap."""
    return market_skills[:limit]


def calculate_match_score(
    owned_skill_ids: set[int],
    target_skills: list[dict],
    *,
    core_ratio: float = CORE_RATIO,
    penalty_strength: float = PENALTY_STRENGTH,
) -> dict:
    """Return a deterministic weighted score without database dependencies."""
    if not target_skills:
        return {"score": 0.0, "base_score": 0.0, "core_missing_penalty": 0.0, "skills": []}

    raw_weights = [log1p(max(0, skill.get("posting_count", 0))) for skill in target_skills]
    total_weight = sum(raw_weights)
    if total_weight <= 0:
        raw_weights = [1.0] * len(target_skills)
        total_weight = float(len(target_skills))

    core_count = max(1, ceil(len(target_skills) * core_ratio))
    weights = [value / total_weight for value in raw_weights]
    base_score = 100 * sum(
        weight for skill, weight in zip(target_skills, weights)
        if skill["skill_id"] in owned_skill_ids
    )
    core_weight = sum(weights[:core_count])
    missing_core_weight = sum(
        weight for skill, weight in zip(target_skills[:core_count], weights[:core_count])
        if skill["skill_id"] not in owned_skill_ids
    )
    missing_core_ratio = missing_core_weight / core_weight if core_weight else 0.0
    score = base_score * (1 - penalty_strength * missing_core_ratio)
    penalty = base_score - score

    explained = []
    for index, (skill, weight) in enumerate(zip(target_skills, weights)):
        owned = skill["skill_id"] in owned_skill_ids
        is_core = index < core_count
        explained.append({
            **skill,
            "weight": weight,
            "tier": "core" if is_core else "supporting",
            "owned": owned,
            "score_contribution": 100 * weight if owned else 0.0,
            "penalty_contribution": (
                base_score * penalty_strength * weight / core_weight
                if is_core and not owned and core_weight else 0.0
            ),
        })
    return {
        "score": round(score, 1),
        "base_score": round(base_score, 1),
        "core_missing_penalty": round(penalty, 1),
        "skills": explained,
    }


def calculate_gap_response(
    session: Session,
    *,
    pool: Pool,
    position: str | None,
    owned_skill_ids: set[int],
    company: str | None = None,
    only_open: bool = False,
) -> MatchGapResponse:
    """이력서 대비 시장 요구기술 갭. company가 주어지면(A-1) 시장 전체가 아니라 그 기업의
    열린 공고만을 모수로 좁혀 '이 기업에 지원하려면 뭘 배워야 하는가'를 답한다. 나머지
    계산(가중 점수, 카테고리 레이더, 스킬별 학습 효과)은 pool 자체를 좁힌 것 외에는
    기존 로직을 그대로 재사용한다.

    only_open은 호출부가 정한다. 대시보드 엔드포인트는 마감된 공고를 제외하려고
    True를 넘기고, RAG 도구(resume_tool)는 "최근 3년 · 마감 포함" 계약을 지키려고
    기본값 False를 그대로 쓴다."""
    market_skills, sample_size = get_market_skill_frequencies(
        session=session,
        pool=pool,
        position=position,
        only_open=only_open,
        company=company,
    )
    target_skills = select_target_skills(market_skills)
    weighted = calculate_match_score(owned_skill_ids, target_skills)
    weighted_by_id = {skill["skill_id"]: skill for skill in weighted["skills"]}
    impact_items = []
    for skill in target_skills:
        if skill["skill_id"] in owned_skill_ids:
            continue
        after = calculate_match_score(owned_skill_ids | {skill["skill_id"]}, target_skills)
        detail = weighted_by_id[skill["skill_id"]]
        impact_items.append({
            "canonical": skill["canonical"],
            "posting_count": skill["posting_count"],
            "frequency": round(skill["freq"], 4),
            "weight": round(detail["weight"], 6),
            "tier": detail["tier"],
            "score_gain_if_owned": round(after["score"] - weighted["score"], 1),
            "unlocked_posting_count": skill["posting_count"],
            "reason": f"선택 시장 공고의 {round(skill['freq'] * 100)}%에서 요구되는 {'핵심' if detail['tier'] == 'core' else '보조'} 기술",
        })
    impact_items.sort(key=lambda item: (-item["score_gain_if_owned"], -item["posting_count"], item["canonical"]))

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
        current_score=weighted["score"],
        items=impact_items[:10],
        formula_version=FORMULA_VERSION,
        company=company,
    )

def calculate_coverage_response(
    session: Session,
    *,
    pool: Pool,
    position: str | None,
    owned_skill_ids: set[int],
    top_k: int = 20,
    only_open: bool = False,
) -> MatchCoverageResponse:
    market_skills, sample_size = get_market_skill_frequencies(
        session=session,
        pool=pool,
        position=position,
        only_open=only_open,
    )

    top_skills = select_target_skills(market_skills, top_k)
    weighted = calculate_match_score(owned_skill_ids, top_skills)
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
                "frequency": round(skill["freq"], 4),
                "posting_count": skill["posting_count"],
                "owned": skill["owned"],
                "weight": round(skill["weight"], 6),
                "tier": skill["tier"],
                "score_contribution": round(skill["score_contribution"], 1),
                "penalty_contribution": round(skill["penalty_contribution"], 1),
            }
            for skill in weighted["skills"]
        ],
        owned_count=owned_count,
        as_of=date.today().isoformat(),
        sample_size=sample_size,
        sample_warning=sample_size < 50,
        score=weighted["score"],
        base_score=weighted["base_score"],
        core_missing_penalty=weighted["core_missing_penalty"],
        formula_version=FORMULA_VERSION,
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
    only_open: bool = False,
) -> str:
    posting_pool_query = build_posting_pool_query(
        pool=pool, position=position, only_open=only_open
    ).subquery()

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


class _CountCache(BaseModel):
    """count_matched_postings 결과(int)를 Redis에 담기 위한 얇은 래퍼. what-if(P3)에서
    matched_before(이력서당 동일)와 matched_after가 이 캐시를 공유해, 같은 이력서로
    칩을 여러 개 눌러도 matched_before는 한 번만 계산되고 재클릭은 완전히 캐시된다."""

    count: int


def count_matched_postings(
    session: Session,
    *,
    pool: Pool,
    position: str | None,
    skill_ids: set[int],
    only_open: bool = False,
) -> int:
    if not skill_ids:
        return 0

    cache_key = make_reference_cache_key(
        "match_count_matched_postings",
        {
            "pool": pool,
            "position": position,
            "only_open": only_open,
            "skill_ids": sorted(skill_ids),
        },
    )
    cached = get_cached(cache_key, _CountCache)
    if cached is not None:
        return cached.count

    posting_pool_query = build_posting_pool_query(
        pool=pool, position=position, only_open=only_open
    ).subquery()

    count = session.scalar(
        select(func.count(distinct(PostingTech.posting_id)))
        .join(posting_pool_query, posting_pool_query.c.id == PostingTech.posting_id)
        .where(
            PostingTech.skill_id.in_(skill_ids),
            PostingTech.is_deleted.is_(False),
        )
    ) or 0

    set_cached(cache_key, _CountCache(count=count), settings.stats_cache_ttl_seconds)
    return count


def calculate_what_if_response(
    session: Session,
    *,
    pool: Pool,
    add: str,
    owned_skill_ids: set[int],
    position: str | None = None,
    only_open: bool = False,
) -> MatchWhatIfResponse:
    add_skill_id, add_canonical = get_skill_id_by_canonical(session=session, canonical=add)

    posting_pool_query = build_posting_pool_query(
        pool=pool, position=position, only_open=only_open
    ).subquery()
    sample_size = session.scalar(select(func.count()).select_from(posting_pool_query)) or 0

    matched_before = count_matched_postings(
        session=session,
        pool=pool,
        position=position,
        skill_ids=owned_skill_ids,
        only_open=only_open,
    )

    matched_after = count_matched_postings(
        session=session,
        pool=pool,
        position=position,
        skill_ids=owned_skill_ids | {add_skill_id},
        only_open=only_open,
    )

    return MatchWhatIfResponse(
        add=add_canonical,
        matched_before=matched_before,
        matched_after=matched_after,
        delta=matched_after - matched_before,
        as_of=get_pool_as_of(session=session, pool=pool, position=position, only_open=only_open),
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
    only_open: bool = False,
) -> MatchCoverageDistributionResponse:
    """공고별(요구기술 min_required_skills개 이상) 커버리지 분포 히스토그램. widgets 'c-coverage-dist' 정식화."""
    cache_key = make_reference_cache_key(
        "match_coverage_distribution",
        {
            "pool": pool,
            "position": position,
            "threshold": threshold,
            "min_required_skills": min_required_skills,
            "bin_size": bin_size,
            "only_open": only_open,
            "owned": sorted(owned_skill_ids),
        },
    )
    cached = get_cached(cache_key, MatchCoverageDistributionResponse)
    if cached is not None:
        return cached

    posting_pool_query = build_posting_pool_query(
        pool=pool, position=position, only_open=only_open
    ).subquery()

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
        only_open=only_open,
    ).coverage_score

    my_percentile = round(sum(1 for c in coverages if c <= coverage_score) / total * 100, 1) if total else 0.0

    response = MatchCoverageDistributionResponse(
        pool=pool,
        coverage_score=coverage_score,
        histogram=[{"range_start": i * bin_size, "count": count} for i, count in enumerate(bins)],
        my_percentile=my_percentile,
        matched=matched,
        total=total,
        threshold=threshold,
        as_of=get_pool_as_of(session=session, pool=pool, position=position, only_open=only_open),
        sample_size=total,
        sample_warning=True if total < 50 else None,
        note=f"요구기술 {min_required_skills}개 이상 공고만 집계 · 히스토그램 bin={bin_size}%",
    )
    set_cached(cache_key, response, settings.stats_cache_ttl_seconds)
    return response


def build_pool_skill_index(session: Session, posting_pool_subquery) -> dict[int, set[int]]:
    """posting_pool_subquery(.c.id 컬럼 보유)에 속한 공고들의 posting_id -> 요구 skill_id
    집합 맵을 단 한 번의 쿼리로 만든다. calculate_coverage_distribution_response가 쓰던
    것과 같은 (posting_id, skill_id) 스트리밍 패턴이다. roadmap 계열(전체 풀 로드맵과
    커밋3의 북마크 스코프 로드맵)이 이 인덱스를 공유해, 후보 기술 하나하나마다 COUNT
    쿼리를 다시 던지지 않고 그리디 선택을 메모리에서 수행할 수 있게 한다."""
    rows = session.execute(
        select(PostingTech.posting_id, PostingTech.skill_id)
        .join(posting_pool_subquery, posting_pool_subquery.c.id == PostingTech.posting_id)
        .where(PostingTech.is_deleted.is_(False))
    ).all()

    posting_skills: dict[int, set[int]] = {}
    for posting_id, skill_id in rows:
        posting_skills.setdefault(posting_id, set()).add(skill_id)
    return posting_skills


def greedy_roadmap_steps(
    posting_skills: dict[int, set[int]],
    owned_skill_ids: set[int],
    candidates: dict[int, dict],
    steps: int,
) -> tuple[int, list[dict]]:
    """posting_id -> 요구 skill 집합 인덱스만 가지고 메모리에서 그리디 학습 순서를 고른다.
    '매칭'의 정의는 count_matched_postings와 동일하게 보유 기술 중 하나라도 그 공고의
    요구 기술과 겹치면 매칭으로 센다(요구기술 대비 커버리지 비율이 아니다). skill_id ->
    posting_id 역인덱스를 만들어두면, 각 단계에서 후보 기술이 새로 매칭시키는 공고 수를
    '아직 매칭 안 된 공고 집합과의 교집합 크기'로 바로 구할 수 있어 매 단계 전체 공고를
    다시 훑지 않아도 된다. DB 세션이 필요 없어 전체 풀 로드맵과 북마크 스코프 로드맵이
    이 함수를 그대로 공유한다."""
    skill_to_postings: dict[int, set[int]] = {}
    for posting_id, skills in posting_skills.items():
        for skill_id in skills:
            skill_to_postings.setdefault(skill_id, set()).add(posting_id)

    current_owned = set(owned_skill_ids)
    matched_ids: set[int] = set()
    for skill_id in current_owned:
        matched_ids |= skill_to_postings.get(skill_id, set())
    matched_before = len(matched_ids)
    start_matched = matched_before
    unmatched_ids = set(posting_skills.keys()) - matched_ids

    remaining_candidates = dict(candidates)
    step_results: list[dict] = []
    for step_no in range(1, steps + 1):
        if not remaining_candidates:
            break

        best_skill_id = None
        best_gain_ids: set[int] = set()
        best_matched_after = matched_before
        for skill_id in remaining_candidates:
            gain_ids = skill_to_postings.get(skill_id, set()) & unmatched_ids
            matched_after = matched_before + len(gain_ids)
            if matched_after > best_matched_after:
                best_matched_after = matched_after
                best_skill_id = skill_id
                best_gain_ids = gain_ids

        if best_skill_id is None:
            break

        chosen = remaining_candidates.pop(best_skill_id)
        current_owned.add(best_skill_id)
        unmatched_ids -= best_gain_ids
        step_results.append(
            {
                "step": step_no,
                "canonical": chosen["canonical"],
                "category": chosen["category"],
                "matched_after": best_matched_after,
                "delta": best_matched_after - matched_before,
                "freq": round(chosen.get("freq", 0.0), 4),
            }
        )
        matched_before = best_matched_after

    return start_matched, step_results


def calculate_roadmap_response(
    session: Session,
    *,
    pool: Pool,
    position: str | None,
    owned_skill_ids: set[int],
    steps: int = 5,
    threshold: float = 50.0,
    candidate_pool_size: int = 30,
    only_open: bool = False,
) -> MatchRoadmapResponse:
    """미보유 기술 중 매 단계 매칭 공고 수를 가장 많이 늘리는 기술을 탐욕적으로 선택. widgets 'y1-learning-path' 정식화.

    예전에는 후보 기술(최대 30개) x 단계(최대 5)마다 count_matched_postings로 COUNT 쿼리를
    새로 던져 요청 하나에 최대 150회 가까운 DB 왕복이 들었다. 공고 -> 요구 skill_id 집합을
    build_pool_skill_index로 한 번만 읽어와 greedy_roadmap_steps가 메모리에서 그리디 선택을
    끝내도록 바꿔, DB 쿼리 수를 요청당 상수 개(시장 스킬 빈도 조회 + 인덱스 조회 + as_of)로
    줄였다."""
    market_skills, sample_size = get_market_skill_frequencies(
        session=session, pool=pool, position=position, only_open=only_open
    )
    candidates = {
        s["skill_id"]: s for s in market_skills if s["skill_id"] not in owned_skill_ids
    }
    candidates = dict(list(candidates.items())[:candidate_pool_size])

    posting_pool_query = build_posting_pool_query(pool=pool, position=position, only_open=only_open).subquery()
    posting_skills = build_pool_skill_index(session, posting_pool_query)

    start_matched, step_results = greedy_roadmap_steps(
        posting_skills, owned_skill_ids, candidates, steps
    )

    return MatchRoadmapResponse(
        pool=pool,
        start_matched=start_matched,
        total=sample_size,
        threshold=threshold,
        steps=step_results,
        as_of=get_pool_as_of(session=session, pool=pool, position=position, only_open=only_open),
        sample_size=sample_size,
        sample_warning=True if sample_size < 50 else None,
    )


DEFAULT_SCOPED_ROADMAP_CANDIDATE_LIMIT = 30


def calculate_scoped_roadmap_response(
    session: Session,
    *,
    posting_ids: list[int],
    owned_skill_ids: set[int],
    steps: int = 5,
    threshold: float = 50.0,
) -> MatchRoadmapResponse:
    """북마크한 공고 id 목록만을 모수로 하는 로드맵(A-5). calculate_roadmap_response가
    pool 전체 시장을 모수로 "시장에서 가장 많이 요구되는 기술부터" 추천한다면, 이쪽은
    "지금 찜해둔 공고들 중 이 기술을 배우면 몇 개가 새로 지원 가능해지는가"를 답한다.
    posting_ids로 직접 범위를 지정하므로 pool/position 필터가 필요 없고, 커밋2에서 뽑아둔
    build_pool_skill_index/greedy_roadmap_steps를 그대로 재사용해 별도 로직을 새로
    만들지 않는다."""
    if not posting_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="posting_ids must not be empty",
        )

    pool_rows = session.execute(
        select(Posting.id, Posting.pool).where(
            Posting.id.in_(posting_ids),
            Posting.is_deleted.is_(False),
            # 로드맵은 "지금 지원 가능한가"를 답해야 하므로 북마크 중 이미 마감된 공고는
            # 모수에서 제외한다 — 커밋1에서 도입한 only_open 필터와 동일한 기준이다.
            Posting.close_date.is_(None) | (Posting.close_date >= date.today()),
        )
    ).all()

    if not pool_rows:
        today = date.today().isoformat()
        return MatchRoadmapResponse(
            pool="domestic",
            start_matched=0,
            total=0,
            threshold=threshold,
            steps=[],
            as_of=today,
            sample_size=0,
            sample_warning=True,
        )

    valid_ids = [row.id for row in pool_rows]
    pool_counts: dict[str, int] = {}
    for row in pool_rows:
        if row.pool:
            pool_counts[row.pool] = pool_counts.get(row.pool, 0) + 1
    dominant_pool = max(pool_counts, key=pool_counts.get) if pool_counts else "domestic"
    if dominant_pool not in ("global", "domestic"):
        dominant_pool = "domestic"

    id_subquery = select(Posting.id).where(Posting.id.in_(valid_ids)).subquery()
    posting_skills = build_pool_skill_index(session, id_subquery)

    total_scoped = len(valid_ids)
    skill_posting_counts: dict[int, int] = {}
    for skills in posting_skills.values():
        for skill_id in skills:
            skill_posting_counts[skill_id] = skill_posting_counts.get(skill_id, 0) + 1

    candidate_skill_ids = [
        skill_id for skill_id in skill_posting_counts if skill_id not in owned_skill_ids
    ]
    skill_meta: dict[int, tuple[str, str]] = {}
    if candidate_skill_ids:
        skill_rows = session.execute(
            select(Skill.id, Skill.canonical, Skill.category).where(
                Skill.id.in_(candidate_skill_ids),
                Skill.is_deleted.is_(False),
            )
        ).all()
        skill_meta = {row.id: (row.canonical, row.category) for row in skill_rows}

    candidates = {
        skill_id: {
            "skill_id": skill_id,
            "canonical": skill_meta.get(skill_id, (str(skill_id), "기타"))[0],
            "category": skill_meta.get(skill_id, (str(skill_id), "기타"))[1],
            "freq": skill_posting_counts[skill_id] / total_scoped if total_scoped else 0.0,
        }
        for skill_id in candidate_skill_ids
        if skill_id in skill_meta
    }
    candidates = dict(
        sorted(candidates.items(), key=lambda kv: -kv[1]["freq"])[:DEFAULT_SCOPED_ROADMAP_CANDIDATE_LIMIT]
    )

    start_matched, step_results = greedy_roadmap_steps(
        posting_skills, owned_skill_ids, candidates, steps
    )

    return MatchRoadmapResponse(
        pool=dominant_pool,
        start_matched=start_matched,
        total=total_scoped,
        threshold=threshold,
        steps=step_results,
        as_of=date.today().isoformat(),
        sample_size=total_scoped,
        sample_warning=True if total_scoped < 50 else None,
    )


def get_industry_skill_frequencies(session: Session, pool: Pool, industry: str) -> tuple[list[dict], int]:
    """pivot-map(P1)이 산업별 상위 요구기술을 그릴 때 쓰는 참조 데이터. get_market_skill_frequencies와
    반환 형태가 같아(skills, sample_size) 같은 _MarketSkillFrequenciesCache 래퍼를 재사용한다.
    이력서와 무관한 시장 통계라 owned는 캐시 키에 넣지 않는다."""
    cache_key = make_reference_cache_key(
        "match_industry_skill_frequencies",
        {"pool": pool, "industry": industry},
    )
    cached = get_cached(cache_key, _MarketSkillFrequenciesCache)
    if cached is not None:
        return cached.skills, cached.sample_size

    base_filters = [Posting.pool == pool, Posting.industry == industry, Posting.is_deleted.is_(False)]

    sample_size = session.scalar(select(func.count()).select_from(Posting).where(*base_filters)) or 0
    if sample_size == 0:
        set_cached(cache_key, _MarketSkillFrequenciesCache(skills=[], sample_size=0), settings.stats_cache_ttl_seconds)
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

    result_skills = [
        {"skill_id": r.skill_id, "canonical": r.canonical, "category": r.category, "freq": float(r.freq)}
        for r in rows
    ]

    set_cached(
        cache_key,
        _MarketSkillFrequenciesCache(skills=result_skills, sample_size=sample_size),
        settings.stats_cache_ttl_seconds,
    )
    return result_skills, sample_size


class _TargetsCache(BaseModel):
    """get_category_targets/get_industry_targets 결과(list[tuple[str, int]])를 Redis에
    담기 위한 얇은 래퍼. JSON 직렬화를 거치면 tuple이 list가 되므로, 필드 타입을
    list[tuple[str, int]]로 선언해 pydantic이 역직렬화 시 다시 tuple로 복원하게 한다 —
    호출부가 for name, n in ... 언패킹을 그대로 쓸 수 있어야 하기 때문이다."""

    targets: list[tuple[str, int]]


def get_category_targets(session: Session, pool: Pool, limit: int) -> list[tuple[str, int]]:
    cache_key = make_reference_cache_key(
        "match_category_targets",
        {"pool": pool, "limit": limit},
    )
    cached = get_cached(cache_key, _TargetsCache)
    if cached is not None:
        return cached.targets

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
    targets = [(row.category, row.n) for row in rows]
    set_cached(cache_key, _TargetsCache(targets=targets), settings.stats_cache_ttl_seconds)
    return targets


def get_industry_targets(session: Session, pool: Pool, limit: int) -> list[tuple[str, int]]:
    cache_key = make_reference_cache_key(
        "match_industry_targets",
        {"pool": pool, "limit": limit},
    )
    cached = get_cached(cache_key, _TargetsCache)
    if cached is not None:
        return cached.targets

    rows = session.execute(
        select(Posting.industry, func.count().label("n"))
        .where(Posting.pool == pool, Posting.industry.isnot(None), Posting.is_deleted.is_(False))
        .group_by(Posting.industry)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    targets = [(row.industry, row.n) for row in rows]
    set_cached(cache_key, _TargetsCache(targets=targets), settings.stats_cache_ttl_seconds)
    return targets


def calculate_pivot_map_response(
    session: Session,
    *,
    pool: Pool,
    owned_skill_ids: set[int],
    kind: str = "both",
    limit: int = 10,
    top_k_skills: int = 15,
    only_open: bool = False,
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
            skills, _ = get_market_skill_frequencies(
                session=session, pool=pool, position=name, only_open=only_open
            )
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
