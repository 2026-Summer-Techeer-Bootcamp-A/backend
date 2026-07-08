from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Cert, Posting, PostingCategory, PostingCert, Resume, ResumeCert


def search_certs(session: Session, q: str | None = None, limit: int = 20) -> list[Cert]:
    stmt = select(Cert).where(Cert.is_deleted.is_(False))

    if q:
        stmt = stmt.where(Cert.name.ilike(f"%{q}%"))

    stmt = stmt.order_by(Cert.name).limit(limit)
    return list(session.execute(stmt).scalars().all())


def resume_exists(session: Session, resume_id: int) -> bool:
    stmt = select(Resume.resume_id).where(
        Resume.resume_id == resume_id,
        Resume.is_deleted.is_(False),
    )
    return session.execute(stmt).first() is not None


def get_owned_cert_names(session: Session, resume_id: int) -> list[str]:
    stmt = (
        select(Cert.name)
        .join(ResumeCert, ResumeCert.cert_id == Cert.id)
        .where(
            ResumeCert.resume_id == resume_id,
            ResumeCert.is_deleted.is_(False),
            ResumeCert.is_out_of_dict.is_(False),
            Cert.is_deleted.is_(False),
        )
        .order_by(Cert.name)
    )
    return list(session.execute(stmt).scalars().all())


def count_matching_postings(session: Session, pool: str, position: str) -> int:
    stmt = (
        select(func.count(func.distinct(Posting.id)))
        .join(PostingCategory, PostingCategory.posting_id == Posting.id)
        .where(
            Posting.pool == pool,
            Posting.is_deleted.is_(False),
            PostingCategory.category == position,
            PostingCategory.is_deleted.is_(False),
        )
    )
    return session.execute(stmt).scalar_one()


def get_required_cert_stats(session: Session, pool: str, position: str) -> list[tuple[str, int]]:
    stmt = (
        select(Cert.name, func.count(func.distinct(Posting.id)).label("posting_count"))
        .join(PostingCert, PostingCert.cert_id == Cert.id)
        .join(Posting, Posting.id == PostingCert.posting_id)
        .join(PostingCategory, PostingCategory.posting_id == Posting.id)
        .where(
            Posting.pool == pool,
            Posting.is_deleted.is_(False),
            PostingCategory.category == position,
            PostingCategory.is_deleted.is_(False),
            PostingCert.is_deleted.is_(False),
            Cert.is_deleted.is_(False),
        )
        .group_by(Cert.name)
        .order_by(Cert.name)
    )
    return [(name, posting_count) for name, posting_count in session.execute(stmt).all()]
