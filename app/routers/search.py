from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import SessionDep
from app.crud.search import search_all
from app.schemas.search import SearchCompanyItem, SearchPostingItem, SearchResponse, SearchSkillItem

router = APIRouter()


@router.get("/search", response_model=SearchResponse)
def search(
    session: SessionDep,
    q: Annotated[str, Query(min_length=1, description="검색어 (공고 제목/회사, 기술명, 회사명)")],
    limit: Annotated[int, Query(ge=1, le=20, description="카테고리별 상위 개수")] = 5,
) -> SearchResponse:
    """공고 · 기술 · 기업을 한 쿼리로 통합 검색해 각 카테고리 상위 매치를 반환한다."""
    result = search_all(session=session, q=q, limit=limit)

    return SearchResponse(
        postings=[
            SearchPostingItem(
                id=posting.id,
                title=posting.title,
                company=posting.company or "",
                pool=posting.pool or "",
            )
            for posting in result["postings"]
        ],
        skills=[SearchSkillItem(canonical=skill.canonical, category=skill.category) for skill in result["skills"]],
        companies=[SearchCompanyItem(**company) for company in result["companies"]],
        query=q,
    )
