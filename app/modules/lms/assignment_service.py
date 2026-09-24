from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.auth.models import User
from app.modules.lms.models import ClassLecturer, ClassStudent, CourseEnrollment, CourseLecturer, LecturerProfile, StudentProfile
from app.modules.lms.repository import AssignmentRepository, ClassRepository, CourseRepository, PeopleRepository
from app.modules.lms.schemas import AssignmentListResponse, AssignmentPersonItem
from app.modules.lms.student_excel_import import parse_excel_students


def _student_item(user, profile, relation, status: str) -> AssignmentPersonItem:
    assigned_at = getattr(relation, "enrolled_at", None) or relation.assigned_at
    return AssignmentPersonItem(
        user_id=user.user_id,
        full_name=user.full_name or "",
        email=user.email,
        reference_number=profile.student_number,
        secondary_label=profile.phone,
        profile_image_url=profile.profile_image_url,
        status=status,
        assigned_at=assigned_at,
    )


def _lecturer_item(user, profile, relation) -> AssignmentPersonItem:
    return AssignmentPersonItem(
        user_id=user.user_id,
        full_name=user.full_name or "",
        email=user.email,
        reference_number=profile.staff_number,
        secondary_label=profile.job_title,
        profile_image_url=profile.profile_image_url,
        status="assigned",
        assigned_at=relation.assigned_at,
    )


async def list_course_students(db: AsyncSession, course_id: int) -> AssignmentListResponse:
    if await CourseRepository(db).get(course_id) is None:
        raise NotFoundError(f"Course {course_id} not found")
    rows = await AssignmentRepository(db).list_course_students(course_id)
    return AssignmentListResponse(
        data=[_student_item(user, profile, relation, relation.status) for user, profile, relation in rows],
        assigned_count=len(rows),
    )


async def enroll_student(db: AsyncSession, course_id: int, user_id: int, assigned_by: int) -> AssignmentPersonItem:
    if await CourseRepository(db).get(course_id) is None:
        raise NotFoundError(f"Course {course_id} not found")
    people = PeopleRepository(db)
    user = await people.get_user(user_id)
    profile = await people.get_student_profile(user_id)
    if user is None or profile is None:
        raise NotFoundError(f"Student {user_id} not found")
    if not user.is_active:
        raise ValidationError("Inactive students cannot be enrolled")
    repo = AssignmentRepository(db)
    existing = await repo.get_enrollment(course_id, user_id)
    if existing is not None and existing.status == "enrolled":
        raise ConflictError("Student is already enrolled in this course")
    relation = await repo.enroll_student(course_id, user_id, assigned_by)
    return _student_item(user, profile, relation, relation.status)


async def withdraw_student(db: AsyncSession, course_id: int, user_id: int) -> None:
    repo = AssignmentRepository(db)
    enrollment = await repo.get_enrollment(course_id, user_id)
    if enrollment is None or enrollment.status != "enrolled":
        raise NotFoundError("Active course enrolment not found")
    await repo.withdraw_student(enrollment)


async def list_course_lecturers(db: AsyncSession, course_id: int) -> AssignmentListResponse:
    if await CourseRepository(db).get(course_id) is None:
        raise NotFoundError(f"Course {course_id} not found")
    rows = await AssignmentRepository(db).list_course_lecturers(course_id)
    return AssignmentListResponse(
        data=[_lecturer_item(user, profile, relation) for user, profile, relation in rows],
        assigned_count=len(rows),
    )


async def assign_course_lecturer(
    db: AsyncSession, course_id: int, user_id: int, assigned_by: int
) -> AssignmentPersonItem:
    if await CourseRepository(db).get(course_id) is None:
        raise NotFoundError(f"Course {course_id} not found")
    people = PeopleRepository(db)
    user = await people.get_user(user_id)
    profile = await people.get_lecturer_profile(user_id)
    if user is None or profile is None:
        raise NotFoundError(f"Lecturer {user_id} not found")
    if not user.is_active:
        raise ValidationError("Inactive lecturers cannot be assigned")
    repo = AssignmentRepository(db)
    if await repo.get_course_lecturer(course_id, user_id) is not None:
        raise ConflictError("Lecturer is already assigned to this course")
    relation = await repo.assign_course_lecturer(course_id, user_id, assigned_by)
    return _lecturer_item(user, profile, relation)


async def remove_course_lecturer(db: AsyncSession, course_id: int, user_id: int) -> None:
    repo = AssignmentRepository(db)
    relation = await repo.get_course_lecturer(course_id, user_id)
    if relation is None:
        raise NotFoundError("Course lecturer assignment not found")
    await repo.remove_course_lecturer(relation)


