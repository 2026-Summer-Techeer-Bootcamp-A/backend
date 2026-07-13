from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import SessionDep
from app.crud.job_category import list_job_categories
from app.schemas.job_category import JobCategoryItem, JobCategoryListResponse
from app.schemas.posting import Pool

router = APIRouter()


@router.get("/job-categories", response_model=JobCategoryListResponse)
def get_job_categories(
    session: SessionDep,
    pool: Annotated[
        Pool | None,
        Query(description="global 또는 domestic — 지정 시 해당 pool에 실제 존재하는 카테고리만 반환"),
    ] = None,
) -> JobCategoryListResponse:
    categories = list_job_categories(session, pool=pool)
    return JobCategoryListResponse(
        categories=[
            JobCategoryItem(name=category.name, is_tech=category.is_tech)
            for category in categories
        ]
    )
