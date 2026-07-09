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
