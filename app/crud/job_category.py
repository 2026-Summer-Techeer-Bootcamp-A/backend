from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import JobCategory


def list_job_categories(session: Session) -> list[JobCategory]:
    stmt = (
        select(JobCategory)
        .where(JobCategory.is_deleted.is_(False))
        .order_by(JobCategory.name)
    )
    return list(session.scalars(stmt).all())
