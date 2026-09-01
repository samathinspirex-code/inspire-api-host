from datetime import date
from typing import Literal

from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import APIError, ForbiddenError
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import AuthenticatorInvitationResponse, CurrentUser
from app.modules.lms import service
from app.modules.lms import student_import_service
from app.modules.lms.student_excel_import import MAX_EXCEL_BYTES, parse_excel_students
from app.modules.lms.schemas.student_import import StudentImportRequest, StudentImportResponse
from app.modules.lms import assignment_service
from app.modules.lms import portal_service
from app.modules.lms import integration_service
from app.modules.lms import meeting_service
from app.modules.lms import attendance_service
from app.modules.lms import content_service
from app.modules.lms import progress_service
from app.modules.lms import assistant_service
from app.modules.lms import coursework_service
from app.modules.lms import gradebook_service
from app.modules.lms import exam_service
from app.modules.lms import notification_service
from app.modules.lms import profile_service
from app.modules.lms import analytics_service
from app.modules.lms import dashboard_service
from app.modules.lms.schemas.dashboard import AdminDashboardResponse
from app.modules.cms import media_service
from app.modules.cms.schemas import MediaAssetResponse, MediaUploadRequest, MediaUploadTicket
from app.modules.lms.dependencies import require_lms_roles
from app.modules.lms.models import LmsModule
from app.modules.lms.schemas.progress import CourseProgressSummaryResponse
from app.modules.lms.schemas import (
    CourseCreate,
    CourseItem,
    CourseListResponse,
    CoursePresentationUpdate,
    CourseUpdate,
    ClassCreate,
    ClassItem,
    ClassListResponse,
    ClassUpdate,
    ActiveUpdate,
    AssignPersonRequest,
    AssignmentListResponse,
    AssignmentPersonItem,
    LecturerCreate,
    LecturerItem,
    LecturerListResponse,
    LecturerUpdate,
    LmsBootstrapResponse,
    ModuleCreate,
    ModuleItem,
    ModuleListResponse,
    ModuleReorderRequest,
    ModuleUpdate,
    ProgrammeListResponse,
    PortalClassDetailResponse,
    PortalClassListResponse,
    PortalCourseDetailResponse,
    PortalCourseListResponse,
    StudentCreate,
    StudentItem,
    StudentListResponse,
    StudentUpdate,
    GoogleIntegrationItem,
    GoogleIntegrationUpdate,
    GoogleConnectResponse,
    GoogleConnectionItem,
    MeetingCreate,
    MeetingItem,
    MeetingListResponse,
    MeetingUpdate,
    AttendanceRecordItem,
    AttendanceRecordUpdate,
    AttendanceReportOptionsResponse,
    AttendanceReportResponse,
    AttendanceSessionItem,
    StudentAttendanceResponse,
    CourseStudioResponse,
    CourseDiscussionCreate,
    CourseDiscussionItem,
    CourseDiscussionListResponse,
    LearningItemCreate,
    LearningItemReorderRequest,
    LearningItemResponse,
    LearningItemUpdate,
    ModuleAccessResponse,
    ModuleAccessUpdate,
    LearningProgressResponse,
    LearningProgressUpdate,
    StudentCourseProgressResponse,
    CourseAssistantAnswer,
    CourseAssistantPublicResponse,
    CourseAssistantQuestion,
    CourseAssistantSettingsResponse,
    CourseAssistantSettingsUpdate,
    LectureQuizAttemptResponse,
    LectureQuizAnswerRequest,
    LectureQuizAnswerResult,
    LectureQuizResultResponse,
    LectureQuizSubmitRequest,
    CourseworkAssignmentCreate,
    CourseworkAssignmentItem,
    CourseworkAssignmentListResponse,
    CourseworkDraftUpdate,
    CourseworkSubmissionListResponse,
    CourseworkMarkUpdate,
    GradeReleaseUpdate,
    LecturerGradebookResponse,
    StudentGradesResponse,
    ExamAnswersUpdate,
    ExamAttemptMarkUpdate,
    ExamAttemptResponse,
    ExamAttemptReviewListResponse,
    ExamCreate,
    ExamEditorResponse,
    ExamGradeReleaseUpdate,
    ExamListResponse,
    ExamQuestionUpsert,
    ExamResultResponse,
    ExamStatusUpdate,
    AnnouncementCreate,
    AnnouncementItem,
    AnnouncementListResponse,
    AnnouncementStatusUpdate,
    NotificationDispatchSummary,
    NotificationListResponse,
    NotificationReadUpdate,
    MyProfileResponse,
    MyProfileUpdate,
    RecoveryCodesRegenerateRequest,
    RecoveryCodesResponse,
    AnalyticsDashboardResponse,
)

router = APIRouter(prefix="/api/v1/lms", tags=["lms"])


@router.get("/bootstrap", response_model=LmsBootstrapResponse)
async def get_bootstrap(current_user: CurrentUser = Depends(get_current_user)) -> LmsBootstrapResponse:
    try:
        return service.build_bootstrap(current_user)
    except ValueError as exc:
        raise ForbiddenError(str(exc)) from exc


admin_access = require_lms_roles("SUPER_ADMIN", "ADMIN")
portal_access = require_lms_roles("LECTURER", "STUDENT")
meeting_view_access = require_lms_roles("SUPER_ADMIN", "ADMIN", "LECTURER", "STUDENT")
super_admin_access = require_lms_roles("SUPER_ADMIN")
lecturer_access = require_lms_roles("LECTURER")
student_access = require_lms_roles("STUDENT")
attendance_manage_access = require_lms_roles("SUPER_ADMIN", "ADMIN", "LECTURER")
media_upload_access = require_lms_roles("SUPER_ADMIN", "ADMIN", "LECTURER")
coursework_access = require_lms_roles("LECTURER", "STUDENT")
exam_access = require_lms_roles("LECTURER", "STUDENT")
notification_access = require_lms_roles("SUPER_ADMIN", "ADMIN", "LECTURER", "STUDENT")
announcement_manage_access = require_lms_roles("SUPER_ADMIN", "ADMIN", "LECTURER")
profile_access = require_lms_roles("LECTURER", "STUDENT")
analytics_access = require_lms_roles("SUPER_ADMIN", "ADMIN", "LECTURER", "STUDENT")