async def list_class_students(db: AsyncSession, class_id: int) -> AssignmentListResponse:
    class_ = await ClassRepository(db).get(class_id)
    if class_ is None:
        raise NotFoundError(f"Class {class_id} not found")
    rows = await AssignmentRepository(db).list_class_students(class_id)
    return AssignmentListResponse(
        data=[_student_item(user, profile, relation, "assigned") for user, profile, relation in rows],
        capacity=class_.capacity,
        assigned_count=len(rows),
    )


async def assign_class_student(db: AsyncSession, class_id: int, user_id: int, assigned_by: int) -> AssignmentPersonItem:
    # Lock the class until the assignment commits so concurrent requests cannot
    # both pass the capacity check and overfill the cohort.
    class_ = await ClassRepository(db).get_for_update(class_id)
    if class_ is None:
        raise NotFoundError(f"Class {class_id} not found")
    people = PeopleRepository(db)
    user = await people.get_user(user_id)
    profile = await people.get_student_profile(user_id)
    if user is None or profile is None:
        raise NotFoundError(f"Student {user_id} not found")
    if not user.is_active:
        raise ValidationError("Inactive students cannot be assigned")
    repo = AssignmentRepository(db)
    # A class is the final point in the academic pathway.  Enrolling directly
    # into a class therefore records the School → Programme → Level → Course →
    # Study Mode → Class relationship as well as granting LMS access.
    if class_.academic_course_id is not None and class_.study_mode is not None:
        academic_enrolment = await db.scalar(text("""
            SELECT course_enrolment_id FROM academic_course_enrolments
            WHERE student_user_id=:user_id AND class_id=:class_id
            LIMIT 1
        """), {"user_id": user_id, "class_id": class_id})
        if academic_enrolment is None:
            option = (await db.execute(text("""
                SELECT price, duration FROM academic_course_study_options
                WHERE course_id=:course_id AND study_mode=:study_mode AND is_enabled
                LIMIT 1
            """), {"course_id": class_.academic_course_id, "study_mode": class_.study_mode})).mappings().first()
            if option is None:
                raise ValidationError("This class does not have an enabled study mode in the academic catalogue")
            await db.execute(text("""
                INSERT INTO academic_course_enrolments
                  (student_user_id, course_id, study_mode, class_id, agreed_price, agreed_duration, status, confirmed_by)
                VALUES (:user_id, :course_id, :study_mode, :class_id, :price, :duration, 'active', :assigned_by)
                ON CONFLICT DO NOTHING
            """), {
                "user_id": user_id, "course_id": class_.academic_course_id,
                "study_mode": class_.study_mode, "class_id": class_id,
                "price": option["price"], "duration": option["duration"], "assigned_by": assigned_by,
            })
    enrollment = await repo.get_enrollment(class_.course_id, user_id)
    if enrollment is None or enrollment.status != "enrolled":
        await repo.enroll_student(class_.course_id, user_id, assigned_by)
    if await repo.get_class_student(class_id, user_id) is not None:
        raise ConflictError("Student is already assigned to this class")
    if await repo.count_class_students(class_id) >= class_.capacity:
        raise ConflictError("Class capacity has been reached")
    relation = await repo.assign_class_student(class_id, user_id, assigned_by)
    return _student_item(user, profile, relation, "assigned")


