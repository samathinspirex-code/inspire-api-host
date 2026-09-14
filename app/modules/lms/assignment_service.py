from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.lms.repository import AssignmentRepository, ClassRepository, CourseRepository, PeopleRepository
from app.modules.lms.schemas import AssignmentListResponse, AssignmentPersonItem


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
    class_ = await ClassRepository(db).get_for_update(class_id)
    if class_ is None:
        raise NotFoundError(f"Class {class_id} not found")
    repo = AssignmentRepository(db)
    existing_ids = {
        user_id for user_id in unique_user_ids
        if await repo.get_class_student(class_id, user_id) is not None
    }
    new_user_ids = [user_id for user_id in unique_user_ids if user_id not in existing_ids]
    if await repo.count_class_students(class_id) + len(new_user_ids) > class_.capacity:
        raise ConflictError("The selected students exceed this class capacity")
    people = PeopleRepository(db)
    for user_id in new_user_ids:
        user = await people.get_user(user_id)
        profile = await people.get_student_profile(user_id)
        if user is None or profile is None:
            raise NotFoundError(f"Student {user_id} not found")
        if not user.is_active:
            raise ValidationError(f"{user.full_name or user.email} is inactive and cannot be assigned")
    for user_id in new_user_ids:
        await assign_class_student(db, class_id, user_id, assigned_by)
    return await list_class_students(db, class_id)


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


async def remove_class_lecturer(db: AsyncSession, class_id: int, user_id: int) -> None:
    repo = AssignmentRepository(db)
    relation = await repo.get_class_lecturer(class_id, user_id)
    if relation is None:
        raise NotFoundError("Class lecturer assignment not found")
    await repo.remove(relation)
