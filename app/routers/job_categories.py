from fastapi import APIRouter

from app.core.deps import SessionDep
from app.crud.job_category import list_job_categories
from app.schemas.job_category import JobCategoryItem, JobCategoryListResponse

router = APIRouter()


@router.get("/job-categories", response_model=JobCategoryListResponse)
def get_job_categories(session: SessionDep) -> JobCategoryListResponse:
    categories = list_job_categories(session)
    return JobCategoryListResponse(
        categories=[
            JobCategoryItem(name=category.name, is_tech=category.is_tech)
            for category in categories
        ]
    )
