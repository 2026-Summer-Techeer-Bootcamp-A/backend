from typing import Annotated

import jwt
from fastapi import APIRouter, Header, HTTPException, Query, status
from jwt.exceptions import InvalidTokenError

from app.core.deps import SessionDep
from app.core.redis import is_token_blocklisted
from app.core.security import ALGORITHM, SECRET_KEY
from app.models.user import User
from app.schemas.match import MatchCoverageResponse,MatchGapResponse,MatchWhatIfResponse, Pool
from app.services.match import (
    calculate_what_if_response,
    calculate_coverage_response,
    calculate_gap_response,
    get_skill_ids_from_resume,
    get_skill_ids_from_session,
)


router = APIRouter()


def get_user_from_optional_authorization(
    session: SessionDep,
    authorization: str | None,
) -> User | None:
    if authorization is None:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    if is_token_blocklisted(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise ValueError
    except (InvalidTokenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    user = session.get(User, int(user_id))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    return user


@router.get(
    "/gap",
    response_model=MatchGapResponse,
    response_model_exclude_none=True,
)
def get_match_gap(
    session: SessionDep,
    pool: Annotated[Pool, Query(description="global 또는 domestic")],
    resume_id: Annotated[int | None, Query(description="저장 이력서 ID")] = None,
    session_id: Annotated[str | None, Query(description="비로그인 분석 세션 ID")] = None,
    position: Annotated[str | None, Query(description="직무 필터")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> MatchGapResponse:
    if resume_id is None and session_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="resume_id or session_id is required",
        )

    if resume_id is not None:
        current_user = get_user_from_optional_authorization(session, authorization)
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )

        owned_skill_ids = get_skill_ids_from_resume(
            session=session,
            resume_id=resume_id,
            current_user=current_user,
        )
    else:
        owned_skill_ids = get_skill_ids_from_session(
            session=session,
            session_id=session_id)

    return calculate_gap_response(
        session=session,
        pool=pool,
        position=position,
        owned_skill_ids=owned_skill_ids,
    )

@router.get(
    "/coverage",
    response_model=MatchCoverageResponse,
)
def get_match_coverage(
    session: SessionDep,
    pool: Annotated[Pool, Query(description="global 또는 domestic")],
    resume_id: Annotated[int | None, Query(description="저장 이력서 ID")] = None,
    session_id: Annotated[str | None, Query(description="비로그인 분석 세션 ID")] = None,
    position: Annotated[str | None, Query(description="직무 필터")] = None,
    top_k: Annotated[int, Query(ge=1, le=100, description="상위 요구 기술 수")] = 20,
    authorization: Annotated[str | None, Header()] = None,
) -> MatchCoverageResponse:
    if resume_id is None and session_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="resume_id or session_id is required",
        )

    if resume_id is not None:
        current_user = get_user_from_optional_authorization(session, authorization)
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )

        owned_skill_ids = get_skill_ids_from_resume(
            session=session,
            resume_id=resume_id,
            current_user=current_user,
        )
    else: #resume/confirm
        owned_skill_ids = get_skill_ids_from_session(
            session=session,
            session_id=session_id)

    return calculate_coverage_response(
        session=session,
        pool=pool,
        position=position,
        owned_skill_ids=owned_skill_ids,
        top_k=top_k,
    )

@router.get(
    "/what-if",
    response_model=MatchWhatIfResponse,
    response_model_exclude_none=True,
)
def get_match_what_if(
    session: SessionDep,
    pool: Annotated[Pool, Query(description="global 또는 domestic")],
    add: Annotated[str, Query(description="가상으로 추가할 canonical 기술명")],
    resume_id: Annotated[int | None, Query(description="저장 이력서 ID")] = None,
    session_id: Annotated[str | None, Query(description="비로그인 분석 세션 ID")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> MatchWhatIfResponse:
    if resume_id is None and session_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="resume_id or session_id is required",
        )

    if resume_id is not None:
        current_user = get_user_from_optional_authorization(session, authorization)
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
            )

        owned_skill_ids = get_skill_ids_from_resume(
            session=session,
            resume_id=resume_id,
            current_user=current_user,
        )
    else:
        owned_skill_ids = get_skill_ids_from_session(
            session=session,
            session_id=session_id)

    return calculate_what_if_response(
        session=session,
        pool=pool,
        add=add,
        owned_skill_ids=owned_skill_ids,
    )