async def assign_class_students_bulk(
    db: AsyncSession, class_id: int, user_ids: list[int], assigned_by: int
) -> AssignmentListResponse:
    unique_user_ids = list(dict.fromkeys(user_ids))
    # Lock the class until the batch commits so concurrent requests cannot both
    # pass the capacity check and overfill the cohort.
    class_ = await ClassRepository(db).get_for_update(class_id)
    if class_ is None:
        raise NotFoundError(f"Class {class_id} not found")
    if not unique_user_ids:
        return await list_class_students(db, class_id)

    # The database is remote, so the whole batch is validated and written with a
    # fixed number of set-based statements rather than a round trip per student.
    already_assigned = set((await db.execute(
        select(ClassStudent.student_user_id).where(
            ClassStudent.class_id == class_id,
            ClassStudent.student_user_id.in_(unique_user_ids),
        )
    )).scalars())
    new_user_ids = [user_id for user_id in unique_user_ids if user_id not in already_assigned]
    if not new_user_ids:
        return await list_class_students(db, class_id)

    students = {
        user.user_id: user
        for user in (await db.execute(
            select(User)
            .join(StudentProfile, StudentProfile.user_id == User.user_id)
            .where(User.user_id.in_(new_user_ids))
        )).scalars()
    }
    for user_id in new_user_ids:
        user = students.get(user_id)
        if user is None:
            raise NotFoundError(f"Student {user_id} not found")
        if not user.is_active:
            raise ValidationError(f"{user.full_name or user.email} is inactive and cannot be assigned")

    if await AssignmentRepository(db).count_class_students(class_id) + len(new_user_ids) > class_.capacity:
        raise ConflictError("The selected students exceed this class capacity")

    # A class is the final point in the academic pathway, so a class enrolment
    # also records the School → Programme → Level → Course → Study Mode → Class
    # relationship as well as granting LMS course access.
    if class_.academic_course_id is not None and class_.study_mode is not None:
        option = (await db.execute(text("""
            SELECT price, duration FROM academic_course_study_options
            WHERE course_id=:course_id AND study_mode=:study_mode AND is_enabled
            LIMIT 1
        """), {"course_id": class_.academic_course_id, "study_mode": class_.study_mode})).mappings().first()
        if option is None:
            raise ValidationError("This class does not have an enabled study mode in the academic catalogue")
        await db.execute(text("""
            INSERT INTO academic_course_enrolments
              (student_user_id, course_id, study_mode, class_id, agreed_price, agreed_duration, status, confirmed_by)
            VALUES (:user_id, :course_id, :study_mode, :class_id, :price, :duration, 'active', :assigned_by)
            ON CONFLICT DO NOTHING
        """), [
            {
                "user_id": user_id, "course_id": class_.academic_course_id,
                "study_mode": class_.study_mode, "class_id": class_id,
                "price": option["price"], "duration": option["duration"], "assigned_by": assigned_by,
            }
            for user_id in new_user_ids
        ])

    await db.execute(
        pg_insert(CourseEnrollment)
        .values([
            {"course_id": class_.course_id, "student_user_id": user_id, "status": "enrolled", "enrolled_by": assigned_by}
            for user_id in new_user_ids
        ])
        .on_conflict_do_update(
            index_elements=[CourseEnrollment.course_id, CourseEnrollment.student_user_id],
            set_={"status": "enrolled", "enrolled_by": assigned_by, "enrolled_at": func.now(), "updated_at": func.now()},
            # Re-enrol withdrawn students without resetting an active enrolment date.
            where=CourseEnrollment.status != "enrolled",
        )
    )
    await db.execute(
        pg_insert(ClassStudent)
        .values([
            {"class_id": class_id, "student_user_id": user_id, "assigned_by": assigned_by}
            for user_id in new_user_ids
        ])
        .on_conflict_do_nothing()
    )
    await db.commit()
    return await list_class_students(db, class_id)


async def copy_class_students(db: AsyncSession, target_class_id: int, source_class_id: int, assigned_by: int) -> AssignmentListResponse:
    if target_class_id == source_class_id:
        raise ValidationError("Choose a different source class")
    if await ClassRepository(db).get(source_class_id) is None:
        raise NotFoundError(f"Source class {source_class_id} not found")
    rows = await AssignmentRepository(db).list_class_students(source_class_id)
    return await assign_class_students_bulk(db, target_class_id, [user.user_id for user, _profile, _relation in rows], assigned_by)


async def enrol_class_students_from_excel(db: AsyncSession, class_id: int, content: bytes, assigned_by: int) -> AssignmentListResponse:
    rows, _sheet = parse_excel_students(content)
    invalid = [f"Row {row.row}: {', '.join(row.errors)}" for row in rows if row.errors]
    if invalid:
        raise ValidationError("; ".join(invalid[:5]))
    people = PeopleRepository(db)
    existing = await people.list_students(None)
    by_email = {user.email.lower(): user.user_id for user, _profile, _last in existing if user.email}
    by_number = {profile.student_number.upper(): user.user_id for user, profile, _last in existing if profile.student_number}
    user_ids = []
    missing = []
    for row in rows:
        user_id = by_email.get(row.email.lower()) if row.email else None
        if not user_id and row.student_number:
            user_id = by_number.get(row.student_number.upper())
        if user_id:
            user_ids.append(user_id)
        else:
            missing.append(row.email or row.full_name)
    if missing:
        raise ValidationError("These students are not registered in LMS: " + ", ".join(missing[:8]))
    return await assign_class_students_bulk(db, class_id, user_ids, assigned_by)


