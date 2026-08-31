"""Small, uncached aggregates for the CMS home; never load student rosters."""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.cms.models import Program
from app.modules.cms.schemas.dashboard import CmsDashboardResponse, DashboardProgram
from app.modules.lms.models import LmsCourse, LmsLearningItem, LmsModule, StudentProfile


async def get_dashboard(db: AsyncSession) -> CmsDashboardResponse:
    # Match the CMS program catalogue and LMS student registry. Count people,
    # not enrolments: one student may belong to multiple courses or no course.
    totals = (await db.execute(select(
        select(func.count()).select_from(Program).scalar_subquery().label("programs"),
        select(func.count()).select_from(StudentProfile)
        .join(User, User.user_id == StudentProfile.user_id)
        .scalar_subquery().label("students"),
    ))).one()

    # Count the items INSIDE course sections, once each, by their saved status.
    # Audience releases/enrolments must not multiply this inventory count.
    content = (await db.execute(
        select(LmsLearningItem.status, LmsLearningItem.item_type, func.count())
        .join(LmsModule, LmsModule.module_id == LmsLearningItem.module_id)
        .join(LmsCourse, LmsCourse.course_id == LmsModule.course_id)
        .group_by(LmsLearningItem.status, LmsLearningItem.item_type)
    )).all()
    published_by_type = {kind: 0 for kind in ("video", "pdf", "text", "link", "assignment", "quiz")}
    draft_content = 0
    for status, kind, count in content:
        if status == "published":
            published_by_type[kind] = count
        elif status == "draft":
            draft_content += count

    # Program has no created_at/status column. Highest IDs are the newest
    # additions; do not invent publication states or monthly growth figures.
    programs = (await db.execute(
        select(Program.program_id, Program.title, Program.school, Program.level, Program.awarding_body)
        .order_by(Program.program_id.desc()).limit(5)
    )).all()
    return CmsDashboardResponse(
        total_programs=totals.programs,
        total_students=totals.students,
        published_content=sum(published_by_type.values()),
        draft_content=draft_content,
        published_by_type=published_by_type,
        recent_programs=[DashboardProgram.model_validate(program) for program in programs],
        generated_at=datetime.now(timezone.utc),
    )
