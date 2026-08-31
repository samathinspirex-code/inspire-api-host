from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.cms.models import MediaAsset
from app.modules.lms.models import (
    ClassStudent,
    CourseEnrollment,
    CourseLecturer,
    LmsClass,
    LmsCourse,
    LmsCourseworkAssignment,
    LmsCourseworkSubmission,
    LmsExam,
    StudentProfile,
)


class CourseworkRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_for_lecturer(self, user_id: int):
        stmt = (
            select(LmsCourseworkAssignment, LmsCourse, LmsClass)
            .join(LmsCourse, LmsCourse.course_id == LmsCourseworkAssignment.course_id)
            .join(
                CourseLecturer,
                and_(
                    CourseLecturer.course_id == LmsCourseworkAssignment.course_id,
                    CourseLecturer.lecturer_user_id == user_id,
                ),
            )
            .outerjoin(
                LmsClass,
                and_(
                    LmsCourseworkAssignment.target_type == "class",
                    LmsClass.class_id == LmsCourseworkAssignment.target_id,
                ),
            )
            .where(~exists(select(LmsExam.exam_id).where(LmsExam.assignment_id == LmsCourseworkAssignment.assignment_id)))
            .order_by(LmsCourseworkAssignment.created_at.desc())
        )
        return list((await self.db.execute(stmt)).all())

    async def list_for_student(self, user_id: int, include_exams: bool = False):
        class_access = select(ClassStudent.class_id).where(ClassStudent.student_user_id == user_id)
        stmt = (
            select(LmsCourseworkAssignment, LmsCourse, LmsClass, LmsCourseworkSubmission)
            .join(LmsCourse, LmsCourse.course_id == LmsCourseworkAssignment.course_id)
            .join(
                CourseEnrollment,
                and_(
                    CourseEnrollment.course_id == LmsCourseworkAssignment.course_id,
                    CourseEnrollment.student_user_id == user_id,
                    CourseEnrollment.status == "enrolled",
                ),
            )
            .outerjoin(
                LmsClass,
                and_(
                    LmsCourseworkAssignment.target_type == "class",
                    LmsClass.class_id == LmsCourseworkAssignment.target_id,
                ),
            )
            .outerjoin(
                LmsCourseworkSubmission,
                and_(
                    LmsCourseworkSubmission.assignment_id == LmsCourseworkAssignment.assignment_id,
                    LmsCourseworkSubmission.student_user_id == user_id,
                ),
            )
            .where(
                LmsCourseworkAssignment.status.in_(("published", "closed")),
                or_(
                    LmsCourseworkAssignment.target_type == "course",
                    and_(
                        LmsCourseworkAssignment.target_type == "class",
                        LmsCourseworkAssignment.target_id.in_(class_access),
                    ),
                ),
            )
            .order_by(LmsCourseworkAssignment.due_at.asc().nullslast(), LmsCourseworkAssignment.created_at.desc())
        )
        if not include_exams:
            stmt = stmt.where(
                ~exists(select(LmsExam.exam_id).where(LmsExam.assignment_id == LmsCourseworkAssignment.assignment_id))
            )
        return list((await self.db.execute(stmt)).all())

    async def get_assignment(self, assignment_id: int):
        return await self.db.get(LmsCourseworkAssignment, assignment_id)

    async def get_assignment_context(self, assignment_id: int):
        stmt = (
            select(LmsCourseworkAssignment, LmsCourse, LmsClass)
            .join(LmsCourse, LmsCourse.course_id == LmsCourseworkAssignment.course_id)
            .outerjoin(
                LmsClass,
                and_(
                    LmsCourseworkAssignment.target_type == "class",
                    LmsClass.class_id == LmsCourseworkAssignment.target_id,
                ),
            )
            .where(LmsCourseworkAssignment.assignment_id == assignment_id)
        )
        return (await self.db.execute(stmt)).one_or_none()

    async def get_submission(self, assignment_id: int, student_user_id: int, for_update: bool = False):
        stmt = select(LmsCourseworkSubmission).where(
            LmsCourseworkSubmission.assignment_id == assignment_id,
            LmsCourseworkSubmission.student_user_id == student_user_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_submissions(self, assignment_id: int):
        stmt = (
            select(LmsCourseworkSubmission, User, StudentProfile, MediaAsset)
            .join(User, User.user_id == LmsCourseworkSubmission.student_user_id)
            .join(StudentProfile, StudentProfile.user_id == LmsCourseworkSubmission.student_user_id)
            .outerjoin(MediaAsset, MediaAsset.media_asset_id == LmsCourseworkSubmission.attachment_asset_id)
            .where(LmsCourseworkSubmission.assignment_id == assignment_id)
            .order_by(LmsCourseworkSubmission.started_at.desc())
        )
        return list((await self.db.execute(stmt)).all())

    async def get_submission_context(self, submission_id: int):
        stmt = (
            select(LmsCourseworkSubmission, LmsCourseworkAssignment)
            .join(
                LmsCourseworkAssignment,
                LmsCourseworkAssignment.assignment_id == LmsCourseworkSubmission.assignment_id,
            )
            .where(LmsCourseworkSubmission.submission_id == submission_id)
        )
        return (await self.db.execute(stmt)).one_or_none()
