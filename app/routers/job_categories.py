from typing import Annotated

from fastapi import APIRouter, Query

from app.core.config import settings
from app.core.deps import SessionDep
from app.crud.job_category import list_job_categories
from app.schemas.job_category import JobCategoryItem, JobCategoryListResponse
from app.schemas.posting import Pool
from app.services.reference_cache import get_cached, make_reference_cache_key, set_cached

router = APIRouter()


@router.get("/job-categories", response_model=JobCategoryListResponse)
def get_job_categories(
    session: SessionDep,
    pool: Annotated[
        Pool | None,
        Query(description="global 또는 domestic — 지정 시 해당 pool에 실제 존재하는 카테고리만 반환"),
    ] = None,
) -> JobCategoryListResponse:
    cache_key = make_reference_cache_key("job-categories", {"pool": pool})
    cached = get_cached(cache_key, JobCategoryListResponse)
    if cached is not None:
        return cached

    categories = list_job_categories(session, pool=pool)
    response = JobCategoryListResponse(
        categories=[
            JobCategoryItem(
                name=category.name, is_tech=category.is_tech, group_name=category.group_name
            )
            for category in categories
        ]
    )
    set_cached(cache_key, response, settings.reference_cache_ttl_seconds)
    return response
