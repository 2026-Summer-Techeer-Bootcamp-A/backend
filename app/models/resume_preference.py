from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.resume import Resume


class ResumePreference(TimestampMixin, Base):
    """이력서 선호도 — resume과 1:1. 자유 태그·지역 등 확장 값은 preferences_extra(JSONB)에 저장."""

    __tablename__ = "resume_preference"
    __table_args__ = (
        CheckConstraint(
            "level IN ('intern', 'junior', 'mid', 'senior', 'lead', 'director')",
            name="ck_resume_preference_level",
        ),
        CheckConstraint(
            "job_search_status IN ('active', 'casual', 'none')",
            name="ck_resume_preference_job_search_status",
        ),
    )

    resume_id: Mapped[int] = mapped_column(ForeignKey("resume.resume_id"), primary_key=True)
    level: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_search_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferences_extra: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
        server_default="{}",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    resume: Mapped["Resume"] = relationship()
