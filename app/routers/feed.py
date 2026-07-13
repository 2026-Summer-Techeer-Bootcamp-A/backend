from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, Query
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
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    authorization: Annotated[str | None, Header()] = None,
) -> FeedResponse:
    """홈 피드용 공고 타임라인 (최신순). 로그인 시 매치 개인화 포함."""
    user = get_user_from_optional_authorization(session, authorization)
    owned_skill_ids = (
        _resolve_owned_skill_ids_for_user(session, user) if user is not None else None
    )
    items, total = list_feed_postings(
        session=session,
        pool=pool,
        category=category,
        page=page,
        page_size=page_size,
        owned_skill_ids=owned_skill_ids,
    )
    return FeedResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        as_of=date.today().isoformat(),
    )
