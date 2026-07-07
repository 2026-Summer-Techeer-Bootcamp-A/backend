from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Cert


def search_certs(session: Session, q: str | None = None, limit: int = 20) -> list[Cert]:
    stmt = select(Cert).where(Cert.is_deleted.is_(False))

    if q:
        stmt = stmt.where(Cert.name.ilike(f"%{q}%"))

    stmt = stmt.order_by(Cert.name).limit(limit)
    return list(session.execute(stmt).scalars().all())
