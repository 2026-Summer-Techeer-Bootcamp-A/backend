from pydantic import BaseModel


class JobCategoryItem(BaseModel):
    name: str
    is_tech: bool


class JobCategoryListResponse(BaseModel):
    categories: list[JobCategoryItem]
