from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.auth.schemas import CurrentUser
from app.modules.auth import service as auth_service
from app.modules.auth.repository import AuthenticatorRepository, RefreshTokenRepository
from app.modules.cms.models import Program
from app.modules.cms.repository import ProgramRepository
from app.modules.lms.repository import ClassRepository, CourseRepository, ModuleRepository, PeopleRepository
from app.modules.lms.schemas import (
    CourseCreate,
    CourseItem,
    CourseListResponse,
    CourseUpdate,
    ClassCreate,
    ClassItem,
    ClassListResponse,
    ClassUpdate,
    LecturerCreate,
    LecturerItem,
    LecturerListResponse,
    LecturerUpdate,
    LmsBootstrapResponse,
    NavigationItem,
    ModuleCreate,
    ModuleItem,
    ModuleListResponse,
    ModuleUpdate,
    Pagination,
    ProgrammeListResponse,
    ProgrammeSummary,
    StudentCreate,
    StudentItem,
    StudentListResponse,
    StudentUpdate,
)

ROLE_PRIORITY = ("SUPER_ADMIN", "ADMIN", "LECTURER", "STUDENT")

ROLE_LABELS = {
    "SUPER_ADMIN": "Super Admin",
    "ADMIN": "Admin",
    "LECTURER": "Lecturer",
    "STUDENT": "Student",
}

COMMON_NAVIGATION = [
    NavigationItem(key="dashboard", label="Dashboard", icon="home"),
    NavigationItem(key="calendar", label="Calendar", icon="calendar"),
    NavigationItem(key="notifications", label="Notifications", icon="bell"),
]

ROLE_NAVIGATION = {
    "SUPER_ADMIN": [
        ("users", "Users", "users"),
        ("programmes", "Programmes", "layers"),
        ("courses", "Courses", "book"),
        ("classes", "Classes", "video"),
        ("enrolments", "Enrolments", "user-check"),
        ("attendance", "Attendance", "clipboard"),
        ("reports", "Reports & Exports", "chart"),
        ("announcements", "Announcements", "megaphone"),
        ("settings", "Settings", "settings"),
    ],
    "ADMIN": [
        ("students", "Students", "users"),
        ("lecturers", "Lecturers", "presentation"),
        ("programmes", "Programmes", "layers"),
        ("courses", "Courses", "book"),
        ("classes", "Classes", "video"),
        ("enrolments", "Enrolments", "user-check"),
        ("attendance", "Attendance", "clipboard"),
        ("reports", "Reports", "chart"),
        ("announcements", "Announcements", "megaphone"),
    ],
    "LECTURER": [
        ("profile", "My Profile", "user"),
        ("my-courses", "My Courses", "book"),
        ("my-classes", "My Classes", "video"),
        ("assignments", "Assignments", "file"),
        ("exams", "Exams", "clipboard"),
        ("grades", "Gradebook", "award"),
        ("attendance", "Attendance", "clipboard"),
        ("reports", "Attendance Reports", "chart"),
        ("meetings", "Online Meetings", "camera"),
        ("announcements", "Announcements", "megaphone"),
    ],
    "STUDENT": [
        ("profile", "My Profile", "user"),
        ("my-courses", "My Courses", "book"),
        ("my-classes", "My Classes", "video"),
        ("meetings", "Online Classes", "camera"),
        ("assignments", "Assignments", "file"),
        ("exams", "Exams", "clipboard"),
        ("attendance", "Attendance", "clipboard"),
        ("grades", "Grades", "award"),
        ("reports", "My Activity", "chart"),
        ("announcements", "Announcements", "megaphone"),
    ],
}

def resolve_role(access: list[str]) -> str | None:
    return next((role for role in ROLE_PRIORITY if role in access), None)


def build_bootstrap(current_user: CurrentUser) -> LmsBootstrapResponse:
    if "LMS" not in current_user.access:
        raise ValueError("User does not have LMS access")

    role = resolve_role(current_user.access)
    if role is None:
        raise ValueError("User does not have an LMS role")

    navigation = [*COMMON_NAVIGATION]
    navigation[1:1] = [
        NavigationItem(key=key, label=label, icon=icon)
        for key, label, icon in ROLE_NAVIGATION[role]
    ]

    return LmsBootstrapResponse(
        role=role,
        role_label=ROLE_LABELS[role],
        navigation=navigation,
        # Dashboard data is loaded independently so navigation stays lightweight.
        metrics=[],
        enabled_features=[item.key for item in navigation],
    )


