from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import SessionDep
from app.crud.skill import search_skills
from app.schemas.skill import SkillListResponse, SkillResponse

router = APIRouter()


@router.get("/skills", response_model=SkillListResponse)
def get_skills(
    session: SessionDep,
    q: str | None = None,
    category: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SkillListResponse:
    skills = search_skills(session=session, q=q, category=category, limit=limit)
    return SkillListResponse(
        skills=[
            SkillResponse(
                canonical=skill.canonical,
                category=skill.category,
                aliases=[alias.alias for alias in skill.aliases if not alias.is_deleted],
            )
            for skill in skills
        ]
    )

