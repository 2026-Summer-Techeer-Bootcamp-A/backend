from datetime import date

from sqlalchemy import JSON, Date, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin


class GithubRepoSnapshot(TimestampMixin, SoftDeleteMixin, Base):
    """GitHub 레포 일별 스냅샷. gh-hn-data-collector 원본을 그대로 적재(합성 없음)."""

    __tablename__ = "github_repo_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    stargazers_count: Mapped[int] = mapped_column(Integer, nullable=False)
    forks_count: Mapped[int] = mapped_column(Integer, nullable=False)
    open_issues_count: Mapped[int] = mapped_column(Integer, nullable=False)
    subscribers_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topics: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    pushed_at: Mapped[date | None] = mapped_column(Date, nullable=True)


class GithubStarHistory(TimestampMixin, SoftDeleteMixin, Base):
    """GitHub 레포 월별 누적 스타 히스토리(백필, repo x 월). 백필 불가 데이터 — 있는 그대로 적재."""

    __tablename__ = "github_star_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    stargazers_count: Mapped[int] = mapped_column(Integer, nullable=False)