async def list_programmes(db: AsyncSession) -> ProgrammeListResponse:
    programmes = await ProgramRepository(db).list_all_programs()
    return ProgrammeListResponse(
        data=[
            ProgrammeSummary(
                program_id=programme.program_id,
                code=programme.code,
                title=programme.title,
                level=programme.level,
                school=programme.school,
                awarding_body=programme.awarding_body,
                duration=programme.duration,
            )
            for programme in programmes
        ]
    )


def _to_course_item(course, program_title: str, program_code: str) -> CourseItem:
    return CourseItem(
        course_id=course.course_id,
        program_id=course.program_id,
        program_title=program_title,
        program_code=program_code,
        code=course.code,
        title=course.title,
        description=course.description,
        takeaways=course.takeaways,
        cover_image_url=course.cover_image_url,
        status=course.status,
        created_at=course.created_at,
        updated_at=course.updated_at,
    )


async def list_courses(
    db: AsyncSession,
    page: int,
    size: int,
    search: str | None,
    program_id: int | None,
    status: str | None,
) -> CourseListResponse:
    rows, total = await CourseRepository(db).list_courses(page, size, search, program_id, status)
    return CourseListResponse(
        data=[_to_course_item(course, program_title, program_code) for course, program_title, program_code in rows],
        pagination=Pagination(
            page=page,
            size=size,
            total_items=total,
            total_pages=(total + size - 1) // size if total else 0,
        ),
    )


async def create_course(db: AsyncSession, payload: CourseCreate, user_id: int) -> CourseItem:
    programme = await db.get(Program, payload.program_id)
    if programme is None:
        raise NotFoundError(f"Programme {payload.program_id} not found")

    repo = CourseRepository(db)
    code = payload.code.strip().upper()
    if await repo.get_by_code(code) is not None:
        raise ConflictError(f"Course code '{code}' is already in use")

    course = await repo.create(
        {
            **payload.model_dump(),
            "code": code,
            "title": payload.title.strip(),
            "description": payload.description.strip() if payload.description else None,
            "takeaways": payload.takeaways.strip() if payload.takeaways else None,
            "created_by": user_id,
        }
    )
    return _to_course_item(course, programme.title, programme.code)


async def update_course(db: AsyncSession, course_id: int, payload: CourseUpdate) -> CourseItem:
    repo = CourseRepository(db)
    course = await repo.get(course_id)
    if course is None:
        raise NotFoundError(f"Course {course_id} not found")

    programme = await db.get(Program, payload.program_id)
    if programme is None:
        raise NotFoundError(f"Programme {payload.program_id} not found")

    code = payload.code.strip().upper()
    if await repo.get_by_code(code, exclude_course_id=course_id) is not None:
        raise ConflictError(f"Course code '{code}' is already in use")

    course = await repo.update(
        course,
        {
            **payload.model_dump(),
            "code": code,
            "title": payload.title.strip(),
            "description": payload.description.strip() if payload.description else None,
            "takeaways": payload.takeaways.strip() if payload.takeaways else None,
        },
    )
    return _to_course_item(course, programme.title, programme.code)


async def delete_course(db: AsyncSession, course_id: int) -> None:
    repo = CourseRepository(db)
    course = await repo.get(course_id)
    if course is None:
        raise NotFoundError(f"Course {course_id} not found")
    await repo.delete(course)


async def get_course(db: AsyncSession, course_id: int) -> CourseItem:
    row = await CourseRepository(db).get_with_program(course_id)
    if row is None:
        raise NotFoundError(f"Course {course_id} not found")
    return _to_course_item(*row)


async def list_modules(db: AsyncSession, course_id: int) -> ModuleListResponse:
    if await CourseRepository(db).get(course_id) is None:
        raise NotFoundError(f"Course {course_id} not found")
    modules = await ModuleRepository(db).list_by_course(course_id)
    return ModuleListResponse(data=[ModuleItem.model_validate(module) for module in modules])


