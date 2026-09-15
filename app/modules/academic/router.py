from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.academic import service
from app.modules.academic.schemas import (
    AcademicLevelCreate, AcademicResponse, AdmissionApplicationCreate, AdmissionApplicationResponse, ClassDetailsUpdate, ClassFromCourseRequest, ClassStatusUpdate, CourseCreate,
    NamedNodeCreate, PathwayConfirmRequest, ProgrammeEnrolmentCreate,
    ProgrammeEnrolmentResponse, ProgrammeLevelUpdate, StudyOptionUpsert,
)
from app.modules.auth.dependencies import get_current_user, require_access
from app.modules.auth.schemas import CurrentUser
from app.core.errors import ForbiddenError


public_router = APIRouter(prefix="/api/v1/public", tags=["public-academic"])
cms_router = APIRouter(prefix="/api/v1/cms/academic", tags=["cms-academic"], dependencies=[Depends(require_access("CMS"))])
lms_router = APIRouter(prefix="/api/v1/lms/academic", tags=["lms-academic"], dependencies=[Depends(require_access("LMS"))])


def require_academic_staff(user: CurrentUser = Depends(get_current_user)):
    if not any(role in user.access for role in ("SUPER_ADMIN", "ADMIN", "LECTURER")):
        raise ForbiddenError("Academic workspace changes require a staff role")
    return user


async def ensure_course_access(db: AsyncSession, course_id: int, user: CurrentUser):
    if not await service.staff_can_manage_course(db, course_id, user):
        raise ForbiddenError("You are not assigned to manage this course")


async def ensure_class_access(db: AsyncSession, class_id: int, user: CurrentUser):
    if not await service.user_can_access_class(db, class_id, user):
        raise ForbiddenError("You are not assigned to this class")


@public_router.get("/catalogue/programmes", response_model=AcademicResponse)
async def public_programmes(school_id: int | None = Query(None, gt=0), db: AsyncSession = Depends(get_db)):
    return {"data": await service.public_programmes(db, school_id)}


@public_router.get("/catalogue/levels", response_model=AcademicResponse)
async def public_levels(programme_id: int = Query(..., gt=0), school_id: int | None = Query(None, gt=0), db: AsyncSession = Depends(get_db)):
    return {"data": await service.public_levels(db, programme_id, school_id)}


@public_router.get("/catalogue/schools", response_model=AcademicResponse)
async def public_schools(db: AsyncSession = Depends(get_db)):
    return {"data": await service.public_schools(db)}


@public_router.get("/catalogue/courses", response_model=AcademicResponse)
async def public_courses(programme_id: int | None = Query(None, gt=0), school_id: int | None = Query(None, gt=0), db: AsyncSession = Depends(get_db)):
    return {"data": await service.list_courses(db, programme_id, school_id, True)}


@public_router.post("/programme-enrolments", response_model=ProgrammeEnrolmentResponse, status_code=201)
async def programme_enrolment(payload: ProgrammeEnrolmentCreate, db: AsyncSession = Depends(get_db)):
    return await service.create_programme_enrolment(db, payload)


@public_router.post("/admission-applications", response_model=AdmissionApplicationResponse, status_code=201)
async def admission_application(payload: AdmissionApplicationCreate, db: AsyncSession = Depends(get_db)):
    return await service.create_admission_lead(db, payload)


@cms_router.get("/{resource}", response_model=AcademicResponse)
async def list_resource(resource: str, db: AsyncSession = Depends(get_db)):
    if resource not in service.RESOURCE_TABLES:
        from app.core.errors import NotFoundError
        raise NotFoundError("Academic resource not found")
    return {"data": await service.list_nodes(db, resource)}


@cms_router.post("/programmes", response_model=AcademicResponse, status_code=201)
async def create_programme(payload: NamedNodeCreate, db: AsyncSession = Depends(get_db)):
    return {"data": await service.create_node(db, "programmes", payload)}


