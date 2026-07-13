from datetime import date

from pydantic import BaseModel


class FeedMatch(BaseModel):
    rate: float
    owned_skills: list[str]
    missing_skills: list[str]


class FeedPostingItem(BaseModel):
    id: int
    title: str
    company: str | None
    industry: str | None
    region: str | None
    pool: str | None
    post_date: date | None
    close_date: date | None
    categories: list[str]
    skills: list[str]
    url: str
    match: FeedMatch | None = None


class FeedResponse(BaseModel):
    items: list[FeedPostingItem]
    page: int
    page_size: int
    total: int
    as_of: str
