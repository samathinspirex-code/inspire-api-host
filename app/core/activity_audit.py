"""Safe request-level audit logging for CMS and LMS actions."""

import re

from fastapi import Request

from app.core.database import AsyncSessionLocal
from app.modules.auth.security import decode_access_token
from app.modules.cms import activity_service


def _describe_change(method: str, path: str) -> tuple[str, str, bool] | None:
    """Describe the actual endpoint action, including permitted learner actions."""
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if not (path.startswith("/api/v1/cms/") or path.startswith("/api/v1/lms/")):
        return None

    # These endpoints are high-frequency learner interactions, not admin audit events.
    if any(fragment in path for fragment in (
        "/progress", "/notifications/", "/assistant/ask", "/lecture-quiz/",
        "/exam-attempts/", "/coursework/assignments/",
    )):
        return None

    lower_path = path.lower()
    # These POST requests perform actions on existing resources. Keep this
    # mapping before the generic method-based descriptions below.
    if re.fullmatch(r"/api/v1/lms/meetings/\d+/zoom/join", lower_path):
        return "Requested meeting join", "Online Meetings", False
    if re.fullmatch(r"/api/v1/lms/meetings/\d+/zoom/end-intent", lower_path):
        return "Requested meeting end", "Online Meetings", False
    if re.fullmatch(r"/api/v1/lms/meetings/\d+/zoom/end-confirmed", lower_path):
        return "Ended meeting", "Online Meetings", False
    if re.fullmatch(r"/api/v1/lms/exams/\d+/start", lower_path):
        return "Started exam", "Exams", False
    if lower_path == "/api/v1/lms/profile":
        return "Saved own profile", "Profile", False
    if lower_path == "/api/v1/lms/profile/media/uploads":
        return "Requested profile photo upload", "Profile", False
    if re.fullmatch(r"/api/v1/lms/profile/media/\d+/complete", lower_path):
        return "Updated profile photo", "Profile", False
    for prefix, label in (
        ("/api/v1/cms/media", "media"),
        ("/api/v1/lms/studio/media", "course media"),
        ("/api/v1/lms/coursework/media", "coursework media"),
        ("/api/v1/lms/coursework/materials", "coursework material"),
    ):
        if lower_path == f"{prefix}/uploads":
            return f"Requested {label} upload", "Media Library" if label == "media" else "Course Content", False
        if re.fullmatch(re.escape(prefix) + r"/\d+/complete", lower_path):
            return f"Completed {label} upload", "Media Library" if label == "media" else "Course Content", False
    if lower_path == "/api/v1/cms/crm/leads":
        return "Created lead", "CRM", False
    if re.fullmatch(r"/api/v1/cms/crm/leads/\d+/stage", lower_path):
        return "Updated lead stage", "CRM", False
    if re.fullmatch(r"/api/v1/cms/crm/leads/\d+/activities", lower_path):
        return "Added lead activity", "CRM", False
    if re.fullmatch(r"/api/v1/cms/crm/leads/\d+", lower_path):
        return "Updated lead", "CRM", False
    if lower_path == "/api/v1/cms/crm/assignment-settings":
        return "Updated lead assignment settings", "CRM", False
    if re.fullmatch(r"/api/v1/cms/crm/counsellors/\d+", lower_path):
        return "Updated counsellor", "CRM", False
    if re.fullmatch(r"/api/v1/lms/exams/\d+/questions/import", lower_path):
        return "Imported exam questions", "Exams", False
    if re.fullmatch(r"/api/v1/lms/exams/\d+/schedule", lower_path):
        return "Updated exam schedule", "Exams", False
    if re.fullmatch(r"/api/v1/lms/exams/\d+/status", lower_path):
        return "Updated exam status", "Exams", False
    if lower_path == "/api/v1/lms/coursework/assignments":
        return "Created coursework assignment", "Coursework", False
    if lower_path == "/api/v1/lms/assessment-templates":
        return "Created assessment template", "Assessments", False
    module = "LMS"
    resource = "LMS record"
    if "/api/v1/cms/" in lower_path:
        module = "CMS"
    if any(value in lower_path for value in ("/programs", "/topics", "/outcomes")):
        module, resource = "Programs", "programme"
    elif "/news-events" in lower_path:
        module, resource = "News & Events", "news or event"
    elif "/media" in lower_path:
        module, resource = "Media Library", "media asset"
    elif "/users" in lower_path:
        module, resource = "Users", "user account"
    elif any(value in lower_path for value in ("course-assistant", "lecture-question", "/assistant")):
        module, resource = "Lecture Intelligence", "course assistant content"
    elif "/meetings" in lower_path:
        module, resource = "Online Meetings", "online meeting"
    elif "/attendance" in lower_path:
        module, resource = "Attendance", "attendance record"
    elif "/announcements" in lower_path:
        module, resource = "Announcements", "announcement"
    elif "/integrations" in lower_path:
        module, resource = "Integrations", "integration setting"
    elif any(value in lower_path for value in ("/studio/", "/course-media/")):
        module, resource = "Course Content", "course content"
    elif "/courses" in lower_path:
        module, resource = "Courses", "course"
    elif "/modules" in lower_path:
        module, resource = "Courses", "course module"
    elif "/classes" in lower_path:
        module, resource = "Classes", "class"
    elif "/students" in lower_path:
        module, resource = "Students", "student"
    elif "/lecturers" in lower_path:
        module, resource = "Lecturers", "lecturer"

    if method == "DELETE":
        verb = "Deleted"
    elif "/cancel" in lower_path:
        verb = "Cancelled"
    elif method == "POST":
        verb = "Created"
    else:
        verb = "Updated"
    sensitive = method == "DELETE" or module in {"Users", "Integrations"} or "/active" in lower_path
    return f"{verb} {resource}", module, sensitive


async def record_request_change(request: Request, status_code: int) -> None:
    """Best-effort logging: audit failures must never affect the original response."""
    description = _describe_change(request.method, request.url.path)
    if status_code >= 400 or not description:
        return
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return
    try:
        claims = decode_access_token(authorization[7:])
        actor_user_id = int(claims["sub"])
        actor_email = str(claims["email"])
        action, module, is_sensitive = description
        async with AsyncSessionLocal() as db:
            await activity_service.record_successful_change(
                db,
                actor_user_id=actor_user_id,
                actor_email=actor_email,
                method=request.method,
                path=request.url.path,
                action=action,
                module=module,
                is_sensitive=is_sensitive,
            )
    except Exception:
        # The requested CMS/LMS change has already completed successfully.
        # Do not turn a logging issue into a user-facing application failure.
        return
