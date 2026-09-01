"""CMS dashboard aggregation and access checks, using only an in-memory database."""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import APIError, api_error_handler
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.models import User
from app.modules.auth.schemas import CurrentUser
from app.modules.cms.dashboard_service import get_dashboard
from app.modules.cms.models import Program
from app.modules.cms.router import router
from app.modules.lms.models import CourseEnrollment, LmsCourse, LmsLearningItem, LmsModule, StudentProfile


class LocalSession:
    def __init__(self, session):
        self.session = session

    async def execute(self, stmt):
        return self.session.execute(stmt)


def program(pid):
    return Program(program_id=pid, slug=f"p{pid}", title=f"Program {pid}", code=f"P{pid}",
                   level="L5", school="Computing", awarding_body="Test", duration="1 year",
                   price_from=0, icon="x", image_label="Test", blurb="Test")


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        for model in [User, Program, StudentProfile, LmsCourse, LmsModule, LmsLearningItem, CourseEnrollment]:
            model.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.db = LocalSession(self.session)
        self.queries = []
        event.listen(self.engine, "before_cursor_execute",
                     lambda conn, cursor, sql, params, ctx, many: self.queries.append(sql))

    async def test_empty_database_returns_real_zeros(self):
        result = await get_dashboard(self.db)
        self.assertEqual((result.total_programs, result.total_students, result.published_content, result.draft_content), (0, 0, 0, 0))
        self.assertEqual(result.recent_programs, [])
        self.assertEqual(sum(result.published_by_type.values()), 0)
        self.assertEqual(len(self.queries), 3)

    async def test_program_count_is_not_capped_by_list_page_size_and_updates(self):
        self.session.add_all(program(pid) for pid in range(1, 122))
        self.session.commit()
        self.queries.clear()
        result = await get_dashboard(self.db)
        self.assertEqual(result.total_programs, 121)
        self.assertEqual([p.program_id for p in result.recent_programs], [121, 120, 119, 118, 117])
        self.assertEqual(len(self.queries), 3)
        self.session.add(program(122))
        self.session.commit()
        self.assertEqual((await get_dashboard(self.db)).total_programs, 122)
        self.session.execute(delete(Program).where(Program.program_id == 122))
        self.session.commit()
        result = await get_dashboard(self.db)
        self.assertEqual(result.total_programs, 121)
        self.assertEqual(result.recent_programs[0].program_id, 121)

    async def test_students_count_registry_people_not_course_enrolments_or_staff(self):
        self.session.add(program(1))
        for cid in [1, 2]:
            self.session.add(LmsCourse(course_id=cid, program_id=1, code=f"C{cid}", title="Course"))
        for uid in [1, 2, 3]:
            self.session.add(User(user_id=uid, email=f"u{uid}@example.test", is_active=uid != 2))
        self.session.add_all([StudentProfile(user_id=1, student_number="S1"), StudentProfile(user_id=2, student_number="S2")])
        self.session.add_all([CourseEnrollment(course_id=1, student_user_id=1), CourseEnrollment(course_id=2, student_user_id=1)])
        self.session.commit()
        result = await get_dashboard(self.db)
        # Includes the unenrolled/inactive registered student, excludes staff.
        self.assertEqual(result.total_students, 2)
        self.assertNotIn("email", result.model_dump_json())
        self.session.add(User(user_id=4, email="new@example.test"))
        self.session.add(StudentProfile(user_id=4, student_number="S4"))
        self.session.commit()
        self.assertEqual((await get_dashboard(self.db)).total_students, 3)
        self.session.execute(delete(StudentProfile).where(StudentProfile.user_id == 4))
        self.session.commit()
        self.assertEqual((await get_dashboard(self.db)).total_students, 2)

    async def test_published_items_inside_sections_not_section_count_or_drafts(self):
        self.session.add(program(1))
        self.session.add(LmsCourse(course_id=1, program_id=1, code="C1", title="Course", status="active"))
        self.session.add_all([LmsModule(module_id=1, course_id=1, title="Section", position=1, status="active"),
                              LmsModule(module_id=2, course_id=1, title="Unreleased section", position=2, status="draft")])
        for iid, kind in enumerate(["video", "video", "pdf", "text", "link", "assignment", "quiz"], start=1):
            self.session.add(LmsLearningItem(learning_item_id=iid, module_id=1, position=iid,
                                            item_type=kind, title=kind, status="published"))
        self.session.add(LmsLearningItem(learning_item_id=8, module_id=1, position=8, item_type="video", title="Draft", status="draft"))
        self.session.add(LmsLearningItem(learning_item_id=9, module_id=2, position=1, item_type="pdf", title="Published but unreleased", status="published"))
        self.session.commit()
        self.queries.clear()
        result = await get_dashboard(self.db)
        self.assertEqual(result.published_content, 8)
        self.assertEqual(result.published_by_type, {"video": 2, "pdf": 2, "text": 1, "link": 1, "assignment": 1, "quiz": 1})
        self.assertEqual(result.draft_content, 1)
        self.assertEqual(len(self.queries), 3)
        self.session.execute(update(LmsLearningItem).where(LmsLearningItem.learning_item_id == 8).values(status="published"))
        self.session.commit()
        result = await get_dashboard(self.db)
        self.assertEqual((result.published_content, result.draft_content), (9, 0))
        self.session.execute(delete(LmsLearningItem).where(LmsLearningItem.learning_item_id == 8))
        self.session.commit()
        self.assertEqual((await get_dashboard(self.db)).published_content, 8)


class DashboardAccessTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.add_exception_handler(APIError, api_error_handler)
        self.app.include_router(router)
        self.app.dependency_overrides[get_db] = lambda: None
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_requires_authentication(self):
        response = self.client.get("/api/v1/cms/dashboard")
        self.assertEqual(response.status_code, 401)

    def test_rejects_lms_only_user(self):
        self.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=1, email="test@example.test", access=["LMS"])
        with patch("app.modules.cms.dashboard_service.get_dashboard", new_callable=AsyncMock) as service:
            response = self.client.get("/api/v1/cms/dashboard")
        self.assertEqual(response.status_code, 403)
        service.assert_not_awaited()

    def test_cms_user_can_read_aggregate_without_lms_permission_and_no_caching(self):
        self.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id=1, email="test@example.test", access=["CMS"])
        payload = dict(total_programs=12, total_students=27, published_content=3, draft_content=1,
                       published_by_type={"video": 2, "pdf": 1}, recent_programs=[], generated_at="2026-08-31T00:00:00Z")
        with patch("app.modules.cms.dashboard_service.get_dashboard", new_callable=AsyncMock, return_value=payload):
            response = self.client.get("/api/v1/cms/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_students"], 27)
        self.assertEqual(response.headers["cache-control"], "no-store")
