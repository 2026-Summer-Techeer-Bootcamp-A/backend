from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.skill import Skill


class InterestSignal(TimestampMixin, SoftDeleteMixin, Base):
    """뜨는 기술 — HN/GitHub 월별 관심 시그널(F19). 백필 불가(수집 프리즈 이후 영구 손실)."""

    __tablename__ = "interest_signal"
    __table_args__ = (CheckConstraint("source IN ('hn', 'github')", name="ck_interest_signal_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skill.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    value: Mapped[float] = mapped_column(Numeric, nullable=False)

    skill: Mapped["Skill"] = relationship()
