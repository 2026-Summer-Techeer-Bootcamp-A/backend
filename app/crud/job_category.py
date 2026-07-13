from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import JobCategory, Posting, PostingCategory


def list_job_categories(session: Session, pool: str | None = None) -> list[JobCategory]:
    """직군 통제 어휘 조회.

    pool이 None이면 기존 동작(전체 어휘)을 그대로 유지한다 — 이력서 직무 선택 등
    pool과 무관한 소비처가 있어 하위호환이 필요하다. pool이 주어지면 해당 pool에
    실제 존재하는(공고에 태깅된) 카테고리만 반환한다.
    """
    stmt = select(JobCategory).where(JobCategory.is_deleted.is_(False))

    if pool is not None:
        stmt = (
            stmt.join(PostingCategory, PostingCategory.category == JobCategory.name)
            .join(Posting, Posting.id == PostingCategory.posting_id)
            .where(
                PostingCategory.is_deleted.is_(False),
                Posting.is_deleted.is_(False),
                Posting.pool == pool,
            )
            .distinct()
        )

    stmt = stmt.order_by(JobCategory.name)
    return list(session.scalars(stmt).all())
