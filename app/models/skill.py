from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin


class Skill(TimestampMixin, SoftDeleteMixin, Base):
    """기술 사전 — 정규 기술명(canonical). collector/taxonomy_v2.json의 DB 반영본."""

    __tablename__ = "skill"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    is_ambiguous: Mapped[bool] = mapped_column(default=False)

    aliases: Mapped[list["SkillAlias"]] = relationship(back_populates="skill")


class SkillAlias(TimestampMixin, SoftDeleteMixin, Base):
    """기술 별칭 — 표준명 외 표기(영문+한글)."""

    __tablename__ = "skill_alias"

    id: Mapped[int] = mapped_column(primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skill.id"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    is_korean: Mapped[bool] = mapped_column(default=False)

    skill: Mapped[Skill] = relationship(back_populates="aliases")