@cms_router.put("/programmes/{node_id}", response_model=AcademicResponse)
async def update_programme(node_id: int, payload: NamedNodeCreate, db: AsyncSession = Depends(get_db)):
    return {"data": await service.update_node(db, "programmes", node_id, payload)}


@cms_router.delete("/programmes/{node_id}", response_model=AcademicResponse)
async def delete_programme(node_id: int, db: AsyncSession = Depends(get_db)):
    return {"data": await service.delete_node(db, "programmes", node_id)}


@cms_router.put("/programmes/{programme_id}/levels", response_model=AcademicResponse)
async def programme_levels(programme_id: int, payload: ProgrammeLevelUpdate, db: AsyncSession = Depends(get_db)):
    return {"data": await service.set_programme_levels(db, programme_id, payload.level_ids)}


@cms_router.get("/programmes/{programme_id}/levels", response_model=AcademicResponse)
async def get_programme_levels(programme_id: int, db: AsyncSession = Depends(get_db)):
    return {"data": await service.public_levels(db, programme_id)}


@cms_router.post("/levels", response_model=AcademicResponse, status_code=201)
async def create_level(payload: AcademicLevelCreate, db: AsyncSession = Depends(get_db)):
    return {"data": await service.create_node(db, "levels", payload)}


@cms_router.put("/levels/{node_id}", response_model=AcademicResponse)
async def update_level(node_id: int, payload: AcademicLevelCreate, db: AsyncSession = Depends(get_db)):
    return {"data": await service.update_node(db, "levels", node_id, payload)}


@cms_router.delete("/levels/{node_id}", response_model=AcademicResponse)
async def delete_level(node_id: int, db: AsyncSession = Depends(get_db)):
    return {"data": await service.delete_node(db, "levels", node_id)}


@cms_router.post("/schools", response_model=AcademicResponse, status_code=201)
async def create_school(payload: NamedNodeCreate, db: AsyncSession = Depends(get_db)):
    return {"data": await service.create_node(db, "schools", payload)}


@cms_router.put("/schools/{node_id}", response_model=AcademicResponse)
async def update_school(node_id: int, payload: NamedNodeCreate, db: AsyncSession = Depends(get_db)):
    return {"data": await service.update_node(db, "schools", node_id, payload)}


@cms_router.delete("/schools/{node_id}", response_model=AcademicResponse)
async def delete_school(node_id: int, db: AsyncSession = Depends(get_db)):
    return {"data": await service.delete_node(db, "schools", node_id)}


@cms_router.get("/courses/list", response_model=AcademicResponse)
async def cms_courses(db: AsyncSession = Depends(get_db)):
    return {"data": await service.list_courses(db)}


@cms_router.post("/courses", response_model=AcademicResponse, status_code=201)
async def create_course(payload: CourseCreate, db: AsyncSession = Depends(get_db)):
    return {"data": await service.upsert_course(db, payload)}


@cms_router.put("/courses/{course_id}", response_model=AcademicResponse)
async def update_course(course_id: int, payload: CourseCreate, db: AsyncSession = Depends(get_db)):
    return {"data": await service.upsert_course(db, payload, course_id)}


@cms_router.delete("/courses/{course_id}", response_model=AcademicResponse)
async def delete_course(course_id: int, db: AsyncSession = Depends(get_db)):
    return {"data": await service.delete_course(db, course_id)}


@cms_router.put("/courses/{course_id}/study-options/{study_mode}", response_model=AcademicResponse)
async def study_option(course_id: int, study_mode: str, payload: StudyOptionUpsert, db: AsyncSession = Depends(get_db)):
    if study_mode != payload.study_mode:
        from app.core.errors import ValidationError
        raise ValidationError("Study mode path and payload must match")
    return {"data": await service.upsert_study_option(db, course_id, payload)}


@cms_router.get("/enrolments/programmes", response_model=AcademicResponse)
async def enrolments(status: str | None = None, db: AsyncSession = Depends(get_db)):
    return {"data": await service.list_programme_enrolments(db, status)}


