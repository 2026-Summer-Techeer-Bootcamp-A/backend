from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.core.deps import SessionDep
from app.crud.posting import list_posting_cards
from app.routers.match import get_user_from_optional_authorization
from app.schemas.posting import Pool, PostingListResponse, PostingSort


router = APIRouter()


@router.get(
    "/postings",
    response_model=PostingListResponse,
    response_model_exclude_none=True,
)
def get_postings(
    session: SessionDep,
    pool: Annotated[Pool | None, Query(description="global 또는 domestic")] = None,
    position: Annotated[str | None, Query(description="직무 필터")] = None,
    sort: Annotated[PostingSort, Query(description="latest 또는 deadline")] = "latest",
    match_only: Annotated[bool, Query(description="이력서와 매칭되는 공고만 조회")] = False,
    resume_id: Annotated[int | None, Query(description="저장 이력서 ID")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    authorization: Annotated[str | None, Header()] = None,
) -> PostingListResponse:
    if sort == "deadline" and pool == "global":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sort=deadline is only supported for domestic postings",
        )

    user_id = None
    if match_only:
        if resume_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="resume_id is required when match_only=true",
            )

        current_user = get_user_from_optional_authorization(session, authorization)
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )
        user_id = current_user.id

    items, total = list_posting_cards(
        session,
        pool=pool,
        position=position,
        sort=sort,
        match_only=match_only,
        resume_id=resume_id,
        user_id=user_id,
        page=page,
        page_size=page_size,
    )

    return PostingListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        as_of=date.today().isoformat(),
    )
