from typing import Annotated

from fastapi import APIRouter, Query

from app.core.config import settings
from app.core.deps import SessionDep
from app.crud.skill import search_skills
from app.schemas.skill import SkillListResponse, SkillResponse
from app.services.reference_cache import get_cached, make_reference_cache_key, set_cached

router = APIRouter()


@router.get("/skills", response_model=SkillListResponse)
def get_skills(
    session: SessionDep,
    q: str | None = None,
    category: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SkillListResponse:
    cache_key = make_reference_cache_key("skills", {"q": q, "category": category, "limit": limit})
    cached = get_cached(cache_key, SkillListResponse)
    if cached is not None:
        return cached

    skills = search_skills(session=session, q=q, category=category, limit=limit)
    response = SkillListResponse(
        skills=[
            SkillResponse(
                canonical=skill.canonical,
                category=skill.category,
                aliases=[alias.alias for alias in skill.aliases if not alias.is_deleted],
            )
            for skill in skills
        ]
    )
    set_cached(cache_key, response, settings.reference_cache_ttl_seconds)
    return response

