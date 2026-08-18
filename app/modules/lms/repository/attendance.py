from datetime import date, datetime, timezone

from sqlalchemy import Date, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.auth.models import User
from app.modules.cms.models import Program
from app.modules.lms.models import (
    AttendanceRecord,
    AttendanceSession,
    ClassStudent,
    LmsClass,
    LmsCourse,
    LecturerProfile,
    OnlineMeeting,
    StudentProfile,
)

StudentUser = aliased(User, name="attendance_student")
LecturerUser = aliased(User, name="attendance_lecturer")


class AttendanceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_class_students(self, class_id: int, meeting_end_time: datetime):
        stmt = (
            select(User, StudentProfile)
            .join(StudentProfile, StudentProfile.user_id == User.user_id)
            .join(ClassStudent, ClassStudent.student_user_id == User.user_id)
            .where(
                ClassStudent.class_id == class_id,
                ClassStudent.assigned_at <= meeting_end_time,
                User.is_active.is_(True),
            )
            .order_by(User.full_name, User.email)
        )
        return list((await self.db.execute(stmt)).all())

    async def get_session_by_meeting(self, meeting_id: int) -> AttendanceSession | None:
        stmt = select(AttendanceSession).where(AttendanceSession.meeting_id == meeting_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def save_failed_sync(
        self, meeting: OnlineMeeting, threshold: int, synced_by: int, message: str
    ) -> AttendanceSession:
        session = await self.get_session_by_meeting(meeting.meeting_id)
        if session is None:
            session = AttendanceSession(
                meeting_id=meeting.meeting_id,
                class_id=meeting.class_id,
                threshold_percentage=threshold,
            )
            self.db.add(session)
        session.sync_status = "failed"
        session.sync_error = message
        session.synced_by = synced_by
        session.synced_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def save_google_sync(
        self,
        meeting: OnlineMeeting,
        threshold: int,
        conference_name: str,
        actual_start: datetime,
        actual_end: datetime,
        unmatched: list[dict],
        records: list[dict],
        synced_by: int,
    ) -> AttendanceSession:
        session = await self.get_session_by_meeting(meeting.meeting_id)
        if session is None:
            session = AttendanceSession(meeting_id=meeting.meeting_id, class_id=meeting.class_id)
            self.db.add(session)
            await self.db.flush()

        session.google_conference_record_name = conference_name
        session.actual_start_time = actual_start
        session.actual_end_time = actual_end
        session.threshold_percentage = threshold
        session.sync_status = "synced"
        session.sync_error = None
        session.unmatched_participants = unmatched
        session.synced_by = synced_by
        session.synced_at = datetime.now(timezone.utc)

        for data in records:
            stmt = select(AttendanceRecord).where(
                AttendanceRecord.attendance_session_id == session.attendance_session_id,
                AttendanceRecord.student_user_id == data["student_user_id"],
            )
            item = (await self.db.execute(stmt)).scalar_one_or_none()
            if item is None:
                item = AttendanceRecord(
                    attendance_session_id=session.attendance_session_id,
                    student_user_id=data["student_user_id"],
                    status=data["status"],
                )
                self.db.add(item)
            elif item.source != "manual_override":
                item.status = data["status"]
                item.source = "google_meet"

            item.attended_seconds = data["attended_seconds"]
            item.attendance_percentage = data["attendance_percentage"]
            item.first_join_time = data["first_join_time"]
            item.last_leave_time = data["last_leave_time"]
            item.google_participant_name = data["google_participant_name"]

        meeting.status = "completed"
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def get_session_context(self, meeting_id: int):
        stmt = (
            select(AttendanceSession, OnlineMeeting, LmsClass, LmsCourse)
            .join(OnlineMeeting, OnlineMeeting.meeting_id == AttendanceSession.meeting_id)
            .join(LmsClass, LmsClass.class_id == AttendanceSession.class_id)
            .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
            .where(AttendanceSession.meeting_id == meeting_id)
        )
        return (await self.db.execute(stmt)).one_or_none()

    async def list_session_records(self, attendance_session_id: int):
        stmt = (
            select(AttendanceRecord, User, StudentProfile)
            .join(User, User.user_id == AttendanceRecord.student_user_id)
            .join(StudentProfile, StudentProfile.user_id == AttendanceRecord.student_user_id)
            .where(AttendanceRecord.attendance_session_id == attendance_session_id)
            .order_by(User.full_name, User.email)
        )
        return list((await self.db.execute(stmt)).all())

    async def get_record_context(self, attendance_record_id: int):
        stmt = (
            select(AttendanceRecord, AttendanceSession, OnlineMeeting, User, StudentProfile)
            .join(AttendanceSession, AttendanceSession.attendance_session_id == AttendanceRecord.attendance_session_id)
            .join(OnlineMeeting, OnlineMeeting.meeting_id == AttendanceSession.meeting_id)
            .join(User, User.user_id == AttendanceRecord.student_user_id)
            .join(StudentProfile, StudentProfile.user_id == AttendanceRecord.student_user_id)
            .where(AttendanceRecord.attendance_record_id == attendance_record_id)
        )
        return (await self.db.execute(stmt)).one_or_none()

    async def override_record(
        self, record: AttendanceRecord, status: str, reason: str, overridden_by: int
    ) -> AttendanceRecord:
        record.status = status
        record.source = "manual_override"
        record.override_reason = reason
        record.overridden_by = overridden_by
        record.overridden_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def list_student_attendance(self, student_user_id: int):
        stmt = (
            select(AttendanceRecord, AttendanceSession, OnlineMeeting, LmsClass, LmsCourse)
            .join(AttendanceSession, AttendanceSession.attendance_session_id == AttendanceRecord.attendance_session_id)
            .join(OnlineMeeting, OnlineMeeting.meeting_id == AttendanceSession.meeting_id)
            .join(LmsClass, LmsClass.class_id == AttendanceSession.class_id)
            .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
            .where(
                AttendanceRecord.student_user_id == student_user_id,
                AttendanceSession.sync_status == "synced",
            )
            .order_by(OnlineMeeting.start_time.desc())
        )
        return list((await self.db.execute(stmt)).all())

    @staticmethod
    def _report_base(stmt):
        return (
            stmt.select_from(AttendanceRecord).join(
                AttendanceSession,
                AttendanceSession.attendance_session_id == AttendanceRecord.attendance_session_id,
            )
            .join(OnlineMeeting, OnlineMeeting.meeting_id == AttendanceSession.meeting_id)
            .join(LmsClass, LmsClass.class_id == AttendanceSession.class_id)
            .join(LmsCourse, LmsCourse.course_id == LmsClass.course_id)
            .join(Program, Program.program_id == LmsCourse.program_id)
            .join(StudentUser, StudentUser.user_id == AttendanceRecord.student_user_id)
            .join(StudentProfile, StudentProfile.user_id == AttendanceRecord.student_user_id)
            .join(LecturerUser, LecturerUser.user_id == OnlineMeeting.lecturer_user_id)
            .join(LecturerProfile, LecturerProfile.user_id == OnlineMeeting.lecturer_user_id)
        )

    @staticmethod
    def _report_conditions(
        lecturer_scope_user_id: int | None = None,
        program_id: int | None = None,
        course_id: int | None = None,
        class_id: int | None = None,
        student_user_id: int | None = None,
        lecturer_user_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> list:
        conditions = [AttendanceSession.sync_status == "synced"]
        if lecturer_scope_user_id is not None:
            conditions.append(OnlineMeeting.lecturer_user_id == lecturer_scope_user_id)
        if program_id is not None:
            conditions.append(LmsCourse.program_id == program_id)
        if course_id is not None:
            conditions.append(LmsCourse.course_id == course_id)
        if class_id is not None:
            conditions.append(LmsClass.class_id == class_id)
        if student_user_id is not None:
            conditions.append(AttendanceRecord.student_user_id == student_user_id)
        if lecturer_user_id is not None:
            conditions.append(OnlineMeeting.lecturer_user_id == lecturer_user_id)
        if date_from is not None:
            conditions.append(cast(OnlineMeeting.start_time, Date) >= date_from)
        if date_to is not None:
            conditions.append(cast(OnlineMeeting.start_time, Date) <= date_to)
        if status is not None:
            conditions.append(AttendanceRecord.status == status)
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            conditions.append(
                or_(
                    StudentUser.full_name.ilike(pattern),
                    StudentUser.email.ilike(pattern),
                    StudentProfile.student_number.ilike(pattern),
                    OnlineMeeting.title.ilike(pattern),
                    LmsClass.code.ilike(pattern),
                    LmsClass.name.ilike(pattern),
                    LmsCourse.code.ilike(pattern),
                    LmsCourse.title.ilike(pattern),
                )
            )
        return conditions

    async def list_report(
        self,
        *,
        lecturer_scope_user_id: int | None = None,
        program_id: int | None = None,
        course_id: int | None = None,
        class_id: int | None = None,
        student_user_id: int | None = None,
        lecturer_user_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        status: str | None = None,
        search: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ):
        conditions = self._report_conditions(
            lecturer_scope_user_id,
            program_id,
            course_id,
            class_id,
            student_user_id,
            lecturer_user_id,
            date_from,
            date_to,
            status,
            search,
        )
        stmt = self._report_base(
            select(
                AttendanceRecord,
                AttendanceSession,
                OnlineMeeting,
                LmsClass,
                LmsCourse,
                Program,
                StudentUser,
                StudentProfile,
                LecturerUser,
                LecturerProfile,
            )
        ).where(*conditions).order_by(
            OnlineMeeting.start_time.desc(), StudentUser.full_name, StudentUser.email
        )
        if offset is not None:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self.db.execute(stmt)).all())

    async def report_summary(self, **filters):
        conditions = self._report_conditions(**filters)
        stmt = self._report_base(
            select(
                func.count(AttendanceRecord.attendance_record_id),
                func.coalesce(
                    func.sum(case((AttendanceRecord.status == "present", 1), else_=0)), 0
                ),
                func.coalesce(
                    func.sum(case((AttendanceRecord.status == "absent", 1), else_=0)), 0
                ),
                func.coalesce(func.avg(AttendanceRecord.attendance_percentage), 0),
                func.count(func.distinct(AttendanceRecord.student_user_id)),
                func.count(func.distinct(AttendanceSession.meeting_id)),
            )
        ).where(*conditions)
        return (await self.db.execute(stmt)).one()

    async def report_options(self, lecturer_scope_user_id: int | None = None) -> dict[str, list]:
        conditions = self._report_conditions(lecturer_scope_user_id=lecturer_scope_user_id)

        async def distinct_rows(*columns):
            stmt = self._report_base(select(*columns)).where(*conditions).distinct().order_by(*columns)
            return list((await self.db.execute(stmt)).all())

        return {
            "programmes": await distinct_rows(Program.program_id, Program.code, Program.title),
            "courses": await distinct_rows(LmsCourse.course_id, LmsCourse.code, LmsCourse.title),
            "classes": await distinct_rows(LmsClass.class_id, LmsClass.code, LmsClass.name),
            "lecturers": await distinct_rows(
                LecturerUser.user_id,
                LecturerProfile.staff_number,
                LecturerUser.full_name,
                LecturerUser.email,
            ),
            "students": await distinct_rows(
                StudentUser.user_id,
                StudentProfile.student_number,
                StudentUser.full_name,
                StudentUser.email,
            ),
        }
