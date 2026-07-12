"""Stats/Trend 확장 인사이트 라우터.

프론트 `/widgets` 갤러리에만 있던 pearl 지표(a,h,o,p,r,x)를 정식 엔드포인트로 노출한다.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import SessionDep
from app.crud.insight import (
    get_global_domestic_gap,
    get_hiring_season,
    get_hype_vs_hire,
    get_industry_fingerprint,
    get_newcomer_gate,
    get_role_stack_fit,
)
from app.schemas.insight import (
    GlobalDomesticGapResponse,
    HiringSeasonResponse,
    HypeVsHireResponse,
    IndustryFingerprintResponse,
    NewcomerGateResponse,
    RoleStackFitResponse,
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
