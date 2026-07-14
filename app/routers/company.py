"""F7+F11: 특정 기술을 요구한 기업 목록 (과거/현재 분할 + 원티드 응답률)."""

from datetime import date

from fastapi import APIRouter, Query

from app.core.config import settings
from app.core.deps import SessionDep
from app.crud.company import find_skill_id, get_companies_by_skill
from app.schemas.company import CompanyBySkillResponse, CompanyEntry
from app.services.reference_cache import get_cached, make_reference_cache_key, set_cached

router = APIRouter()

DOMESTIC_NOTE = "국내 응답률은 원티드 공고에서만 집계돼요"


@router.get("/company/by-skill", response_model=CompanyBySkillResponse)
def companies_by_skill(
    session: SessionDep,
    skill: str = Query(..., min_length=1, description="조회할 기술명 (예: Kotlin)"),
    pool: str | None = Query(
        None,
        pattern=r"^(global|domestic)$",
        description="global 또는 domestic. 두 풀은 절대 혼합하지 않습니다",
    ),
) -> CompanyBySkillResponse:
    """skill을 요구한 기업 목록을 180일 기준으로 과거/현재로 나눠 돌려줍니다.

    response_rate(F11)는 원티드 공고에만 존재하며, 다른 출처 기업은 null일 수 있습니다.
    """
    cache_key = make_reference_cache_key("company-by-skill", {"skill": skill, "pool": pool})
    cached = get_cached(cache_key, CompanyBySkillResponse)
    if cached is not None:
        return cached

    skill_id = find_skill_id(session, skill)

    if skill_id is None:
        # 사전에 없는 기술 → 빈 결과 (에러가 아님)
        today = date.today().isoformat()
        response = CompanyBySkillResponse(
            skill=skill,
            split_date=today,
            present=[],
            past=[],
            as_of=today,
            domestic_note=DOMESTIC_NOTE if pool == "domestic" else None,
        )
        set_cached(cache_key, response, settings.company_by_skill_cache_ttl_seconds)
        return response

    split_date, as_of, present_rows, past_rows = get_companies_by_skill(
        session=session,
        skill_id=skill_id,
        pool=pool,
    )

    response = CompanyBySkillResponse(
        skill=skill,
        split_date=split_date.isoformat(),
        present=[CompanyEntry(**row) for row in present_rows],
        past=[CompanyEntry(**row) for row in past_rows],
        as_of=as_of.isoformat(),
        domestic_note=DOMESTIC_NOTE if pool == "domestic" else None,
    )
    set_cached(cache_key, response, settings.company_by_skill_cache_ttl_seconds)
    return response
