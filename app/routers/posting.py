from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.core.deps import SessionDep
from app.crud.posting import get_nearby_postings, get_posting_detail, get_similar_postings, list_posting_cards
from app.routers.match import get_user_from_optional_authorization
from app.schemas.posting import (
    NearbyPostingsResponse,
    Pool,
    PostingDetailResponse,
    PostingListResponse,
    PostingSort,
    SimilarPostingsResponse,
)


router = APIRouter()


@router.get(
    "/postings/{posting_id}",
    response_model=PostingDetailResponse,
    response_model_exclude_none=True,
)
def get_posting(
    posting_id: int,
    session: SessionDep,
) -> PostingDetailResponse:
    return PostingDetailResponse(**get_posting_detail(session, posting_id=posting_id))


@router.get(
    "/postings/{posting_id}/nearby",
    response_model=NearbyPostingsResponse,
    response_model_exclude_none=True,
)
def get_posting_nearby(
    posting_id: int,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> NearbyPostingsResponse:
    """자기 자신을 제외한, 같은 지역(region_district)의 최신 공고."""
    items = get_nearby_postings(session, posting_id=posting_id, limit=limit)
    return NearbyPostingsResponse(items=items, as_of=date.today().isoformat())


@router.get(
    "/postings/{posting_id}/similar",
    response_model=SimilarPostingsResponse,
    response_model_exclude_none=True,
)
def get_posting_similar(
    posting_id: int,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SimilarPostingsResponse:
    """자기 자신을 제외한, 요구 기술 겹침이 많은 순 유사 공고."""
    items = get_similar_postings(session, posting_id=posting_id, limit=limit)
    return SimilarPostingsResponse(items=items, as_of=date.today().isoformat())


@router.get(
    "/postings",
    response_model=PostingListResponse,
    response_model_exclude_none=True,
)
def get_postings(
    session: SessionDep,
    pool: Annotated[Pool | None, Query(description="global 또는 domestic")] = None,
    position: Annotated[str | None, Query(description="직무 필터")] = None,
    sort: Annotated[PostingSort, Query(description="latest, deadline 또는 match")] = "latest",
    match_only: Annotated[bool, Query(description="이력서와 매칭되는 공고만 조회")] = False,
    resume_id: Annotated[int | None, Query(description="저장 이력서 ID")] = None,
    district: Annotated[str | None, Query(description="지역(구/동) 필터. region_district 부분일치")] = None,
    deadline_within_days: Annotated[
        int | None, Query(ge=1, le=365, description="마감까지 N일 이내인 공고만 조회")
    ] = None,
    min_match: Annotated[
        float | None, Query(ge=0, le=100, description="최소 매칭률(%). resume_id 필요")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    authorization: Annotated[str | None, Header()] = None,
) -> PostingListResponse:
    if sort == "deadline" and pool == "global":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sort=deadline is only supported for domestic postings",
        )

    if (match_only or min_match is not None) and resume_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="resume_id is required when match_only=true or min_match is set",
        )

    user_id = None
    if match_only or min_match is not None:
        current_user = get_user_from_optional_authorization(session, authorization)
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )
        user_id = current_user.id
    elif sort == "match" and resume_id is not None:
        # sort=match는 이력서/인증 컨텍스트가 없으면 의미가 없으니, match_only/min_match와
        # 달리 401로 막지 않고 user_id를 None으로 둬 crud 쪽에서 최신순으로 폴백한다.
        current_user = get_user_from_optional_authorization(session, authorization)
        if current_user is not None:
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
        district=district,
        deadline_within_days=deadline_within_days,
        min_match=min_match,
    )

    return PostingListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        as_of=date.today().isoformat(),
    )