@cms_router.get("/crm/leads", response_model=AcademicResponse)
async def crm_leads(status: str | None = None, db: AsyncSession = Depends(get_db)):
    return {"data": await service.list_admission_leads(db, status)}


@cms_router.get("/analytics/summary", response_model=AcademicResponse)
async def analytics(db: AsyncSession = Depends(get_db)):
    return {"data": await service.academic_analytics(db)}


@cms_router.post("/enrolments/programmes/{enrolment_id}/confirm", response_model=AcademicResponse)
async def confirm(enrolment_id: int, payload: PathwayConfirmRequest, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return {"data": await service.confirm_pathway(db, enrolment_id, payload, user.user_id)}


@cms_router.get("/classes/list", response_model=AcademicResponse)
async def cms_classes(db: AsyncSession = Depends(get_db)):
    return {"data": await service.list_academic_classes(db)}


@lms_router.get("/my/enrolments", response_model=AcademicResponse)
async def my_enrolments(user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if "STUDENT" not in user.access:
        raise ForbiddenError("This enrolment view is available to students")
    return {"data": await service.list_my_enrolments(db, user.user_id)}


@lms_router.get("/courses", response_model=AcademicResponse)
async def academic_courses(user: CurrentUser = Depends(require_academic_staff), db: AsyncSession = Depends(get_db)):
    courses = await service.list_courses(db)
    if "LECTURER" in user.access:
        courses = [course for course in courses if await service.staff_can_manage_course(db, course["course_id"], user)]
    return {"data": courses}


@lms_router.get("/classes", response_model=AcademicResponse)
async def academic_classes(user: CurrentUser = Depends(require_academic_staff), db: AsyncSession = Depends(get_db)):
    classes = await service.list_academic_classes(db)
    if "LECTURER" in user.access:
        classes = [item for item in classes if await service.user_can_access_class(db, item["class_id"], user)]
    return {"data": classes}


@lms_router.get("/courses/{course_id}/study-options", response_model=AcademicResponse)
async def lms_course_study_options(course_id: int, user: CurrentUser = Depends(require_academic_staff), db: AsyncSession = Depends(get_db)):
    await service.ensure_staff_can_manage_lms_course(db, course_id, user)
    return {"data": await service.list_lms_course_study_options(db, course_id)}


@lms_router.delete("/courses/{course_id}", response_model=AcademicResponse)
async def archive_course_workspace(course_id: int, user: CurrentUser = Depends(require_academic_staff), db: AsyncSession = Depends(get_db)):
    return {"data": await service.archive_master_course_workspace(db, course_id, user)}


@lms_router.delete("/classes/{class_id}", response_model=AcademicResponse)
async def archive_class_workspace(class_id: int, user: CurrentUser = Depends(require_academic_staff), db: AsyncSession = Depends(get_db)):
    return {"data": await service.archive_class_workspace(db, class_id, user)}


@lms_router.patch("/classes/{class_id}/status", response_model=AcademicResponse)
async def update_class_status(class_id: int, payload: ClassStatusUpdate, user: CurrentUser = Depends(require_academic_staff), db: AsyncSession = Depends(get_db)):
    return {"data": await service.update_class_workspace_status(db, class_id, payload.status, user)}


@lms_router.put("/classes/{class_id}", response_model=AcademicResponse)
async def update_class_details(class_id: int, payload: ClassDetailsUpdate, user: CurrentUser = Depends(require_academic_staff), db: AsyncSession = Depends(get_db)):
    return {"data": await service.update_class_workspace_details(db, class_id, payload, user)}


@lms_router.post("/classes/from-course", response_model=AcademicResponse, status_code=201)
async def class_from_course(payload: ClassFromCourseRequest, user: CurrentUser = Depends(require_academic_staff), db: AsyncSession = Depends(get_db)):
    await service.ensure_staff_can_manage_lms_course(db, payload.source_course_id, user)
    return {"data": await service.create_class_from_course(db, payload, user.user_id)}