async def remove_class_student(db: AsyncSession, class_id: int, user_id: int) -> None:
    repo = AssignmentRepository(db)
    relation = await repo.get_class_student(class_id, user_id)
    if relation is None:
        raise NotFoundError("Class student assignment not found")
    class_ = await ClassRepository(db).get(class_id)
    await repo.remove(relation)
    if class_ is not None:
        still_assigned = await db.scalar(text("""
            SELECT 1 FROM lms_class_students cs
            JOIN lms_classes cl ON cl.class_id=cs.class_id
            WHERE cs.student_user_id=:user_id AND cl.course_id=:course_id
            LIMIT 1
        """), {"user_id": user_id, "course_id": class_.course_id})
        if not still_assigned:
            await db.execute(text("""
                UPDATE lms_course_enrollments SET status='withdrawn', updated_at=now()
                WHERE course_id=:course_id AND student_user_id=:user_id
            """), {"course_id": class_.course_id, "user_id": user_id})
        await db.execute(text("""
            DELETE FROM academic_course_enrolments
            WHERE class_id=:class_id AND student_user_id=:user_id
        """), {"class_id": class_id, "user_id": user_id})
        await db.commit()


async def list_class_lecturers(db: AsyncSession, class_id: int) -> AssignmentListResponse:
    class_ = await ClassRepository(db).get(class_id)
    if class_ is None:
        raise NotFoundError(f"Class {class_id} not found")
    rows = await AssignmentRepository(db).list_class_lecturers(class_id)
    return AssignmentListResponse(
        data=[_lecturer_item(user, profile, relation) for user, profile, relation in rows],
        assigned_count=len(rows),
    )


async def assign_class_lecturer(db: AsyncSession, class_id: int, user_id: int, assigned_by: int) -> AssignmentPersonItem:
    class_ = await ClassRepository(db).get(class_id)
    if class_ is None:
        raise NotFoundError(f"Class {class_id} not found")
    people = PeopleRepository(db)
    user = await people.get_user(user_id)
    profile = await people.get_lecturer_profile(user_id)
    if user is None or profile is None:
        raise NotFoundError(f"Lecturer {user_id} not found")
    if not user.is_active:
        raise ValidationError("Inactive lecturers cannot be assigned")
    repo = AssignmentRepository(db)
    if await repo.get_course_lecturer(class_.course_id, user_id) is None:
        await repo.assign_course_lecturer(class_.course_id, user_id, assigned_by)
    if await repo.get_class_lecturer(class_id, user_id) is not None:
        raise ConflictError("Lecturer is already assigned to this class")
    relation = await repo.assign_class_lecturer(class_id, user_id, assigned_by)
    return _lecturer_item(user, profile, relation)


async def assign_class_lecturers_bulk(db: AsyncSession, class_id: int, user_ids: list[int], assigned_by: int) -> AssignmentListResponse:
    unique_user_ids = list(dict.fromkeys(user_ids))
    class_ = await ClassRepository(db).get(class_id)
    if class_ is None:
        raise NotFoundError(f"Class {class_id} not found")
    if not unique_user_ids:
        return await list_class_lecturers(db, class_id)

    lecturers = {
        user.user_id: user
        for user in (await db.execute(
            select(User)
            .join(LecturerProfile, LecturerProfile.user_id == User.user_id)
            .where(User.user_id.in_(unique_user_ids))
        )).scalars()
    }
    for user_id in unique_user_ids:
        user = lecturers.get(user_id)
        if user is None:
            raise NotFoundError(f"Lecturer {user_id} not found")
        if not user.is_active:
            raise ValidationError(f"{user.full_name or user.email} is inactive and cannot be assigned")

    # Teaching an intake also grants access to its reusable Course page.
    # Lecturers already assigned are skipped rather than failing the batch.
    await db.execute(
        pg_insert(CourseLecturer)
        .values([
            {"course_id": class_.course_id, "lecturer_user_id": user_id, "assigned_by": assigned_by}
            for user_id in unique_user_ids
        ])
        .on_conflict_do_nothing()
    )
    await db.execute(
        pg_insert(ClassLecturer)
        .values([
            {"class_id": class_id, "lecturer_user_id": user_id, "assigned_by": assigned_by}
            for user_id in unique_user_ids
        ])
        .on_conflict_do_nothing()
    )
    await db.commit()
    return await list_class_lecturers(db, class_id)


async def remove_class_lecturer(db: AsyncSession, class_id: int, user_id: int) -> None:
    repo = AssignmentRepository(db)
    relation = await repo.get_class_lecturer(class_id, user_id)
    if relation is None:
        raise NotFoundError("Class lecturer assignment not found")
    await repo.remove(relation)