async def create_module(
    db: AsyncSession, course_id: int, payload: ModuleCreate, user_id: int
) -> ModuleItem:
    if await CourseRepository(db).get(course_id) is None:
        raise NotFoundError(f"Course {course_id} not found")

    title = payload.title.strip()
    if not title:
        raise ValidationError("Module title cannot be empty")
    repo = ModuleRepository(db)
    module = await repo.create(
        {
            **payload.model_dump(),
            "course_id": course_id,
            "title": title,
            "description": payload.description.strip() if payload.description else None,
            "position": await repo.next_position(course_id),
            "created_by": user_id,
        }
    )
    return ModuleItem.model_validate(module)


async def update_module(db: AsyncSession, module_id: int, payload: ModuleUpdate) -> ModuleItem:
    repo = ModuleRepository(db)
    module = await repo.get(module_id)
    if module is None:
        raise NotFoundError(f"Module {module_id} not found")
    title = payload.title.strip()
    if not title:
        raise ValidationError("Module title cannot be empty")
    module = await repo.update(
        module,
        {
            **payload.model_dump(),
            "title": title,
            "description": payload.description.strip() if payload.description else None,
        },
    )
    return ModuleItem.model_validate(module)


async def delete_module(db: AsyncSession, module_id: int) -> None:
    repo = ModuleRepository(db)
    module = await repo.get(module_id)
    if module is None:
        raise NotFoundError(f"Module {module_id} not found")
    await repo.delete_and_renumber(module)


async def reorder_modules(db: AsyncSession, course_id: int, module_ids: list[int]) -> ModuleListResponse:
    if await CourseRepository(db).get(course_id) is None:
        raise NotFoundError(f"Course {course_id} not found")
    repo = ModuleRepository(db)
    modules = await repo.list_by_course(course_id)
    existing_ids = {module.module_id for module in modules}
    if len(module_ids) != len(set(module_ids)) or set(module_ids) != existing_ids:
        raise ValidationError("module_ids must contain every module in this course exactly once")
    reordered = await repo.reorder(modules, module_ids)
    return ModuleListResponse(data=[ModuleItem.model_validate(module) for module in reordered])


def _to_class_item(class_, course_code: str, course_title: str, program_title: str) -> ClassItem:
    return ClassItem(
        class_id=class_.class_id,
        course_id=class_.course_id,
        course_code=course_code,
        course_title=course_title,
        program_title=program_title,
        code=class_.code,
        name=class_.name,
        description=class_.description,
        start_date=class_.start_date,
        end_date=class_.end_date,
        delivery_mode=class_.delivery_mode,
        timezone=class_.timezone,
        capacity=class_.capacity,
        status=class_.status,
        created_at=class_.created_at,
        updated_at=class_.updated_at,
    )


async def list_classes(
    db: AsyncSession,
    page: int,
    size: int,
    search: str | None,
    course_id: int | None,
    status: str | None,
) -> ClassListResponse:
    rows, total = await ClassRepository(db).list_classes(page, size, search, course_id, status)
    return ClassListResponse(
        data=[_to_class_item(*row) for row in rows],
        pagination=Pagination(
            page=page,
            size=size,
            total_items=total,
            total_pages=(total + size - 1) // size if total else 0,
        ),
    )


async def create_class(db: AsyncSession, payload: ClassCreate, user_id: int) -> ClassItem:
    course_row = await CourseRepository(db).get_with_program(payload.course_id)
    if course_row is None:
        raise NotFoundError(f"Course {payload.course_id} not found")

    repo = ClassRepository(db)
    code = payload.code.strip().upper()
    if await repo.get_by_code(code) is not None:
        raise ConflictError(f"Class code '{code}' is already in use")
    name = payload.name.strip()
    if not name:
        raise ValidationError("Class name cannot be empty")

    class_ = await repo.create(
        {
            **payload.model_dump(),
            "code": code,
            "name": name,
            "description": payload.description.strip() if payload.description else None,
            "timezone": payload.timezone.strip(),
            "created_by": user_id,
        }
    )
    course, program_title, _program_code = course_row
    return _to_class_item(class_, course.code, course.title, program_title)


async def update_class(db: AsyncSession, class_id: int, payload: ClassUpdate) -> ClassItem:
    repo = ClassRepository(db)
    class_ = await repo.get(class_id)
    if class_ is None:
        raise NotFoundError(f"Class {class_id} not found")

    course_row = await CourseRepository(db).get_with_program(payload.course_id)
    if course_row is None:
        raise NotFoundError(f"Course {payload.course_id} not found")
    code = payload.code.strip().upper()
    if await repo.get_by_code(code, exclude_class_id=class_id) is not None:
        raise ConflictError(f"Class code '{code}' is already in use")
    name = payload.name.strip()
    if not name:
        raise ValidationError("Class name cannot be empty")

    class_ = await repo.update(
        class_,
        {
            **payload.model_dump(),
            "code": code,
            "name": name,
            "description": payload.description.strip() if payload.description else None,
            "timezone": payload.timezone.strip(),
        },
    )
    course, program_title, _program_code = course_row
    return _to_class_item(class_, course.code, course.title, program_title)


