"""Stats/Trend 확장 인사이트 라우터.

프론트 `/widgets` 갤러리에만 있던 pearl 지표(a,h,o,p,r,x)를 정식 엔드포인트로 노출한다.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, Query

from app.core.deps import SessionDep
from app.crud.insight import (
    get_cooccurrence,
    get_global_domestic_gap,
    get_hiring_season,
    get_hot_companies,
    get_hype_vs_hire,
    get_industry_fingerprint,
    get_newcomer_gate,
    get_posting_timeline,
    get_region_density,
    get_response_rate,
    get_role_stack_fit,
    get_skill_share,
    get_skill_trend_yearly,
    get_skill_unlock,
)
from app.routers.match import resolve_optional_owned_skill_ids, resolve_owned_skill_ids
from app.schemas.insight import (
    CooccurrenceResponse,
    GlobalDomesticGapResponse,
    HiringSeasonResponse,
    HotCompaniesResponse,
    HypeVsHireResponse,
    IndustryFingerprintResponse,
    NewcomerGateResponse,
    PostingTimelineResponse,
    RegionDensityResponse,
    ResponseRateResponse,
    RoleStackFitResponse,
    SkillShareResponse,
    SkillTrendYearlyResponse,
    SkillUnlockResponse,
)
from app.schemas.posting import Pool

router = APIRouter()


@router.get("/trend/hype-vs-hire", response_model=HypeVsHireResponse)
def trend_hype_vs_hire(
    session: SessionDep,
    skill: Annotated[str, Query(description="canonical 기술명")],
) -> HypeVsHireResponse:
    """관심(HN 언급) vs 실수요(공고) 괴리를 분기별로 비교합니다. add가 taxonomy 밖이면 422."""
    result = get_hype_vs_hire(session=session, skill=skill)
    return HypeVsHireResponse(
        skill=result["skill"],
        quarters=result["quarters"],
        as_of=date.today().isoformat(),
        sample_size=result["sample_size"],
        note="관심=HN 월별 언급 합계 · 수요=분기별 공고 수(himalayas 제외)",
    )


@router.get("/stats/newcomer-gate", response_model=NewcomerGateResponse)
def stats_newcomer_gate(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 15,
) -> NewcomerGateResponse:
    """기술별 신입 채용 개방도(국내 전용). career_min<=0을 '신입 가능' 근사치로 사용합니다."""
    items, sample_size = get_newcomer_gate(session=session, limit=limit)
    return NewcomerGateResponse(
        items=items,
        as_of=date.today().isoformat(),
        sample_size=sample_size,
        sample_warning=sample_size < 50,
        note="jumpit의 newcomer 원본 플래그는 DB에 미적재 — career_min<=0을 근사치로 사용",
    )


@router.get("/stats/global-domestic-gap", response_model=GlobalDomesticGapResponse)
def stats_global_domestic_gap(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> GlobalDomesticGapResponse:
    """국내/해외 각 풀 내 기술 점유율을 비교합니다(절대 합산하지 않음)."""
    global_favored, domestic_favored, global_total, domestic_total = get_global_domestic_gap(
        session=session, limit=limit
    )
    return GlobalDomesticGapResponse(
        global_favored=global_favored,
        domestic_favored=domestic_favored,
        as_of=date.today().isoformat(),
        sample_size={"global": global_total, "domestic": domestic_total},
    )


@router.get("/stats/hiring-season", response_model=HiringSeasonResponse)
def stats_hiring_season(session: SessionDep) -> HiringSeasonResponse:
    """월별 채용 성수기 지수(=월별 건수/월평균). himalayas·진행 중인 올해는 제외합니다."""
    months, pool_totals = get_hiring_season(session=session)
    return HiringSeasonResponse(
        months=months,
        as_of=date.today().isoformat(),
        sample_size=pool_totals,
        note="himalayas(단일 스냅샷) 제외 · 진행 중인 올해 제외 · 지수=월별건수/월평균",
    )


@router.get("/stats/industry-fingerprint", response_model=IndustryFingerprintResponse)
def stats_industry_fingerprint(
    session: SessionDep,
    limit_industries: Annotated[int, Query(ge=1, le=20)] = 8,
    limit_skills: Annotated[int, Query(ge=1, le=20)] = 8,
) -> IndustryFingerprintResponse:
    """산업별 기술 지문(국내 전용). index=산업 내 비중÷전 산업 평균 비중."""
    industries, sample_size = get_industry_fingerprint(
        session=session, limit_industries=limit_industries, limit_skills=limit_skills
    )
    return IndustryFingerprintResponse(
        industries=industries,
        as_of=date.today().isoformat(),
        sample_size=sample_size,
        sample_warning=sample_size < 50,
        note="posting.industry는 jumpit text_rule 분류만 신뢰 가능(표본 얇음, 참고용)",
    )


@router.get("/stats/role-stack-fit", response_model=RoleStackFitResponse)
def stats_role_stack_fit(
    session: SessionDep,
    pool: Annotated[Pool | None, Query(description="global 또는 domestic. 미지정 시 전체")] = None,
    top_n_categories: Annotated[int, Query(ge=2, le=10)] = 6,
) -> RoleStackFitResponse:
    """직군간 요구 기술 벡터 유사도 매트릭스(0~100). job_category.is_tech=true인 직군만 대상."""
    categories, matrix, sample_size = get_role_stack_fit(
        session=session, pool=pool, top_n_categories=top_n_categories
    )
    return RoleStackFitResponse(
        categories=categories,
        matrix=matrix,
        as_of=date.today().isoformat(),
        sample_size=sample_size,
    )


@router.get("/stats/skill-share", response_model=SkillShareResponse)
def stats_skill_share(
    session: SessionDep,
    pool: Annotated[Pool, Query(description="global 또는 domestic")],
    position: Annotated[str | None, Query(description="job_category name. 미지정 시 전체 직군 합산")] = None,
    top_k: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SkillShareResponse:
    """풀(+직군) 내 기술 점유율. mv_skill_share 마트 기반, posting_count 내림차순 top_k."""
    items, sample_size = get_skill_share(session=session, pool=pool, position=position, top_k=top_k)
    return SkillShareResponse(
        items=items,
        as_of=date.today().isoformat(),
        sample_size=sample_size,
    )


@router.get("/stats/cooccurrence", response_model=CooccurrenceResponse)
def stats_cooccurrence(
    session: SessionDep,
    pool: Annotated[Pool, Query(description="global 또는 domestic")],
    skill: Annotated[str | None, Query(description="포커스 canonical 기술명. 미지정 시 pool 전체 상위 링크")] = None,
    top_k: Annotated[int, Query(ge=1, le=200)] = 30,
) -> CooccurrenceResponse:
    """기술 co-occurrence 네트워크. skill 지정 시 이웃 링크, 미지정 시 pool 전체 상위 링크(중복 쌍 제거)."""
    nodes, links = get_cooccurrence(session=session, pool=pool, skill=skill, top_k=top_k)
    return CooccurrenceResponse(
        nodes=nodes,
        links=links,
        as_of=date.today().isoformat(),
    )


@router.get(
    "/stats/posting-timeline",
    response_model=PostingTimelineResponse,
    response_model_exclude_none=True,
)
def stats_posting_timeline(
    session: SessionDep,
    pool: Annotated[Pool, Query(description="global 또는 domestic")],
    days: Annotated[int, Query(ge=1, le=365, description="집계 일수")] = 36,
    position: Annotated[str | None, Query(description="직무 카테고리")] = None,
    resume_id: Annotated[int | None, Query(description="저장 이력서 ID")] = None,
    session_id: Annotated[str | None, Query(description="비로그인 분석 세션 ID")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> PostingTimelineResponse:
    """최신 공고 일별 타임라인. resume_id/session_id 지정 시 보유기술과 1개 이상 겹치는 공고 수도 반환."""
    owned_skill_ids = resolve_optional_owned_skill_ids(session, resume_id, session_id, authorization)
    daily, as_of = get_posting_timeline(
        session=session,
        pool=pool,
        days=days,
        owned_skill_ids=owned_skill_ids,
        position=position,
    )
    return PostingTimelineResponse(daily=daily, as_of=as_of)


@router.get("/stats/response-rate", response_model=ResponseRateResponse)
def stats_response_rate(
    session: SessionDep,
    pool: Annotated[Pool, Query(description="global 또는 domestic")] = "domestic",
) -> ResponseRateResponse:
    """응답률 분포(20포인트 폭 5버킷) + 회사별 평균 응답률. response_rate는 wanted 소스만 적재됨."""
    result = get_response_rate(session=session, pool=pool)
    return ResponseRateResponse(
        pool=pool,
        median_rate=result["median_rate"],
        levels=result["levels"],
        companies=result["companies"],
        as_of=date.today().isoformat(),
        sample_size=result["sample_size"],
    )


@router.get("/stats/skill-trend-yearly", response_model=SkillTrendYearlyResponse)
def stats_skill_trend_yearly(
    session: SessionDep,
    pool: Annotated[Pool, Query(description="global 또는 domestic")],
    top_k: Annotated[int, Query(ge=1, le=50, description="추적할 상위 기술 수")] = 15,
) -> SkillTrendYearlyResponse:
    """연도별 기술 점유율 추이 + 급상승/급하락 무버스."""
    result = get_skill_trend_yearly(session=session, pool=pool, top_k=top_k)
    return SkillTrendYearlyResponse(
        pool=pool,
        years=result["years"],
        series=result["series"],
        movers=result["movers"],
        as_of=date.today().isoformat(),
        sample_size=result["sample_size"],
    )


@router.get("/stats/hot-companies", response_model=HotCompaniesResponse)
def stats_hot_companies(
    session: SessionDep,
    pool: Annotated[Pool, Query(description="global 또는 domestic")],
    days: Annotated[int, Query(ge=1, le=90, description="집계 일수")] = 30,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> HotCompaniesResponse:
    """최근 days일간(풀 내 최신 공고일 기준) 신규 공고가 많은 활발 기업."""
    items, as_of = get_hot_companies(session=session, pool=pool, days=days, limit=limit)
    return HotCompaniesResponse(pool=pool, days=days, items=items, as_of=as_of)


@router.get("/stats/region-density", response_model=RegionDensityResponse)
def stats_region_density(
    session: SessionDep,
    pool: Annotated[Pool, Query(description="global 또는 domestic")] = "domestic",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RegionDensityResponse:
    """지역(구/동)별 공고 밀도. region_district는 domestic 공고에만 적재됨."""
    items, as_of = get_region_density(session=session, pool=pool, limit=limit)
    return RegionDensityResponse(pool=pool, items=items, as_of=as_of)


@router.get(
    "/stats/skill-unlock",
    response_model=SkillUnlockResponse,
    response_model_exclude_none=True,
)
def stats_skill_unlock(
    session: SessionDep,
    pool: Annotated[Pool, Query(description="global 또는 domestic")],
    resume_id: Annotated[int | None, Query(description="저장 이력서 ID")] = None,
    session_id: Annotated[str | None, Query(description="비로그인 분석 세션 ID")] = None,
    position: Annotated[str | None, Query(description="직무 필터")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> SkillUnlockResponse:
    """한계 해금 — 기술 하나를 더 배우면 지원 가능(apply)해지는 공고가 얼마나 늘어나는지."""
    owned_skill_ids = resolve_owned_skill_ids(session, resume_id, session_id, authorization)
    result = get_skill_unlock(session=session, pool=pool, owned_skill_ids=owned_skill_ids, position=position)
    return SkillUnlockResponse(
        pool=pool,
        funnel=result["funnel"],
        candidates=result["candidates"],
        as_of=date.today().isoformat(),
        sample_size=result["sample_size"],
        sample_warning=True if result["sample_size"] < 50 else None,
    )
