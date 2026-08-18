from pydantic import BaseModel

from app.modules.lms.schemas.assignment import AssignmentPersonItem
from app.modules.lms.schemas.class_ import ClassItem
from app.modules.lms.schemas.course import CourseItem
from app.modules.lms.schemas.module import ModuleItem


class PortalCourseItem(CourseItem):
    module_count: int
    class_count: int
    people_count: int
    people_label: str


class PortalCourseListResponse(BaseModel):
    data: list[PortalCourseItem]


class PortalCourseDetailResponse(BaseModel):
    course: PortalCourseItem
    modules: list[ModuleItem]
    people: list[AssignmentPersonItem]
    people_label: str


class PortalClassItem(ClassItem):
    people_count: int
    people_label: str


class PortalClassListResponse(BaseModel):
    data: list[PortalClassItem]


class PortalClassDetailResponse(BaseModel):
    class_: PortalClassItem
    people: list[AssignmentPersonItem]
    people_label: str
