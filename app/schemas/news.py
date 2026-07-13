from typing import Literal

from pydantic import BaseModel

NewsSource = Literal["hackernews", "geeknews", "github"]


class NewsItem(BaseModel):
    title: str
    url: str
    comments_url: str | None = None
    points: int | None = None
    comments_count: int | None = None
    language: str | None = None
    stars: int | None = None
    description: str | None = None


class NewsResponse(BaseModel):
    source: NewsSource
    items: list[NewsItem]
    fetched_at: str
    stale: bool = False
    error: bool = False
