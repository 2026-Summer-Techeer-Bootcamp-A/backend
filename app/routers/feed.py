from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import SessionDep
from app.crud.feed import list_feed_postings
from app.models.resume import Resume
from app.models.user import User
from app.routers.match import get_user_from_optional_authorization
from app.schemas.feed import FeedResponse
from app.schemas.posting import Pool
from app.services.match import get_skill_ids_from_resume

router = APIRouter()


def _resolve_owned_skill_ids_for_user(session: Session, user: User) -> set[int] | None:
    """유저의 대표(최신) 이력서 스킬셋. 이력서가 없거나 실패하면 None (익명 degrade)."""
    try:
        resume_id = session.execute(
            select(Resume.resume_id)
            .where(Resume.user_id == user.id, Resume.is_deleted.is_(False))
            .order_by(Resume.resume_id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if resume_id is None:
            return None
        return get_skill_ids_from_resume(
            session=session, resume_id=resume_id, current_user=user
        )
    except Exception:
        return None


@router.get("/feed/postings", response_model=FeedResponse)
def read_feed_postings(
    session: SessionDep,
    pool: Annotated[Pool | None, Query()] = None,
    category: Annotated[str | None, Query(description="job_category name")] = None,
    district: Annotated[str | None, Query(description="region_district 부분 일치")] = None,
    deadline_within_days: Annotated[int | None, Query(ge=1)] = None,
    min_match: Annotated[
        int | None, Query(ge=0, le=100, description="최소 매치율(%). 로그인 + 이력서 필요")
    ] = None,
    sort: Annotated[
        Literal["latest", "match"],
        Query(description="latest 또는 match. match는 로그인+이력서 필요 — 없으면 latest로 자동 폴백(에러 없음)"),
    ] = "latest",
    industry: Annotated[str | None, Query(description="업종 부분일치(Posting.industry)")] = None,
    skills: Annotated[str | None, Query(description="쉼표로 구분한 기술명(하나 이상 일치)")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    authorization: Annotated[str | None, Header()] = None,
) -> FeedResponse:
    """홈 피드용 공고 타임라인 (최신순/매칭순). 로그인 시 매치 개인화 포함."""
    user = get_user_from_optional_authorization(session, authorization)
    owned_skill_ids = (
        _resolve_owned_skill_ids_for_user(session, user) if user is not None else None
    )
    if min_match is not None and owned_skill_ids is None:
        # 매치율 필터는 이력서 스킬셋 없이는 계산할 수 없다.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_match requires an authenticated user with a resume",
        )
    items, total = list_feed_postings(
        session=session,
        pool=pool,
        category=category,
        page=page,
        page_size=page_size,
        owned_skill_ids=owned_skill_ids,
        district=district,
        deadline_within_days=deadline_within_days,
        min_match=min_match,
        sort=sort,
        skills=[skill.strip() for skill in skills.split(",") if skill.strip()] if skills else None,
        industry=industry,
    )
    return FeedResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        as_of=date.today().isoformat(),
    )
