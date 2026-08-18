from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.lms.models import LmsLearningItem, LmsLearningProgress, LmsModule


class ProgressRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, learning_item_id: int, student_user_id: int) -> LmsLearningProgress | None:
        stmt = select(LmsLearningProgress).where(
            LmsLearningProgress.learning_item_id == learning_item_id,
            LmsLearningProgress.student_user_id == student_user_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def save(
        self, learning_item_id: int, student_user_id: int, data: dict[str, Any]
    ) -> LmsLearningProgress:
        progress = await self.get(learning_item_id, student_user_id)
        if progress is None:
            progress = LmsLearningProgress(
                learning_item_id=learning_item_id,
                student_user_id=student_user_id,
                **data,
            )
            self.db.add(progress)
        else:
            for field, value in data.items():
                setattr(progress, field, value)
        await self.db.commit()
        await self.db.refresh(progress)
        return progress

    async def list_course_progress(
        self, course_id: int, student_user_id: int
    ) -> dict[int, LmsLearningProgress]:
        stmt = (
            select(LmsLearningProgress)
            .join(LmsLearningItem, LmsLearningItem.learning_item_id == LmsLearningProgress.learning_item_id)
            .join(LmsModule, LmsModule.module_id == LmsLearningItem.module_id)
            .where(
                LmsModule.course_id == course_id,
                LmsLearningProgress.student_user_id == student_user_id,
            )
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return {row.learning_item_id: row for row in rows}
