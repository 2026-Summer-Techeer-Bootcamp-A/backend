from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Person(Base):
    __tablename__ = "person"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