async def delete_class(db: AsyncSession, class_id: int) -> None:
    repo = ClassRepository(db)
    class_ = await repo.get(class_id)
    if class_ is None:
        raise NotFoundError(f"Class {class_id} not found")
    await repo.delete(class_)


def _student_item(
    user,
    profile,
    authenticator_status: str = "not_invited",
    invitation_expires_at=None,
) -> StudentItem:
    return StudentItem(
        user_id=user.user_id,
        full_name=user.full_name or "",
        email=user.email,
        student_number=profile.student_number,
        phone=profile.phone,
        profile_image_url=profile.profile_image_url,
        notes=profile.notes,
        is_active=user.is_active,
        created_at=user.created_at,
        authenticator_status=authenticator_status,
        authenticator_invitation_expires_at=invitation_expires_at,
    )


def _lecturer_item(
    user,
    profile,
    authenticator_status: str = "not_invited",
    invitation_expires_at=None,
) -> LecturerItem:
    return LecturerItem(
        user_id=user.user_id,
        full_name=user.full_name or "",
        email=user.email,
        staff_number=profile.staff_number,
        job_title=profile.job_title,
        phone=profile.phone,
        profile_image_url=profile.profile_image_url,
        expertise=profile.expertise,
        is_active=user.is_active,
        created_at=user.created_at,
        authenticator_status=authenticator_status,
        authenticator_invitation_expires_at=invitation_expires_at,
    )


def _clean_optional(value: str | None) -> str | None:
    cleaned = value.strip() if value else ""
    return cleaned or None


async def list_students(db: AsyncSession, search: str | None) -> StudentListResponse:
    rows = await PeopleRepository(db).list_students(search)
    statuses = await AuthenticatorRepository(db).setup_statuses(
        [user.user_id for user, _profile in rows]
    )
    return StudentListResponse(
        data=[
            _student_item(user, profile, *statuses[user.user_id])
            for user, profile in rows
        ]
    )


async def create_student(db: AsyncSession, payload: StudentCreate, created_by: int) -> StudentItem:
    repo = PeopleRepository(db)
    email = payload.email.strip().lower()
    number = payload.student_number.strip().upper()
    if await repo.get_user_by_email(email) is not None:
        raise ConflictError(f"Email '{email}' is already in use")
    if await repo.student_number_exists(number):
        raise ConflictError(f"Student number '{number}' is already in use")
    access = await repo.access_levels(["LMS", "STUDENT"])
    if {item.access_key for item in access} != {"LMS", "STUDENT"}:
        raise ValidationError("LMS and STUDENT access levels must be seeded before creating students")
    user, profile = await repo.create_student(
        {"full_name": payload.full_name.strip(), "email": email, "created_by": created_by},
        {"student_number": number, "phone": _clean_optional(payload.phone), "profile_image_url": _clean_optional(payload.profile_image_url), "notes": _clean_optional(payload.notes)},
        access,
    )
    return _student_item(user, profile)


async def update_student(db: AsyncSession, user_id: int, payload: StudentUpdate) -> StudentItem:
    repo = PeopleRepository(db)
    user = await repo.get_user(user_id)
    profile = await repo.get_student_profile(user_id)
    if user is None or profile is None:
        raise NotFoundError(f"Student {user_id} not found")
    email = payload.email.strip().lower()
    number = payload.student_number.strip().upper()
    if await repo.get_user_by_email(email, exclude_user_id=user_id) is not None:
        raise ConflictError(f"Email '{email}' is already in use")
    if await repo.student_number_exists(number, exclude_user_id=user_id):
        raise ConflictError(f"Student number '{number}' is already in use")
    user, profile = await repo.update_person(
        user,
        profile,
        {"full_name": payload.full_name.strip(), "email": email},
        {"student_number": number, "phone": _clean_optional(payload.phone), "profile_image_url": _clean_optional(payload.profile_image_url), "notes": _clean_optional(payload.notes)},
    )
    return _student_item(user, profile)


