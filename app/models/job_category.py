from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin


class JobCategory(TimestampMixin, SoftDeleteMixin, Base):
    """직군 카테고리. resume.position / posting_category.category가 이름으로 공유하는 통제 어휘 (물리 FK 아님)."""

    __tablename__ = "job_category"

    id: Mapped[int] = mapped_column(primary_key=True) # Mapped = SQLA 2.0의 컬럼 정의 방식
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_tech: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
