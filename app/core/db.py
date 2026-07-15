from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# Engine is created lazily-connected: create_engine does not open a connection
# until first use, so importing this module never requires a live database.
# pool_size/max_overflow are explicit (not SQLAlchemy's 5+10 default): with
# --workers 2 that default caps the whole process at 15 connections total,
# which a handful of multi-second raw-aggregation stats queries can exhaust
# outright under load, starving every other endpoint waiting on the pool.
# 10+10 per worker x 2 workers = 40 max, versus Cloud SQL's max_connections=100.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=10,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped DB session."""
    with SessionLocal() as session:
        yield session
