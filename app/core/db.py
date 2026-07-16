from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# Engine is created lazily-connected: create_engine does not open a connection
# until first use, so importing this module never requires a live database.
#
# Pool sizing rationale (--workers 9, PostgreSQL max_connections=400):
#   - Reserved for monitoring/internal services: ~10 connections
#   - Available for app: ~390 connections
#   - Per-worker allocation: pool_size=30, max_overflow=10 → 40 max/worker
#   - Total max: 40 × 9 workers = 360 connections (within pg limit)
#   - pool_timeout=10: fail fast under saturation instead of blocking 30s,
#     which lets the caller surface a 503 quickly rather than queuing.
# 이름 붙여둔 이유: /metrics에서 워커당 풀 용량(POOL_SIZE + MAX_OVERFLOW)을
# 노출할 때 여기 숫자를 그대로 다시 적지 않고 재사용하기 위함(app/main.py).
POOL_SIZE = 30
MAX_OVERFLOW = 10

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_timeout=10,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped DB session."""
    with SessionLocal() as session:
        yield session
