from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.modules.auth.models.access_level import AccessLevel
    from app.modules.auth.models.user import User


class UserAccessLevel(Base):
    __tablename__ = "user_access_levels"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    access_level_id: Mapped[int] = mapped_column(
        ForeignKey("access_levels.access_level_id", ondelete="CASCADE"), primary_key=True
    )
    assigned_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.user_id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="access_levels", foreign_keys=[user_id])
    access_level: Mapped["AccessLevel"] = relationship()
