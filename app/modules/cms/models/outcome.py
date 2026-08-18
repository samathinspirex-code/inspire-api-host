from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.modules.cms.models.program import Program


class Outcome(Base):
    __tablename__ = "outcomes"
    __table_args__ = (
        UniqueConstraint("program_id", "order", name="uq_outcomes_program_id_order"),
        Index("idx_outcomes_program_id", "program_id"),
    )

    outcome_id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.program_id", ondelete="CASCADE"), nullable=False)
    order: Mapped[int] = mapped_column(nullable=False)
    outcome: Mapped[str] = mapped_column(String(255), nullable=False)

    program: Mapped["Program"] = relationship(back_populates="outcomes")
