from datetime import date
from typing import Literal

from pydantic import BaseModel


Pool = Literal["global", "domestic"]
PostingSort = Literal["latest", "deadline"]


class PostingCardItem(BaseModel):
    id: int
    title: str
    company: str | None
    post_date: date | None
    close_date: date | None
    skills: list[str]
    url: str
    matched_count: int | None = None


class PostingListResponse(BaseModel):
    items: list[PostingCardItem]
    page: int
    page_size: int
    total: int
    as_of: str


class PostingDetailResponse(BaseModel):
    id: int
    source: str
    pool: str | None
    company: str | None
    title: str
    post_date: date | None
    close_date: date | None
    career_min: int | None
    career_max: int | None
    region: str | None
    lat: float | None = None
    lng: float | None = None
    industry: str | None
    response_rate: float | None
    categories: list[str]
    skills: list[str]
    certs: list[str]
    url: str
