from datetime import date, datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings

from app.core.db import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.cert import Cert
    from app.models.skill import Skill


class Posting(TimestampMixin, SoftDeleteMixin, Base):
    """채용 공고 핵심 팩트. pool은 source로부터 자동 파생(사람이 직접 입력하지 않음)."""

    __tablename__ = "posting"
    __table_args__ = (
        UniqueConstraint("source", "source_uid"),
        CheckConstraint("pool IN ('domestic', 'global')", name="ck_posting_pool"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    source_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    pool: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    company: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    post_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    career_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    career_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seniority_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    region_country: Mapped[str | None] = mapped_column(CHAR(2), nullable=True)
    region_city: Mapped[str | None] = mapped_column(Text, nullable=True)
    region_district: Mapped[str | None] = mapped_column(Text, nullable=True)
    lat: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    lng: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_rate: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    raw_postings: Mapped[list["RawPosting"]] = relationship(back_populates="posting")
    techs: Mapped[list["PostingTech"]] = relationship(back_populates="posting")
    certs: Mapped[list["PostingCert"]] = relationship(back_populates="posting")
    categories: Mapped[list["PostingCategory"]] = relationship(back_populates="posting")
    embedding: Mapped["PostingEmbedding | None"] = relationship(back_populates="posting", uselist=False)


class RawPosting(TimestampMixin, SoftDeleteMixin, Base):
    """수집 원본 JSON 보관. mart에 아직 안 올라온 소스 필드까지 전부 여기 있음."""

    __tablename__ = "raw_posting"

    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("posting.id"), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    posting: Mapped[Posting] = relationship(back_populates="raw_postings")


class PostingTech(TimestampMixin, SoftDeleteMixin, Base):
    """공고 ↔ 기술 (N:M). 커버리지·갭·시장통계·co-occurrence 전부 이 테이블이 근거."""

    __tablename__ = "posting_tech"
    __table_args__ = (UniqueConstraint("posting_id", "skill_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("posting.id"), nullable=False, index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skill.id"), nullable=False, index=True)

    posting: Mapped[Posting] = relationship(back_populates="techs")
    skill: Mapped["Skill"] = relationship()


class PostingCert(TimestampMixin, SoftDeleteMixin, Base):
    """공고 ↔ 자격증 (N:M)."""

    __tablename__ = "posting_cert"
    __table_args__ = (UniqueConstraint("posting_id", "cert_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("posting.id"), nullable=False, index=True)
    cert_id: Mapped[int] = mapped_column(ForeignKey("cert.id"), nullable=False, index=True)

    posting: Mapped[Posting] = relationship(back_populates="certs")
    cert: Mapped["Cert"] = relationship()


class PostingCategory(TimestampMixin, SoftDeleteMixin, Base):
    """공고 ↔ 직군. category는 job_category.name을 문자열로 참조한다(물리 FK 아님)."""

    __tablename__ = "posting_category"
    __table_args__ = (UniqueConstraint("posting_id", "category"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("posting.id"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)

    posting: Mapped[Posting] = relationship(back_populates="categories")


class PostingEmbedding(TimestampMixin, SoftDeleteMixin, Base):
    """공고 1:1 벡터. id 자체가 posting.id를 참조하는 공유 PK(1:1) — DDL상 유일한 물리 FK."""

    __tablename__ = "posting_embedding"

    id: Mapped[int] = mapped_column(ForeignKey("posting.id"), primary_key=True)
    # 차원은 config.embedding_dim(=1024, BGE-M3)에서 단일 소스로 참조 — 하드코딩 금지.
    embedding: Mapped[list[float]] = mapped_column(
        Text().with_variant(Vector(settings.embedding_dim), "postgresql"), nullable=False
    )
    model: Mapped[str | None] = mapped_column(Text, nullable=True)

    posting: Mapped[Posting] = relationship(back_populates="embedding")
