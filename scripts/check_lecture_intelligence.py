"""Read-only smoke check for the lecture-content and question-bank services."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.modules.lms import assistant_service  # noqa: E402


async def check() -> None:
    async with AsyncSessionLocal() as db:
        courses = await assistant_service.list_admin_courses(db)
        print(f"Courses available: {len(courses.data)}")
        if not courses.data:
            return
        course_id = courses.data[0].course_id
        detail = await assistant_service.get_admin_course(db, course_id)
        questions = await assistant_service.list_questions(db, course_id)
        print(f"Course {course_id}: {len(detail.sources)} materials, {len(questions.data)} questions")


if __name__ == "__main__":
    asyncio.run(check())
