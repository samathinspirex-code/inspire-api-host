from app.modules.lms.repository.class_ import ClassRepository
from app.modules.lms.repository.course import CourseRepository
from app.modules.lms.repository.content import ContentRepository
from app.modules.lms.repository.module import ModuleRepository
from app.modules.lms.repository.people import PeopleRepository
from app.modules.lms.repository.portal import PortalRepository
from app.modules.lms.repository.integration import IntegrationRepository
from app.modules.lms.repository.meeting import MeetingRepository
from app.modules.lms.repository.attendance import AttendanceRepository
from app.modules.lms.repository.assignment import AssignmentRepository
from app.modules.lms.repository.progress import ProgressRepository

__all__ = [
    "AttendanceRepository",
    "AssignmentRepository",
    "ClassRepository",
    "CourseRepository",
    "ContentRepository",
    "ModuleRepository",
    "PeopleRepository",
    "PortalRepository",
    "IntegrationRepository",
    "MeetingRepository",
    "ProgressRepository",
]
