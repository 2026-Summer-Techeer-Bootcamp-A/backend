from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class Resume(TimestampMixin, SoftDeleteMixin, Base):
    """이력서 — 스킬셋·메타만 저장(원본 PDF·개인정보는 저장하지 않음)."""

    __tablename__ = "resume"
    __table_args__ = (CheckConstraint("pool IN ('domestic', 'global')", name="ck_resume_pool"),)

    resume_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[str | None] = mapped_column(String(64), nullable=True)
    career_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    career_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pool: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="resumes")
    skills: Mapped[list["ResumeSkill"]] = relationship(back_populates="resume")
    certs: Mapped[list["ResumeCert"]] = relationship(back_populates="resume")


class ResumeSkill(TimestampMixin, SoftDeleteMixin, Base):
    """이력서의 기술. 사전 밖 기술은 raw_label로 보존한다(is_out_of_dict=true)."""

    __tablename__ = "resume_skill"

    id: Mapped[int] = mapped_column(primary_key=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resume.resume_id"), nullable=False, index=True)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skill.id"), nullable=True, index=True)
    raw_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_out_of_dict: Mapped[bool] = mapped_column(default=False)

    resume: Mapped[Resume] = relationship(back_populates="skills")


class ResumeCert(TimestampMixin, SoftDeleteMixin, Base):
    """이력서의 자격증. 사전 밖 자격증은 raw_label로 보존한다(is_out_of_dict=true)."""

    __tablename__ = "resume_cert"

    id: Mapped[int] = mapped_column(primary_key=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resume.resume_id"), nullable=False, index=True)
    cert_id: Mapped[int | None] = mapped_column(ForeignKey("cert.id"), nullable=True, index=True)
    raw_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_out_of_dict: Mapped[bool] = mapped_column(default=False)

    resume: Mapped[Resume] = relationship(back_populates="certs")
