from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import SessionDep
from app.schemas.match import Pool
from app.schemas.stats import SkillShareResponse
from app.services.stats import get_skill_share_response

router = APIRouter()


@router.get("/stats/skills", response_model=SkillShareResponse)
def get_stats_skills(
    session: SessionDep,
    pool: Annotated[Pool, Query(description="global 또는 domestic")],
    position: Annotated[str | None, Query(description="직무 필터")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="상위 기술 수")] = 30,
) -> SkillShareResponse:
    return get_skill_share_response(
        session=session,
        pool=pool,
        position=position,
        limit=limit,
    )
