from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.resume import Resume


class User(TimestampMixin, SoftDeleteMixin, Base):
    """사용자 계정. email은 대소문자 구분 없이 중복 체크(CITEXT, 운영 Postgres 한정)."""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(
        String(255).with_variant(CITEXT(), "postgresql"), nullable=False, unique=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    resumes: Mapped[list["Resume"]] = relationship(back_populates="user")
