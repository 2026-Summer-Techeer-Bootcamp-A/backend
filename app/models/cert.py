from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin


class Cert(TimestampMixin, SoftDeleteMixin, Base):
    """자격증 사전 — 정규 자격증명."""

    __tablename__ = "cert"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
