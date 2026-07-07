from datetime import datetime

from sqlalchemy import DateTime, false, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """created_at — 전 테이블 공통 규약 (cite/04-erd.md)."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SoftDeleteMixin:
    """is_deleted/deleted_at — 하드 삭제 없이 소프트 삭제만 사용하는 공통 규약."""

    is_deleted: Mapped[bool] = mapped_column(default=False, server_default=false())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
