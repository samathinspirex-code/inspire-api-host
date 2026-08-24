from typing import TYPE_CHECKING, Optional

from sqlalchemy import String, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.modules.cms.models.topic import Topic
    from app.modules.cms.models.outcome import Outcome


class Program(Base):
    __tablename__ = "programs"

    program_id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    level: Mapped[str] = mapped_column(String(100), nullable=False)
    school: Mapped[str] = mapped_column(String(100), nullable=False)
    awarding_body: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    duration: Mapped[str] = mapped_column(String(100), nullable=False)
    price_from: Mapped[int] = mapped_column(Integer, nullable=False)
    tag: Mapped[Optional[str]] = mapped_column(String(100))
    icon: Mapped[str] = mapped_column(String(100), nullable=False)
    image_label: Mapped[str] = mapped_column(String(100), nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(Text)
    blurb: Mapped[str] = mapped_column(Text, nullable=False)
    popularity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    topics: Mapped[list["Topic"]] = relationship(back_populates="program", cascade="all, delete-orphan")
    outcomes: Mapped[list["Outcome"]] = relationship(back_populates="program", cascade="all, delete-orphan")
