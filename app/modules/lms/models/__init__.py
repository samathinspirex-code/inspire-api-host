from app.modules.lms.models.class_ import LmsClass
from app.modules.lms.models.course import LmsCourse
from app.modules.lms.models.module import LmsModule
from app.modules.lms.models.meeting import OnlineMeeting
from app.modules.lms.models.attendance import AttendanceRecord, AttendanceSession
from app.modules.lms.models.people import LecturerProfile, StudentProfile
from app.modules.lms.models.integration import (
    GoogleAccountConnection,
    GoogleIntegrationSettings,
    GoogleOAuthState,
)
from app.modules.lms.models.assignment import (
    ClassLecturer,
    ClassStudent,
    CourseEnrollment,
    CourseLecturer,
)
from app.modules.lms.models.content import (
    LmsCourseAssistantSettings,
    LmsCourseAssistantSystemSettings,
    LmsCourseDiscussion,
    LmsCourseKnowledgeSource,
    LmsCourseKnowledgeChunk,
    LmsLectureQuestion,
    LmsLectureQuizAttempt,
    LmsLectureQuizAttemptQuestion,
    LmsLearningItem,
    LmsModuleAccess,
)
from app.modules.lms.models.progress import LmsLearningProgress
from app.modules.lms.models.coursework import LmsCourseworkAssignment, LmsCourseworkSubmission
from app.modules.lms.models.exam import LmsExam, LmsExamAnswer, LmsExamAttempt, LmsExamQuestion
from app.modules.lms.models.notification import LmsAnnouncement, LmsNotification

__all__ = [
    "ClassLecturer",
    "AttendanceRecord",
    "AttendanceSession",
    "ClassStudent",
    "CourseEnrollment",
    "CourseLecturer",
    "LecturerProfile",
    "GoogleAccountConnection",
    "GoogleIntegrationSettings",
    "GoogleOAuthState",
    "LmsClass",
    "LmsCourse",
    "LmsModule",
    "OnlineMeeting",
    "StudentProfile",
    "LmsLearningItem",
    "LmsModuleAccess",
    "LmsCourseDiscussion",
    "LmsCourseAssistantSettings",
    "LmsCourseAssistantSystemSettings",
    "LmsCourseKnowledgeSource",
    "LmsCourseKnowledgeChunk",
    "LmsLectureQuestion",
    "LmsLectureQuizAttempt",
    "LmsLectureQuizAttemptQuestion",
    "LmsLearningProgress",
    "LmsCourseworkAssignment",
    "LmsCourseworkSubmission",
    "LmsExam",
    "LmsExamQuestion",
    "LmsExamAttempt",
    "LmsExamAnswer",
    "LmsAnnouncement",
    "LmsNotification",
]