@router.get("/admin/dashboard", response_model=AdminDashboardResponse)
async def get_admin_dashboard(
    response: Response,
    current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> AdminDashboardResponse:
    response.headers["Cache-Control"] = "no-store"
    return await dashboard_service.get_admin_dashboard(db)


def _google_ui_redirect(status: str, message: str = "") -> str:
    params = {"view": "meetings", "google": status}
    if message:
        params["message"] = message
    return f"{settings.LMS_UI_URL.rstrip('/')}?{urlencode(params)}"


@router.get("/profile", response_model=MyProfileResponse)
async def get_my_profile(
    current_user: CurrentUser = Depends(profile_access),
    db: AsyncSession = Depends(get_db),
) -> MyProfileResponse:
    return await profile_service.get_my_profile(
        db, current_user.user_id, service.resolve_role(current_user.access)
    )


@router.get("/analytics/dashboard", response_model=AnalyticsDashboardResponse)
async def get_analytics_dashboard(
    response: Response,
    current_user: CurrentUser = Depends(analytics_access),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsDashboardResponse:
    response.headers["Cache-Control"] = "no-store"
    return await analytics_service.get_dashboard(
        db, current_user.user_id, service.resolve_role(current_user.access)
    )


@router.patch("/profile", response_model=MyProfileResponse)
async def update_my_profile(
    payload: MyProfileUpdate,
    current_user: CurrentUser = Depends(profile_access),
    db: AsyncSession = Depends(get_db),
) -> MyProfileResponse:
    return await profile_service.update_my_profile(
        db, current_user.user_id, service.resolve_role(current_user.access), payload
    )


@router.post("/profile/media/uploads", response_model=MediaUploadTicket)
async def request_profile_photo_upload(
    payload: MediaUploadRequest,
    current_user: CurrentUser = Depends(profile_access),
    db: AsyncSession = Depends(get_db),
) -> MediaUploadTicket:
    return await profile_service.request_profile_upload(db, payload, current_user.user_id)


@router.post("/profile/media/{asset_id}/complete", response_model=MyProfileResponse)
async def complete_profile_photo_upload(
    asset_id: int,
    current_user: CurrentUser = Depends(profile_access),
    db: AsyncSession = Depends(get_db),
) -> MyProfileResponse:
    return await profile_service.complete_profile_upload(
        db, asset_id, current_user.user_id, service.resolve_role(current_user.access)
    )


@router.post("/profile/recovery-codes", response_model=RecoveryCodesResponse)
async def regenerate_profile_recovery_codes(
    payload: RecoveryCodesRegenerateRequest,
    current_user: CurrentUser = Depends(profile_access),
    db: AsyncSession = Depends(get_db),
) -> RecoveryCodesResponse:
    return await profile_service.regenerate_recovery_codes(
        db, current_user.user_id, payload.authenticator_code
    )


@router.get("/coursework/assignments", response_model=CourseworkAssignmentListResponse)
async def list_coursework_assignments(
    current_user: CurrentUser = Depends(coursework_access),
    db: AsyncSession = Depends(get_db),
) -> CourseworkAssignmentListResponse:
    return await coursework_service.list_assignments(
        db, current_user.user_id, service.resolve_role(current_user.access)
    )


@router.post("/coursework/assignments", response_model=CourseworkAssignmentItem)
async def create_coursework_assignment(
    payload: CourseworkAssignmentCreate,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> CourseworkAssignmentItem:
    return await coursework_service.create_assignment(db, payload, current_user.user_id)


@router.post("/coursework/assignments/{assignment_id}/start", response_model=CourseworkAssignmentItem)
async def start_coursework_assignment(
    assignment_id: int,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> CourseworkAssignmentItem:
    return await coursework_service.start_assignment(db, assignment_id, current_user.user_id)


@router.patch("/coursework/assignments/{assignment_id}/draft", response_model=CourseworkAssignmentItem)
async def save_coursework_assignment_draft(
    assignment_id: int,
    payload: CourseworkDraftUpdate,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> CourseworkAssignmentItem:
    return await coursework_service.save_draft(db, assignment_id, payload, current_user.user_id)


@router.post("/coursework/assignments/{assignment_id}/submit", response_model=CourseworkAssignmentItem)
async def submit_coursework_assignment(
    assignment_id: int,
    payload: CourseworkDraftUpdate,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> CourseworkAssignmentItem:
    return await coursework_service.submit_assignment(db, assignment_id, payload, current_user.user_id)


@router.get(
    "/coursework/assignments/{assignment_id}/submissions",
    response_model=CourseworkSubmissionListResponse,
)
async def list_coursework_submissions(
    assignment_id: int,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> CourseworkSubmissionListResponse:
    return await coursework_service.list_submissions(db, assignment_id, current_user.user_id)


@router.patch(
    "/coursework/submissions/{submission_id}/mark",
    response_model=CourseworkSubmissionListResponse,
)
async def mark_coursework_submission(
    submission_id: int,
    payload: CourseworkMarkUpdate,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> CourseworkSubmissionListResponse:
    return await coursework_service.mark_submission(db, submission_id, payload, current_user.user_id)


@router.post("/coursework/media/uploads", response_model=MediaUploadTicket)
async def request_coursework_upload(
    payload: MediaUploadRequest,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> MediaUploadTicket:
    assignment_payload = payload.model_copy(update={"folder": "assignment-submissions"})
    return await media_service.request_upload(db, assignment_payload, current_user.user_id)


@router.post("/coursework/media/{asset_id}/complete", response_model=MediaAssetResponse)
async def complete_coursework_upload(
    asset_id: int,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> MediaAssetResponse:
    return await coursework_service.complete_student_upload(db, asset_id, current_user.user_id)


@router.patch(
    "/coursework/assignments/{assignment_id}/grade-release",
    response_model=CourseworkAssignmentItem,
)
async def update_coursework_grade_release(
    assignment_id: int,
    payload: GradeReleaseUpdate,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> CourseworkAssignmentItem:
    return await gradebook_service.set_grade_release(
        db, assignment_id, payload.grades_released, current_user.user_id
    )


@router.get("/gradebook", response_model=LecturerGradebookResponse)
async def get_lecturer_gradebook(
    course_id: int = Query(..., gt=0),
    class_id: int | None = Query(None, gt=0),
    assignment_id: int | None = Query(None, gt=0),
    search: str | None = Query(None, max_length=255),
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> LecturerGradebookResponse:
    return await gradebook_service.lecturer_gradebook(
        db, current_user.user_id, course_id, class_id, assignment_id, search
    )


@router.get("/gradebook/export")
async def export_lecturer_gradebook(
    course_id: int = Query(..., gt=0),
    class_id: int | None = Query(None, gt=0),
    assignment_id: int | None = Query(None, gt=0),
    search: str | None = Query(None, max_length=255),
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> Response:
    report = await gradebook_service.lecturer_gradebook(
        db, current_user.user_id, course_id, class_id, assignment_id, search
    )
    content = gradebook_service.build_gradebook_csv(report)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="gradebook-{report.course_code}-{date.today().isoformat()}.csv"'},
    )


@router.get("/my/grades", response_model=StudentGradesResponse)
async def get_student_grades(
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> StudentGradesResponse:
    return await gradebook_service.student_grades(db, current_user.user_id)


@router.get("/exams", response_model=ExamListResponse)
async def list_exams(
    current_user: CurrentUser = Depends(exam_access),
    db: AsyncSession = Depends(get_db),
) -> ExamListResponse:
    return await exam_service.list_exams(db, current_user.user_id, service.resolve_role(current_user.access) or "")


@router.post("/exams", response_model=ExamEditorResponse)
async def create_exam(
    payload: ExamCreate,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> ExamEditorResponse:
    return await exam_service.create_exam(db, payload, current_user.user_id)


@router.get("/exams/{exam_id}/editor", response_model=ExamEditorResponse)
async def get_exam_editor(
    exam_id: int,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> ExamEditorResponse:
    return await exam_service.get_editor(db, exam_id, current_user.user_id)


@router.post("/exams/{exam_id}/questions", response_model=ExamEditorResponse)
async def add_exam_question(
    exam_id: int,
    payload: ExamQuestionUpsert,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> ExamEditorResponse:
    return await exam_service.add_question(db, exam_id, payload, current_user.user_id)


@router.put("/exam-questions/{question_id}", response_model=ExamEditorResponse)
async def update_exam_question(
    question_id: int,
    payload: ExamQuestionUpsert,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> ExamEditorResponse:
    return await exam_service.update_question(db, question_id, payload, current_user.user_id)


@router.delete("/exam-questions/{question_id}", response_model=ExamEditorResponse)
async def delete_exam_question(
    question_id: int,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> ExamEditorResponse:
    return await exam_service.delete_question(db, question_id, current_user.user_id)


@router.patch("/exams/{exam_id}/status", response_model=ExamEditorResponse)
async def update_exam_status(
    exam_id: int,
    payload: ExamStatusUpdate,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> ExamEditorResponse:
    return await exam_service.update_status(db, exam_id, payload.status, current_user.user_id)


@router.patch("/exams/{exam_id}/grade-release", response_model=ExamEditorResponse)
async def update_exam_grade_release(
    exam_id: int,
    payload: ExamGradeReleaseUpdate,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> ExamEditorResponse:
    return await exam_service.update_grade_release(db, exam_id, payload.grades_released, current_user.user_id)


@router.post("/exams/{exam_id}/start", response_model=ExamAttemptResponse)
async def start_exam(
    exam_id: int,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> ExamAttemptResponse:
    return await exam_service.start_exam(db, exam_id, current_user.user_id)


@router.patch("/exam-attempts/{attempt_id}/answers", response_model=ExamAttemptResponse)
async def save_exam_answers(
    attempt_id: int,
    payload: ExamAnswersUpdate,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> ExamAttemptResponse:
    return await exam_service.save_answers(db, attempt_id, payload, current_user.user_id)


@router.post("/exam-attempts/{attempt_id}/submit", response_model=ExamAttemptResponse)
async def submit_exam(
    attempt_id: int,
    payload: ExamAnswersUpdate,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> ExamAttemptResponse:
    return await exam_service.submit_exam(db, attempt_id, payload, current_user.user_id)


@router.get("/exams/{exam_id}/attempts", response_model=ExamAttemptReviewListResponse)
async def list_exam_attempts(
    exam_id: int,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> ExamAttemptReviewListResponse:
    return await exam_service.list_attempts(db, exam_id, current_user.user_id)


@router.patch("/exam-attempts/{attempt_id}/mark", response_model=ExamAttemptReviewListResponse)
async def mark_exam_attempt(
    attempt_id: int,
    payload: ExamAttemptMarkUpdate,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> ExamAttemptReviewListResponse:
    return await exam_service.mark_attempt(db, attempt_id, payload, current_user.user_id)


@router.get("/exams/{exam_id}/result", response_model=ExamResultResponse)
async def get_exam_result(
    exam_id: int,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> ExamResultResponse:
    return await exam_service.get_result(db, exam_id, current_user.user_id)


@router.get("/notifications", response_model=NotificationListResponse)
async def list_notifications(
    current_user: CurrentUser = Depends(notification_access),
    db: AsyncSession = Depends(get_db),
) -> NotificationListResponse:
    return await notification_service.list_notifications(db, current_user.user_id)


@router.patch("/notifications/{notification_id}/read", response_model=NotificationListResponse)
async def update_notification_read(
    notification_id: int,
    payload: NotificationReadUpdate,
    current_user: CurrentUser = Depends(notification_access),
    db: AsyncSession = Depends(get_db),
) -> NotificationListResponse:
    return await notification_service.set_notification_read(db, notification_id, payload.read, current_user.user_id)


@router.post("/notifications/read-all", response_model=NotificationListResponse)
async def mark_all_notifications_read(
    current_user: CurrentUser = Depends(notification_access),
    db: AsyncSession = Depends(get_db),
) -> NotificationListResponse:
    return await notification_service.mark_all_read(db, current_user.user_id)


@router.get("/announcements", response_model=AnnouncementListResponse)
async def list_announcements(
    current_user: CurrentUser = Depends(announcement_manage_access),
    db: AsyncSession = Depends(get_db),
) -> AnnouncementListResponse:
    return await notification_service.list_announcements(db, current_user.user_id, service.resolve_role(current_user.access) or "")


@router.post("/announcements", response_model=AnnouncementItem)
async def create_announcement(
    payload: AnnouncementCreate,
    current_user: CurrentUser = Depends(announcement_manage_access),
    db: AsyncSession = Depends(get_db),
) -> AnnouncementItem:
    return await notification_service.create_announcement(db, payload, current_user.user_id, service.resolve_role(current_user.access) or "")


@router.patch("/announcements/{announcement_id}/status", response_model=AnnouncementItem)
async def update_announcement_status(
    announcement_id: int,
    payload: AnnouncementStatusUpdate,
    current_user: CurrentUser = Depends(announcement_manage_access),
    db: AsyncSession = Depends(get_db),
) -> AnnouncementItem:
    return await notification_service.update_announcement_status(db, announcement_id, payload.status, current_user.user_id, service.resolve_role(current_user.access) or "")


@router.post("/notifications/dispatch", response_model=NotificationDispatchSummary)
async def dispatch_notifications_now(
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> NotificationDispatchSummary:
    return await notification_service.dispatch_cycle(db)


@router.get("/integrations/google", response_model=GoogleIntegrationItem)
async def get_google_integration(
    _current_user: CurrentUser = Depends(super_admin_access), db: AsyncSession = Depends(get_db)
) -> GoogleIntegrationItem:
    return await integration_service.get_google_integration(db)


@router.put("/integrations/google", response_model=GoogleIntegrationItem)
async def update_google_integration(
    payload: GoogleIntegrationUpdate,
    current_user: CurrentUser = Depends(super_admin_access),
    db: AsyncSession = Depends(get_db),
) -> GoogleIntegrationItem:
    return await integration_service.update_google_integration(db, payload, current_user.user_id)


@router.get("/integrations/google/connection", response_model=GoogleConnectionItem)
async def get_google_connection(
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> GoogleConnectionItem:
    return await integration_service.get_google_connection(db, current_user.user_id)


@router.post("/integrations/google/connect", response_model=GoogleConnectResponse)
async def connect_google_account(
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> GoogleConnectResponse:
    return await integration_service.begin_google_connection(
        db, current_user.user_id, current_user.email
    )


@router.delete("/integrations/google/connection", status_code=204)
async def disconnect_google_account(
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> None:
    await integration_service.disconnect_google_account(db, current_user.user_id)


@router.get("/integrations/google/callback", include_in_schema=False)
async def google_oauth_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    if not state:
        return RedirectResponse(_google_ui_redirect("error", "The Google connection state is missing."))
    try:
        if error:
            await integration_service.cancel_google_connection(db, state)
            return RedirectResponse(_google_ui_redirect("cancelled", "Google account connection was cancelled."))
        if not code:
            return RedirectResponse(_google_ui_redirect("error", "Google did not return an authorization code."))
        google_email = await integration_service.complete_google_connection(db, code, state)
        return RedirectResponse(
            _google_ui_redirect("connected", f"Connected {google_email} successfully.")
        )
    except APIError as exc:
        return RedirectResponse(_google_ui_redirect("error", exc.message))


@router.get("/my/courses", response_model=PortalCourseListResponse)
async def list_my_courses(
    current_user: CurrentUser = Depends(portal_access), db: AsyncSession = Depends(get_db)
) -> PortalCourseListResponse:
    role = service.resolve_role(current_user.access)
    return await portal_service.list_my_courses(db, current_user.user_id, role)


@router.get("/my/courses/{course_id}", response_model=PortalCourseDetailResponse)
async def get_my_course(
    course_id: int,
    current_user: CurrentUser = Depends(portal_access),
    db: AsyncSession = Depends(get_db),
) -> PortalCourseDetailResponse:
    role = service.resolve_role(current_user.access)
    return await portal_service.get_my_course(db, course_id, current_user.user_id, role)


@router.patch("/my/courses/{course_id}/presentation", response_model=PortalCourseDetailResponse)
async def update_my_course_presentation(
    course_id: int,
    payload: CoursePresentationUpdate,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> PortalCourseDetailResponse:
    return await portal_service.update_my_course_presentation(
        db, course_id, payload, current_user.user_id
    )


@router.get("/my/courses/{course_id}/studio", response_model=CourseStudioResponse)
async def get_my_course_studio(
    course_id: int,
    current_user: CurrentUser = Depends(portal_access),
    db: AsyncSession = Depends(get_db),
) -> CourseStudioResponse:
    role = service.resolve_role(current_user.access)
    return await content_service.get_course_studio(db, course_id, current_user.user_id, role)


@router.post("/studio/media/uploads", response_model=MediaUploadTicket, status_code=201)
async def request_course_media_upload(
    payload: MediaUploadRequest,
    current_user: CurrentUser = Depends(media_upload_access),
    db: AsyncSession = Depends(get_db),
) -> MediaUploadTicket:
    return await media_service.request_upload(db, payload, current_user.user_id)


@router.post("/studio/media/{asset_id}/complete", response_model=MediaAssetResponse)
async def complete_course_media_upload(
    asset_id: int,
    current_user: CurrentUser = Depends(media_upload_access),
    db: AsyncSession = Depends(get_db),
) -> MediaAssetResponse:
    return await media_service.complete_upload(db, asset_id)


@router.get("/my/courses/{course_id}/assistant", response_model=CourseAssistantPublicResponse)
async def get_my_course_assistant(
    course_id: int,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(portal_access),
    db: AsyncSession = Depends(get_db),
) -> CourseAssistantPublicResponse:
    role = service.resolve_role(current_user.access)
    response = await assistant_service.get_public_settings(db, course_id, current_user.user_id, role)
    background_tasks.add_task(
        assistant_service.automate_course_intelligence,
        course_id,
        current_user.user_id,
        ingest=False,
    )
    return response


@router.post("/my/courses/{course_id}/assistant/ask", response_model=CourseAssistantAnswer)
async def ask_my_course_assistant(
    course_id: int,
    payload: CourseAssistantQuestion,
    current_user: CurrentUser = Depends(portal_access),
    db: AsyncSession = Depends(get_db),
) -> CourseAssistantAnswer:
    role = service.resolve_role(current_user.access)
    return await assistant_service.answer_question(
        db, course_id, payload.question.strip(), current_user.user_id, role
    )


@router.get(
    "/studio/courses/{course_id}/assistant-settings",
    response_model=CourseAssistantSettingsResponse,
)
async def get_studio_assistant_settings(
    course_id: int,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> CourseAssistantSettingsResponse:
    return await assistant_service.get_manager_settings(db, course_id, current_user.user_id)


@router.put(
    "/studio/courses/{course_id}/assistant-settings",
    response_model=CourseAssistantSettingsResponse,
)
async def update_studio_assistant_settings(
    course_id: int,
    payload: CourseAssistantSettingsUpdate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> CourseAssistantSettingsResponse:
    response = await assistant_service.update_manager_settings(
        db, course_id, payload, current_user.user_id
    )
    if response.is_enabled:
        background_tasks.add_task(
            assistant_service.automate_course_intelligence,
            course_id,
            current_user.user_id,
        )
    return response


@router.get("/my/learning-items/{item_id}/lecture-quiz", response_model=LectureQuizAttemptResponse)
async def get_my_lecture_quiz(
    item_id: int,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> LectureQuizAttemptResponse:
    return await assistant_service.get_or_create_quiz_attempt(db, item_id, current_user.user_id)


@router.post("/my/learning-items/{item_id}/lecture-quiz/answer", response_model=LectureQuizAnswerResult)
async def answer_my_lecture_quiz(
    item_id: int,
    payload: LectureQuizAnswerRequest,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> LectureQuizAnswerResult:
    return await assistant_service.answer_quiz_question(db, item_id, payload, current_user.user_id)


@router.post("/my/learning-items/{item_id}/lecture-quiz/submit", response_model=LectureQuizResultResponse)
async def submit_my_lecture_quiz(
    item_id: int,
    payload: LectureQuizSubmitRequest,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> LectureQuizResultResponse:
    return await assistant_service.submit_quiz_attempt(db, item_id, payload, current_user.user_id)


@router.post(
    "/my/learning-items/{item_id}/progress",
    response_model=LearningProgressResponse,
)
async def record_my_learning_progress(
    item_id: int,
    payload: LearningProgressUpdate,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> LearningProgressResponse:
    return await progress_service.record_progress(
        db, item_id, payload, current_user.user_id
    )


@router.get(
    "/my/courses/{course_id}/progress",
    response_model=StudentCourseProgressResponse,
)
async def get_my_course_progress(
    course_id: int,
    current_user: CurrentUser = Depends(student_access),
    db: AsyncSession = Depends(get_db),
) -> StudentCourseProgressResponse:
    return await progress_service.get_course_progress(
        db,
        course_id,
        current_user.user_id,
        current_user.user_id,
        "STUDENT",
    )


@router.get(
    "/studio/courses/{course_id}/students/{student_user_id}/progress",
    response_model=StudentCourseProgressResponse,
)
async def get_student_course_progress(
    course_id: int,
    student_user_id: int,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> StudentCourseProgressResponse:
    return await progress_service.get_course_progress(
        db,
        course_id,
        student_user_id,
        current_user.user_id,
        "LECTURER",
    )


@router.get("/studio/courses/{course_id}/progress-summary", response_model=CourseProgressSummaryResponse)
async def get_course_progress_summary(
    course_id: int,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> CourseProgressSummaryResponse:
    return await progress_service.get_course_progress_summary(db, course_id, current_user.user_id)


@router.get("/my/courses/{course_id}/discussions", response_model=CourseDiscussionListResponse)
async def list_my_course_discussions(
    course_id: int,
    current_user: CurrentUser = Depends(portal_access),
    db: AsyncSession = Depends(get_db),
) -> CourseDiscussionListResponse:
    role = service.resolve_role(current_user.access)
    return await content_service.list_course_discussions(db, course_id, current_user.user_id, role)


@router.post(
    "/my/courses/{course_id}/discussions",
    response_model=CourseDiscussionItem,
    status_code=201,
)
async def create_my_course_discussion(
    course_id: int,
    payload: CourseDiscussionCreate,
    current_user: CurrentUser = Depends(portal_access),
    db: AsyncSession = Depends(get_db),
) -> CourseDiscussionItem:
    role = service.resolve_role(current_user.access)
    return await content_service.create_course_discussion(
        db, course_id, payload, current_user.user_id, role
    )


@router.post("/studio/courses/{course_id}/sections", response_model=ModuleItem, status_code=201)
async def create_studio_section(
    course_id: int,
    payload: ModuleCreate,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> ModuleItem:
    await content_service.ensure_course_manager(db, course_id, current_user.user_id)
    return await service.create_module(db, course_id, payload, current_user.user_id)


@router.put("/studio/sections/{module_id}", response_model=ModuleItem)
async def update_studio_section(
    module_id: int,
    payload: ModuleUpdate,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> ModuleItem:
    await content_service.ensure_module_manager(db, module_id, current_user.user_id)
    return await service.update_module(db, module_id, payload)


@router.delete("/studio/sections/{module_id}", status_code=204)
async def delete_studio_section(
    module_id: int,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> None:
    await content_service.ensure_module_manager(db, module_id, current_user.user_id)
    await service.delete_module(db, module_id)


@router.put("/studio/courses/{course_id}/sections/reorder", response_model=ModuleListResponse)
async def reorder_studio_sections(
    course_id: int,
    payload: ModuleReorderRequest,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> ModuleListResponse:
    await content_service.ensure_course_manager(db, course_id, current_user.user_id)
    return await service.reorder_modules(db, course_id, payload.module_ids)


@router.post("/studio/sections/{module_id}/items", response_model=LearningItemResponse, status_code=201)
async def create_studio_item(
    module_id: int,
    payload: LearningItemCreate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> LearningItemResponse:
    item = await content_service.create_learning_item(db, module_id, payload, current_user.user_id)
    background_tasks.add_task(
        assistant_service.automate_learning_item_intelligence,
        item.learning_item_id,
        current_user.user_id,
    )
    return item


@router.put("/studio/items/{item_id}", response_model=LearningItemResponse)
async def update_studio_item(
    item_id: int,
    payload: LearningItemUpdate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> LearningItemResponse:
    item = await content_service.update_learning_item(db, item_id, payload, current_user.user_id)
    background_tasks.add_task(
        assistant_service.automate_learning_item_intelligence,
        item.learning_item_id,
        current_user.user_id,
    )
    return item


@router.delete("/studio/items/{item_id}", status_code=204)
async def delete_studio_item(
    item_id: int,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> None:
    await content_service.delete_learning_item(db, item_id, current_user.user_id)


@router.put("/studio/sections/{module_id}/items/reorder", response_model=list[LearningItemResponse])
async def reorder_studio_items(
    module_id: int,
    payload: LearningItemReorderRequest,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> list[LearningItemResponse]:
    return await content_service.reorder_learning_items(db, module_id, payload, current_user.user_id)


@router.put("/studio/sections/{module_id}/access", response_model=ModuleAccessResponse)
async def update_studio_access(
    module_id: int,
    payload: ModuleAccessUpdate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> ModuleAccessResponse:
    response = await content_service.update_module_access(db, module_id, payload, current_user.user_id)
    if payload.is_unlocked:
        module = await db.get(LmsModule, module_id)
        if module is not None and await assistant_service.activate_course_assistant(
            db, module.course_id, current_user.user_id
        ):
            background_tasks.add_task(
                assistant_service.automate_course_intelligence,
                module.course_id,
                current_user.user_id,
            )
    return response


@router.get("/my/classes", response_model=PortalClassListResponse)
async def list_my_classes(
    current_user: CurrentUser = Depends(portal_access), db: AsyncSession = Depends(get_db)
) -> PortalClassListResponse:
    role = service.resolve_role(current_user.access)
    return await portal_service.list_my_classes(db, current_user.user_id, role)


@router.get("/my/classes/{class_id}", response_model=PortalClassDetailResponse)
async def get_my_class(
    class_id: int,
    current_user: CurrentUser = Depends(portal_access),
    db: AsyncSession = Depends(get_db),
) -> PortalClassDetailResponse:
    role = service.resolve_role(current_user.access)
    return await portal_service.get_my_class(db, class_id, current_user.user_id, role)


@router.get("/my/meetings", response_model=MeetingListResponse)
async def list_my_meetings(
    current_user: CurrentUser = Depends(meeting_view_access), db: AsyncSession = Depends(get_db)
) -> MeetingListResponse:
    role = service.resolve_role(current_user.access)
    return await meeting_service.list_my_meetings(db, current_user.user_id, role)


@router.post("/meetings", response_model=MeetingItem, status_code=201)
async def create_online_meeting(
    payload: MeetingCreate,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> MeetingItem:
    return await meeting_service.create_meeting(db, payload, current_user.user_id)


@router.put("/meetings/{meeting_id}", response_model=MeetingItem)
async def update_online_meeting(
    meeting_id: int,
    payload: MeetingUpdate,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> MeetingItem:
    return await meeting_service.update_meeting(db, meeting_id, payload, current_user.user_id)


@router.post("/meetings/{meeting_id}/cancel", response_model=MeetingItem)
async def cancel_online_meeting(
    meeting_id: int,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> MeetingItem:
    return await meeting_service.cancel_meeting(db, meeting_id, current_user.user_id)


@router.post(
    "/meetings/{meeting_id}/attendance/sync",
    response_model=AttendanceSessionItem,
)
async def sync_online_meeting_attendance(
    meeting_id: int,
    current_user: CurrentUser = Depends(lecturer_access),
    db: AsyncSession = Depends(get_db),
) -> AttendanceSessionItem:
    return await attendance_service.sync_meeting_attendance(
        db, meeting_id, current_user.user_id
    )


@router.get(
    "/meetings/{meeting_id}/attendance",
    response_model=AttendanceSessionItem,
)
async def get_online_meeting_attendance(
    meeting_id: int,
    current_user: CurrentUser = Depends(attendance_manage_access),
    db: AsyncSession = Depends(get_db),
) -> AttendanceSessionItem:
    role = service.resolve_role(current_user.access)
    return await attendance_service.get_meeting_attendance(
        db, meeting_id, current_user.user_id, role
    )


@router.patch(
    "/attendance/records/{attendance_record_id}",
    response_model=AttendanceRecordItem,
)
async def override_attendance_record(
    attendance_record_id: int,
    payload: AttendanceRecordUpdate,
    current_user: CurrentUser = Depends(attendance_manage_access),
    db: AsyncSession = Depends(get_db),
) -> AttendanceRecordItem:
    role = service.resolve_role(current_user.access)
    return await attendance_service.override_attendance_record(
        db, attendance_record_id, payload, current_user.user_id, role
    )


@router.get("/my/attendance", response_model=StudentAttendanceResponse)
async def list_student_attendance(
    current_user: CurrentUser = Depends(require_lms_roles("STUDENT")),
    db: AsyncSession = Depends(get_db),
) -> StudentAttendanceResponse:
    return await attendance_service.list_my_attendance(db, current_user.user_id)


@router.get("/attendance/report/options", response_model=AttendanceReportOptionsResponse)
async def get_attendance_report_options(
    current_user: CurrentUser = Depends(attendance_manage_access),
    db: AsyncSession = Depends(get_db),
) -> AttendanceReportOptionsResponse:
    role = service.resolve_role(current_user.access)
    return await attendance_service.get_attendance_report_options(
        db, current_user.user_id, role
    )


@router.get("/attendance/report", response_model=AttendanceReportResponse)
async def get_attendance_report(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    program_id: int | None = Query(None, gt=0),
    course_id: int | None = Query(None, gt=0),
    class_id: int | None = Query(None, gt=0),
    student_user_id: int | None = Query(None, gt=0),
    lecturer_user_id: int | None = Query(None, gt=0),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    status: Literal["present", "absent"] | None = Query(None),
    search: str | None = Query(None, max_length=255),
    current_user: CurrentUser = Depends(attendance_manage_access),
    db: AsyncSession = Depends(get_db),
) -> AttendanceReportResponse:
    role = service.resolve_role(current_user.access)
    return await attendance_service.list_attendance_report(
        db,
        current_user.user_id,
        role,
        page,
        size,
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


@router.get("/attendance/report/export")
async def export_attendance_report(
    program_id: int | None = Query(None, gt=0),
    course_id: int | None = Query(None, gt=0),
    class_id: int | None = Query(None, gt=0),
    student_user_id: int | None = Query(None, gt=0),
    lecturer_user_id: int | None = Query(None, gt=0),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    status: Literal["present", "absent"] | None = Query(None),
    search: str | None = Query(None, max_length=255),
    current_user: CurrentUser = Depends(attendance_manage_access),
    db: AsyncSession = Depends(get_db),
) -> Response:
    role = service.resolve_role(current_user.access)
    content = await attendance_service.export_attendance_report_csv(
        db,
        current_user.user_id,
        role,
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
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="attendance-report-{date.today().isoformat()}.csv"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/programmes", response_model=ProgrammeListResponse)
async def list_programmes(
    _current_user: CurrentUser = Depends(admin_access), db: AsyncSession = Depends(get_db)
) -> ProgrammeListResponse:
    return await service.list_programmes(db)


@router.get("/courses", response_model=CourseListResponse)
async def list_courses(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    program_id: int | None = Query(None, gt=0),
    status: Literal["draft", "active", "archived"] | None = Query(None),
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> CourseListResponse:
    return await service.list_courses(db, page, size, search, program_id, status)


@router.post("/courses", response_model=CourseItem, status_code=201)
async def create_course(
    payload: CourseCreate,
    current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> CourseItem:
    return await service.create_course(db, payload, current_user.user_id)


@router.get("/courses/{course_id}", response_model=CourseItem)
async def get_course(
    course_id: int,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> CourseItem:
    return await service.get_course(db, course_id)


@router.put("/courses/{course_id}", response_model=CourseItem)
async def update_course(
    course_id: int,
    payload: CourseUpdate,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> CourseItem:
    return await service.update_course(db, course_id, payload)


@router.delete("/courses/{course_id}", status_code=204)
async def delete_course(
    course_id: int,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> None:
    await service.delete_course(db, course_id)


@router.get("/courses/{course_id}/modules", response_model=ModuleListResponse)
async def list_modules(
    course_id: int,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> ModuleListResponse:
    return await service.list_modules(db, course_id)


@router.post("/courses/{course_id}/modules", response_model=ModuleItem, status_code=201)
async def create_module(
    course_id: int,
    payload: ModuleCreate,
    current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> ModuleItem:
    return await service.create_module(db, course_id, payload, current_user.user_id)


@router.put("/modules/{module_id}", response_model=ModuleItem)
async def update_module(
    module_id: int,
    payload: ModuleUpdate,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> ModuleItem:
    return await service.update_module(db, module_id, payload)


@router.delete("/modules/{module_id}", status_code=204)
async def delete_module(
    module_id: int,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> None:
    await service.delete_module(db, module_id)


@router.put("/courses/{course_id}/modules/reorder", response_model=ModuleListResponse)
async def reorder_modules(
    course_id: int,
    payload: ModuleReorderRequest,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> ModuleListResponse:
    return await service.reorder_modules(db, course_id, payload.module_ids)


@router.get("/classes", response_model=ClassListResponse)
async def list_classes(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    course_id: int | None = Query(None, gt=0),
    status: Literal["planned", "active", "completed", "cancelled"] | None = Query(None),
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> ClassListResponse:
    return await service.list_classes(db, page, size, search, course_id, status)


@router.post("/classes", response_model=ClassItem, status_code=201)
async def create_class(
    payload: ClassCreate,
    current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> ClassItem:
    return await service.create_class(db, payload, current_user.user_id)


@router.put("/classes/{class_id}", response_model=ClassItem)
async def update_class(
    class_id: int,
    payload: ClassUpdate,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> ClassItem:
    return await service.update_class(db, class_id, payload)


@router.delete("/classes/{class_id}", status_code=204)
async def delete_class(
    class_id: int,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> None:
    await service.delete_class(db, class_id)


@router.get("/students", response_model=StudentListResponse)
async def list_students(
    search: str | None = Query(None),
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> StudentListResponse:
    return await service.list_students(db, search)


@router.post("/students", response_model=StudentItem, status_code=201)
async def create_student(
    payload: StudentCreate,
    current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> StudentItem:
    return await service.create_student(db, payload, current_user.user_id)


@router.post("/students/import", response_model=StudentImportResponse)
async def import_students(
    payload: StudentImportRequest,
    response: Response,
    current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> StudentImportResponse:
    response.headers["Cache-Control"] = "no-store"
    return await student_import_service.import_students(db, payload, current_user.user_id)


@router.post("/students/import/excel", response_model=StudentImportResponse)
async def import_students_excel(
    request: Request,
    response: Response,
    preview: bool = Query(True),
    current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> StudentImportResponse:
    if request.headers.get("content-type", "").split(";")[0] != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        raise APIError(415, "UNSUPPORTED_FILE", "Upload an Excel .xlsx file.")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_EXCEL_BYTES:
            raise APIError(413, "FILE_TOO_LARGE", "Excel files must be at most 2 MB.")
        data.extend(chunk)
    rows, sheet_name = await run_in_threadpool(parse_excel_students, bytes(data))
    result = await student_import_service.import_student_rows(db, rows, preview, current_user.user_id)
    result.sheet_name = sheet_name
    response.headers["Cache-Control"] = "no-store"
    return result


@router.put("/students/{user_id}", response_model=StudentItem)
async def update_student(
    user_id: int,
    payload: StudentUpdate,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> StudentItem:
    return await service.update_student(db, user_id, payload)


@router.patch("/students/{user_id}/active", response_model=StudentItem)
async def set_student_active(
    user_id: int,
    payload: ActiveUpdate,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> StudentItem:
    return await service.set_student_active(db, user_id, payload.is_active)


@router.get("/lecturers", response_model=LecturerListResponse)
async def list_lecturers(
    search: str | None = Query(None),
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> LecturerListResponse:
    return await service.list_lecturers(db, search)


@router.post("/lecturers", response_model=LecturerItem, status_code=201)
async def create_lecturer(
    payload: LecturerCreate,
    current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> LecturerItem:
    return await service.create_lecturer(db, payload, current_user.user_id)


@router.put("/lecturers/{user_id}", response_model=LecturerItem)
async def update_lecturer(
    user_id: int,
    payload: LecturerUpdate,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> LecturerItem:
    return await service.update_lecturer(db, user_id, payload)


@router.patch("/lecturers/{user_id}/active", response_model=LecturerItem)
async def set_lecturer_active(
    user_id: int,
    payload: ActiveUpdate,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> LecturerItem:
    return await service.set_lecturer_active(db, user_id, payload.is_active)


@router.post(
    "/users/{user_id}/authenticator-invitation",
    response_model=AuthenticatorInvitationResponse,
)
async def send_lms_authenticator_invitation(
    user_id: int,
    current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> AuthenticatorInvitationResponse:
    return await service.send_person_authenticator_invitation(
        db, user_id, current_user.user_id
    )


@router.get("/courses/{course_id}/students", response_model=AssignmentListResponse)
async def list_course_students(
    course_id: int,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> AssignmentListResponse:
    return await assignment_service.list_course_students(db, course_id)


@router.post("/courses/{course_id}/students", response_model=AssignmentPersonItem, status_code=201)
async def enroll_course_student(
    course_id: int,
    payload: AssignPersonRequest,
    current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> AssignmentPersonItem:
    return await assignment_service.enroll_student(db, course_id, payload.user_id, current_user.user_id)


@router.delete("/courses/{course_id}/students/{user_id}", status_code=204)
async def withdraw_course_student(
    course_id: int,
    user_id: int,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> None:
    await assignment_service.withdraw_student(db, course_id, user_id)


@router.get("/courses/{course_id}/lecturers", response_model=AssignmentListResponse)
async def list_course_lecturers(
    course_id: int,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> AssignmentListResponse:
    return await assignment_service.list_course_lecturers(db, course_id)


@router.post("/courses/{course_id}/lecturers", response_model=AssignmentPersonItem, status_code=201)
async def assign_course_lecturer(
    course_id: int,
    payload: AssignPersonRequest,
    current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> AssignmentPersonItem:
    return await assignment_service.assign_course_lecturer(db, course_id, payload.user_id, current_user.user_id)


@router.delete("/courses/{course_id}/lecturers/{user_id}", status_code=204)
async def remove_course_lecturer(
    course_id: int,
    user_id: int,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> None:
    await assignment_service.remove_course_lecturer(db, course_id, user_id)


@router.get("/classes/{class_id}/students", response_model=AssignmentListResponse)
async def list_class_students(
    class_id: int,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> AssignmentListResponse:
    return await assignment_service.list_class_students(db, class_id)


@router.post("/classes/{class_id}/students", response_model=AssignmentPersonItem, status_code=201)
async def assign_class_student(
    class_id: int,
    payload: AssignPersonRequest,
    current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> AssignmentPersonItem:
    return await assignment_service.assign_class_student(db, class_id, payload.user_id, current_user.user_id)


@router.delete("/classes/{class_id}/students/{user_id}", status_code=204)
async def remove_class_student(
    class_id: int,
    user_id: int,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> None:
    await assignment_service.remove_class_student(db, class_id, user_id)


@router.get("/classes/{class_id}/lecturers", response_model=AssignmentListResponse)
async def list_class_lecturers(
    class_id: int,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> AssignmentListResponse:
    return await assignment_service.list_class_lecturers(db, class_id)


@router.post("/classes/{class_id}/lecturers", response_model=AssignmentPersonItem, status_code=201)
async def assign_class_lecturer(
    class_id: int,
    payload: AssignPersonRequest,
    current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> AssignmentPersonItem:
    return await assignment_service.assign_class_lecturer(db, class_id, payload.user_id, current_user.user_id)


@router.delete("/classes/{class_id}/lecturers/{user_id}", status_code=204)
async def remove_class_lecturer(
    class_id: int,
    user_id: int,
    _current_user: CurrentUser = Depends(admin_access),
    db: AsyncSession = Depends(get_db),
) -> None:
    await assignment_service.remove_class_lecturer(db, class_id, user_id)
