from pydantic import BaseModel


class JobCategoryItem(BaseModel):
    name: str
    is_tech: bool
    group_name: str | None


class JobCategoryListResponse(BaseModel):
    categories: list[JobCategoryItem]
