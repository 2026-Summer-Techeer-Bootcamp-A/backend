from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin


class Concept(TimestampMixin, SoftDeleteMixin, Base):
    """개념·패러다임 사전 — 기술축과 별개의 4번째 축.

    MSA·대규모 트래픽·생성형 AI·CI/CD 등 공고에 빈출하는 개념을 정규화한다.
    collector/concepts_taxonomy.json의 DB 반영본. 별칭은 마트 적재 시 이미 해소되므로
    별칭 테이블 없이 정규명(name) + 상위 분류(category)만 둔다.
    """

    __tablename__ = "concept"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
