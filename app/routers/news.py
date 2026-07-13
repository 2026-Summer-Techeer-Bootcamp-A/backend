from typing import Annotated

from fastapi import APIRouter, Query

from app.schemas.news import NewsResponse, NewsSource
from app.services.news import get_news

router = APIRouter()


@router.get("/news", response_model=NewsResponse)
def read_news(
    source: Annotated[NewsSource, Query(description="hackernews | geeknews | github")],
    limit: Annotated[int, Query(ge=1, le=30)] = 15,
) -> NewsResponse:
    """기술 뉴스 피드. Redis 4시간 캐시, 실패 시 24시간 stale 폴백."""
    return NewsResponse(**get_news(source, limit))