async def set_student_active(db: AsyncSession, user_id: int, is_active: bool) -> StudentItem:
    repo = PeopleRepository(db)
    user = await repo.get_user(user_id)
    profile = await repo.get_student_profile(user_id)
    if user is None or profile is None:
        raise NotFoundError(f"Student {user_id} not found")
    user = await repo.set_active(user, is_active)
    if not is_active:
        await RefreshTokenRepository(db).revoke_all_for_user(user_id)
    return _student_item(user, profile)


async def list_lecturers(db: AsyncSession, search: str | None) -> LecturerListResponse:
    rows = await PeopleRepository(db).list_lecturers(search)
    statuses = await AuthenticatorRepository(db).setup_statuses(
        [user.user_id for user, _profile in rows]
    )
    return LecturerListResponse(
        data=[
            _lecturer_item(user, profile, *statuses[user.user_id])
            for user, profile in rows
        ]
    )


async def create_lecturer(db: AsyncSession, payload: LecturerCreate, created_by: int) -> LecturerItem:
    repo = PeopleRepository(db)
    email = payload.email.strip().lower()
    number = payload.staff_number.strip().upper()
    if await repo.get_user_by_email(email) is not None:
        raise ConflictError(f"Email '{email}' is already in use")
    if await repo.staff_number_exists(number):
        raise ConflictError(f"Staff number '{number}' is already in use")
    access = await repo.access_levels(["LMS", "LECTURER"])
    if {item.access_key for item in access} != {"LMS", "LECTURER"}:
        raise ValidationError("LMS and LECTURER access levels must be seeded before creating lecturers")
    user, profile = await repo.create_lecturer(
        {"full_name": payload.full_name.strip(), "email": email, "created_by": created_by},
        {
            "staff_number": number,
            "job_title": _clean_optional(payload.job_title),
            "phone": _clean_optional(payload.phone),
            "profile_image_url": _clean_optional(payload.profile_image_url),
            "expertise": _clean_optional(payload.expertise),
        },
        access,
    )
    return _lecturer_item(user, profile)


async def update_lecturer(db: AsyncSession, user_id: int, payload: LecturerUpdate) -> LecturerItem:
    repo = PeopleRepository(db)
    user = await repo.get_user(user_id)
    profile = await repo.get_lecturer_profile(user_id)
    if user is None or profile is None:
        raise NotFoundError(f"Lecturer {user_id} not found")
    email = payload.email.strip().lower()
    number = payload.staff_number.strip().upper()
    if await repo.get_user_by_email(email, exclude_user_id=user_id) is not None:
        raise ConflictError(f"Email '{email}' is already in use")
    if await repo.staff_number_exists(number, exclude_user_id=user_id):
        raise ConflictError(f"Staff number '{number}' is already in use")
    user, profile = await repo.update_person(
        user,
        profile,
        {"full_name": payload.full_name.strip(), "email": email},
        {
            "staff_number": number,
            "job_title": _clean_optional(payload.job_title),
            "phone": _clean_optional(payload.phone),
            "profile_image_url": _clean_optional(payload.profile_image_url),
            "expertise": _clean_optional(payload.expertise),
        },
    )
    return _lecturer_item(user, profile)


async def set_lecturer_active(db: AsyncSession, user_id: int, is_active: bool) -> LecturerItem:
    repo = PeopleRepository(db)
    user = await repo.get_user(user_id)
    profile = await repo.get_lecturer_profile(user_id)
    if user is None or profile is None:
        raise NotFoundError(f"Lecturer {user_id} not found")
    user = await repo.set_active(user, is_active)
    if not is_active:
        await RefreshTokenRepository(db).revoke_all_for_user(user_id)
    return _lecturer_item(user, profile)


async def send_person_authenticator_invitation(
    db: AsyncSession, user_id: int, created_by: int
):
    user = await PeopleRepository(db).get_user(user_id)
    if user is None or not user.is_active:
        raise NotFoundError(f"Active LMS user {user_id} not found")
    access = {item.access_level.access_key for item in user.access_levels}
    if not ({"STUDENT", "LECTURER"} & access):
        raise NotFoundError(f"Student or lecturer {user_id} not found")
    return await auth_service.issue_authenticator_setup_invitation(
        db, user_id, created_by
    )
