"""Offline SQL/permission regressions. SQLite fixtures never use the configured database."""
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError
from app.modules.lms import assistant_service, content_service, progress_service
from app.modules.lms.models import (
    ClassStudent, CourseEnrollment, LmsClass, LmsCourse, LmsLearningItem, LmsLearningProgress,
    LmsModule, LmsModuleAccess, LmsLectureQuestion, LmsLectureQuizAttempt, LmsLectureQuizAttemptQuestion,
    LmsCourseworkAssignment, LmsExam,
)
from app.modules.lms.repository import ContentRepository
from app.modules.lms.schemas import LectureQuizAnswerRequest, LectureQuizSubmitRequest, LearningProgressUpdate
from app.modules.lms.schemas.content import CourseProgressionSettings


class LocalSession:
    """Async-shaped adapter to execute the actual SQL against an in-memory database."""
    def __init__(self, engine):
        self.session = Session(engine, expire_on_commit=False)

    async def execute(self, stmt):
        return self.session.execute(stmt)

    async def get(self, model, key):
        return self.session.get(model, key)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()

    async def refresh(self, row):
        self.session.refresh(row)

    def add(self, row):
        self.session.add(row)


class LegacyOutlineRepository(ContentRepository):
    # Reproduce the old two-per-section read pattern for the query-count baseline.
    async def list_items_for_modules(self, ids):
        return {mid: await self.list_items(mid) for mid in ids}

    async def list_access_for_modules(self, ids):
        return {mid: await self.list_access(mid) for mid in ids}


class QuizPerformanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        for model in [LmsCourse, LmsModule, LmsLearningItem, CourseEnrollment, LmsClass,
                      ClassStudent, LmsModuleAccess, LmsLearningProgress, LmsLectureQuestion,
                      LmsLectureQuizAttempt, LmsLectureQuizAttemptQuestion,
                      LmsCourseworkAssignment, LmsExam]:
            model.__table__.create(self.engine)
        # PostgreSQL returns aware timestamps; SQLite needs this normalization in the fixture.
        def normalize_rule(row, _context):
            if row.available_from and row.available_from.tzinfo is None:
                row.available_from = row.available_from.replace(tzinfo=timezone.utc)
        event.listen(LmsModuleAccess, "load", normalize_rule)
        self.addCleanup(event.remove, LmsModuleAccess, "load", normalize_rule)
        with Session(self.engine) as db:
            db.add(LmsCourse(course_id=1, program_id=1, code="TEST", title="Offline test course"))
            db.add(CourseEnrollment(course_id=1, student_user_id=7, status="enrolled"))
            for pos in range(1, 13):
                db.add(LmsModule(module_id=pos, course_id=1, title=f"Section {pos}", position=pos, status="active"))
                db.add(LmsLearningItem(learning_item_id=100+pos, module_id=pos, title=f"Lesson {pos}",
                    position=1, item_type="video" if pos == 12 else "text", status="published"))
                db.add(LmsModuleAccess(module_id=pos, scope_type="course", scope_id=1, is_unlocked=True))
            db.add(LmsLearningProgress(learning_item_id=112, student_user_id=7, is_completed=True))
            db.commit()
        self.queries = []
        event.listen(self.engine, "before_cursor_execute", lambda conn, cursor, sql, params, ctx, many: self.queries.append(sql))

    def db(self):
        db = LocalSession(self.engine)
        self.addCleanup(db.session.close)
        return db

    def bank(self, item_id, count=4):
        with Session(self.engine) as db:
            for pos in range(count):
                db.add(LmsLectureQuestion(course_id=1, learning_item_id=item_id, question=f"Concept {pos}",
                    option_a="First", option_b="Second", option_c="Third", option_d="Fourth",
                    correct_option="B", explanation="Second is correct.", status="approved"))
            db.commit()

    def change(self, model, key, **values):
        with Session(self.engine) as db:
            row = db.get(model, key)
            for name, value in values.items():
                setattr(row, name, value)
            db.commit()

    async def assert_access(self, expected, item_id=112, student_id=7):
        db = self.db()
        if expected:
            self.assertEqual((await content_service.get_accessible_student_item(db, item_id, student_id)).learning_item_id, item_id)
        else:
            with self.assertRaises(ForbiddenError):
                await content_service.get_accessible_student_item(db, item_id, student_id)
        db.session.close()

    async def test_access_query_count_drops_from_full_outline_to_three(self):
        db = self.db()
        self.queries.clear()
        item = await db.get(LmsLearningItem, 112)
        module = await db.get(LmsModule, item.module_id)
        with patch.object(content_service, "ContentRepository", LegacyOutlineRepository):
            studio = await content_service.get_course_studio(db, module.course_id, 7, "STUDENT")
        self.assertTrue(studio.sections[-1].items[0].is_accessible)
        baseline = len(self.queries)
        db.session.close()
        self.queries.clear()
        await self.assert_access(True)
        optimized = len(self.queries)
        self.assertEqual(optimized, 3)
        self.assertGreaterEqual(baseline, 30)
        print(f"\n12-section access check: {baseline} SQL reads before; {optimized} after.")

    async def test_batched_outline_keeps_permissions_and_item_order(self):
        with patch.object(content_service, "ContentRepository", LegacyOutlineRepository):
            old_db = self.db()
            old = await content_service.get_course_studio(old_db, 1, 7, "STUDENT")
            old_db.session.close()
        self.queries.clear()
        current = await content_service.get_course_studio(self.db(), 1, 7, "STUDENT")
        self.assertEqual(old, current)
        self.assertLessEqual(len(self.queries), 9)

    async def test_published_practice_test_is_visible_even_when_container_status_is_stale(self):
        self.change(LmsLearningItem, 101, item_type="pdf")
        with Session(self.engine) as db:
            db.add(LmsModule(module_id=13, course_id=1, title="Practice Test", position=13, status="active"))
            db.add(LmsLearningItem(
                learning_item_id=113, module_id=13, title="Released practice",
                position=1, item_type="quiz", resource_url="practice-test:1", status="draft",
            ))
            db.flush()
            db.add(LmsCourseworkAssignment(
                assignment_id=1, learning_item_id=113, course_id=1, target_type="course",
                target_id=1, title="Released practice", instructions="Try it", assignment_type="timed",
                duration_minutes=30, status="published", created_by=1,
            ))
            db.flush()
            db.add(LmsExam(
                exam_id=1, assessment_kind="practice_test", assignment_id=1, course_id=1,
                target_type="course", target_id=1, title="Released practice", instructions="Try it",
                duration_minutes=30, status="published", created_by=1,
            ))
            db.commit()
        studio = await content_service.get_course_studio(self.db(), 1, 7, "STUDENT")
        practice = next(section for section in studio.sections if section.title == "Practice Test")
        self.assertEqual([item.learning_item_id for item in practice.items], [113])
        accessible = await content_service.get_accessible_student_item(self.db(), 113, 7)
        self.assertEqual(accessible.learning_item_id, 113)

    async def test_draft_withdrawn_and_foreign_students_remain_denied(self):
        await self.assert_access(False, student_id=8)
        self.change(CourseEnrollment, (1, 7), status="withdrawn")
        await self.assert_access(False)
        self.change(CourseEnrollment, (1, 7), status="enrolled")
        self.change(LmsModule, 12, status="draft")
        await self.assert_access(False)
        self.change(LmsModule, 12, status="active")
        self.change(LmsLearningItem, 112, status="draft")
        await self.assert_access(False)

    async def test_individual_release_overrides_class_and_course_rules(self):
        with Session(self.engine) as db:
            db.add(LmsClass(class_id=1, course_id=1, code="CLASS", name="Test", start_date=date.today(), end_date=date.today()))
            db.add(ClassStudent(class_id=1, student_user_id=7))
            db.add(LmsModuleAccess(module_id=12, scope_type="class", scope_id=1, is_unlocked=False))
            db.commit()
        await self.assert_access(False)
        with Session(self.engine) as db:
            rule = LmsModuleAccess(module_id=12, scope_type="student", scope_id=7, is_unlocked=True)
            db.add(rule); db.commit(); rule_id = rule.module_access_id
        await self.assert_access(True)
        self.change(LmsModuleAccess, rule_id, available_from=datetime.now(timezone.utc)+timedelta(days=1))
        await self.assert_access(False)

    async def test_video_completion_unlocks_next_content_without_submitting_quiz(self):
        self.change(LmsLearningItem, 101, item_type="video")
        await self.assert_access(False)
        first = await progress_service.record_progress(
            self.db(), 101, LearningProgressUpdate(event="heartbeat", duration_seconds=20,
                                                    position_seconds=17, watched_seconds_delta=17), 7,
        )
        self.assertEqual(first.completion_percent, 75)
        self.change(LmsLearningProgress, first.progress_id,
                    last_activity_at=datetime.now(timezone.utc) - timedelta(seconds=15))
        partial = await progress_service.record_progress(
            self.db(), 101, LearningProgressUpdate(event="heartbeat", duration_seconds=20,
                                                    position_seconds=17, watched_seconds_delta=2), 7,
        )
        self.assertEqual(partial.completion_percent, 85)
        self.assertFalse(partial.is_completed)
        await self.assert_access(False)
        self.bank(101)
        await self.assert_access(False)
        with Session(self.engine) as db:
            db.add(LmsLectureQuizAttempt(learning_item_id=101, student_user_id=7,
                                         total_questions=4, score=4,
                                         submitted_at=datetime.now(timezone.utc)))
            db.commit()
        await self.assert_access(False)
        result = await progress_service.record_progress(
            self.db(), 101, LearningProgressUpdate(event="ended", duration_seconds=20,
                                                    position_seconds=20), 7,
        )
        self.assertTrue(result.is_completed)
        await self.assert_access(True)
        studio = await content_service.get_course_studio(self.db(), 1, 7, "STUDENT")
        self.assertTrue(studio.sections[1].items[0].is_accessible)
        self.assertIsNone(studio.sections[1].items[0].item_locked_reason)

    async def test_unfinished_video_blocks_even_with_no_quiz_and_respects_release(self):
        self.change(LmsLearningItem, 101, item_type="video")
        await self.assert_access(False)
        studio = await content_service.get_course_studio(self.db(), 1, 7, "STUDENT")
        self.assertFalse(studio.sections[1].items[0].is_accessible)
        self.assertIn("Finish watching", studio.sections[1].items[0].item_locked_reason)
        self.change(LmsModule, 1, status="draft")
        await self.assert_access(True)
        self.change(LmsModule, 1, status="active")
        self.change(LmsModuleAccess, 1, is_unlocked=False)
        await self.assert_access(True)
        self.change(LmsModuleAccess, 1, is_unlocked=True)
        await self.assert_access(False)

    async def test_completed_quiz_stays_completed_until_explicit_reattempt(self):
        self.bank(112, count=6)
        db = self.db()
        self.queries.clear()
        first = await assistant_service.get_or_create_quiz_attempt(db, 112, 7)
        self.assertEqual(len(first.questions), 4)
        bank_reads = [sql for sql in self.queries if sql.lstrip().startswith("SELECT lms_lecture_questions.") and "ORDER BY" in sql]
        self.assertEqual(len(bank_reads), 1)
        resumed = await assistant_service.get_or_create_quiz_attempt(db, 112, 7)
        self.assertEqual(first.attempt_id, resumed.attempt_id)
        first_ids = {q.question_id for q in first.questions}
        for q in first.questions:
            await assistant_service.answer_quiz_question(db, 112, LectureQuizAnswerRequest(
                attempt_id=first.attempt_id, question_id=q.question_id, selected_option="B"), 7)
        result = await assistant_service.submit_quiz_attempt(db, 112, LectureQuizSubmitRequest(
            attempt_id=first.attempt_id, answers=[{"question_id": q.question_id, "selected_option": "B"} for q in first.questions]), 7)
        self.assertEqual(result.score, 4)
        second = await assistant_service.get_or_create_quiz_attempt(db, 112, 7)
        self.assertEqual(second.attempt_number, 1)
        self.assertEqual(first.attempt_id, second.attempt_id)
        self.assertEqual({q.question_id for q in second.questions}, first_ids)

    async def test_only_earlier_items_in_the_same_section_block_access(self):
        with Session(self.engine) as db:
            db.add(LmsLearningItem(learning_item_id=113, module_id=12, position=2, title="Later video",
                                  item_type="video", status="published"))
            db.commit()
        self.bank(113)
        await self.assert_access(True)
        self.change(LmsLearningItem, 113, position=0)
        await self.assert_access(False)
        self.change(LmsLearningItem, 113, status="draft")
        await self.assert_access(True)

    async def test_class_membership_from_another_course_cannot_unlock_a_section(self):
        self.change(LmsModuleAccess, 12, is_unlocked=False)
        with Session(self.engine) as db:
            db.add(LmsCourse(course_id=2, program_id=1, code="OTHER", title="Other course"))
            db.add(LmsClass(class_id=9, course_id=2, code="OTHER-CLASS", name="Other class",
                            start_date=date.today(), end_date=date.today()))
            db.add(ClassStudent(class_id=9, student_user_id=7))
            db.add(LmsModuleAccess(module_id=12, scope_type="class", scope_id=9, is_unlocked=True))
            db.commit()
        await self.assert_access(False)

    async def test_unfinished_pdfs_preserve_existing_access_with_sequence_on(self):
        self.change(LmsLearningItem, 101, item_type="pdf")
        with Session(self.engine) as db:
            db.add(LmsLearningItem(learning_item_id=113, module_id=1, position=2,
                                  title="Next PDF", item_type="pdf", status="published"))
            db.commit()
        await self.assert_access(True, 101)
        await self.assert_access(True, 113)
        await self.assert_access(True, 112)
        studio = await content_service.get_course_studio(self.db(), 1, 7, "STUDENT")
        items = {item.learning_item_id: item for section in studio.sections for item in section.items}
        self.assertTrue(studio.sequential_content_enabled)
        self.assertTrue(all(item.is_accessible for item in items.values()))
        self.assertFalse(items[101].is_completed)
        self.assertFalse(items[113].is_completed)

    async def test_pdf_completion_is_saved_for_that_student_only(self):
        self.change(LmsLearningItem, 101, item_type="pdf")
        with Session(self.engine) as db:
            db.add(CourseEnrollment(course_id=1, student_user_id=8, status="enrolled"))
            db.commit()
        result = await progress_service.record_progress(
            self.db(), 101, LearningProgressUpdate(event="complete"), 7,
        )
        self.assertTrue(result.is_completed)
        self.assertEqual(result.completion_percent, 100)
        student = await content_service.get_course_studio(self.db(), 1, 7, "STUDENT")
        other = await content_service.get_course_studio(self.db(), 1, 8, "STUDENT")
        self.assertTrue(student.sections[0].items[0].is_completed)
        self.assertFalse(other.sections[0].items[0].is_completed)
        self.assertTrue(student.sections[-1].items[0].is_accessible)
        self.assertTrue(other.sections[-1].items[0].is_accessible)

    async def test_off_opens_released_pdf_and_video_without_previous_completion(self):
        self.change(LmsLearningItem, 101, item_type="video")
        self.bank(101)
        self.change(LmsLearningItem, 102, item_type="pdf", resource_url="https://example.test/lesson.pdf")
        await self.assert_access(False, 102)
        self.change(LmsCourse, 1, sequential_content_enabled=False)
        await self.assert_access(True, 102)
        await self.assert_access(True, 112)
        studio = await content_service.get_course_studio(self.db(), 1, 7, "STUDENT")
        self.assertFalse(studio.sequential_content_enabled)
        self.assertTrue(all(item.is_accessible for section in studio.sections for item in section.items))
        self.assertEqual(studio.sections[1].items[0].resource_url, "https://example.test/lesson.pdf")
        self.assertFalse(studio.sections[1].items[0].is_completed)
        with patch.object(assistant_service, "_download_pdf", new_callable=AsyncMock, return_value=b"%PDF-test") as download:
            self.assertEqual(await content_service.read_learning_item_pdf(self.db(), 102, 7, "STUDENT"), b"%PDF-test")
            download.assert_awaited_once_with("https://example.test/lesson.pdf")

    async def test_class_and_template_settings_remain_independent(self):
        with Session(self.engine) as db:
            for cid in (2, 3):
                db.add(LmsCourse(course_id=cid, program_id=1, code=f"CLASS-{cid}",
                                 title=f"Class {cid}", is_class_copy=True,
                                 source_master_course_id=1))
            db.commit()
        # A class update affects neither its template nor another class from it.
        setting = await content_service.update_course_progression_settings(
            self.db(), 2, CourseProgressionSettings(sequential_content_enabled=False),
        )
        self.assertFalse(setting.sequential_content_enabled)
        with Session(self.engine) as db:
            self.assertTrue(db.get(LmsCourse, 1).sequential_content_enabled)
            self.assertFalse(db.get(LmsCourse, 2).sequential_content_enabled)
            self.assertTrue(db.get(LmsCourse, 3).sequential_content_enabled)
        # Editing the template later does not overwrite either existing class.
        await content_service.update_course_progression_settings(
            self.db(), 1, CourseProgressionSettings(sequential_content_enabled=False),
        )
        await content_service.update_course_progression_settings(
            self.db(), 1, CourseProgressionSettings(sequential_content_enabled=True),
        )
        with Session(self.engine) as db:
            self.assertTrue(db.get(LmsCourse, 1).sequential_content_enabled)
            self.assertFalse(db.get(LmsCourse, 2).sequential_content_enabled)
            self.assertTrue(db.get(LmsCourse, 3).sequential_content_enabled)

    async def test_off_still_enforces_release_schedules_publication_and_enrollment(self):
        self.change(LmsCourse, 1, sequential_content_enabled=False)
        self.change(LmsModuleAccess, 12, is_unlocked=False)
        await self.assert_access(False)
        studio = await content_service.get_course_studio(self.db(), 1, 7, "STUDENT")
        self.assertFalse(studio.sections[-1].items[0].is_accessible)
        self.change(LmsModuleAccess, 12, is_unlocked=True,
                    available_from=datetime.now(timezone.utc) + timedelta(days=1))
        await self.assert_access(False)
        self.change(LmsModuleAccess, 12, available_from=None)
        await self.assert_access(True)
        self.change(LmsLearningItem, 112, status="draft")
        await self.assert_access(False)
        self.change(LmsLearningItem, 112, status="published")
        self.change(LmsModule, 12, status="draft")
        await self.assert_access(False)
        self.change(LmsModule, 12, status="active")
        self.change(CourseEnrollment, (1, 7), status="withdrawn")
        await self.assert_access(False)
        await self.assert_access(False, student_id=8)

    async def test_reenabling_sequence_restores_video_rules_and_preserves_pdf_progress(self):
        self.change(LmsLearningItem, 101, item_type="video")
        self.bank(101)
        self.change(LmsLearningItem, 102, item_type="pdf")
        self.change(LmsCourse, 1, sequential_content_enabled=False)
        await progress_service.record_progress(self.db(), 102, LearningProgressUpdate(event="complete"), 7)
        await self.assert_access(True)
        self.change(LmsCourse, 1, sequential_content_enabled=True)
        await self.assert_access(False, 102)
        await self.assert_access(False)
        await progress_service.record_progress(
            self.db(), 101, LearningProgressUpdate(event="ended", duration_seconds=100,
                                                    position_seconds=100), 7,
        )
        await self.assert_access(True)
        studio = await content_service.get_course_studio(self.db(), 1, 7, "STUDENT")
        self.assertTrue(studio.sections[1].items[0].is_completed)

    async def test_locked_or_draft_pdf_does_not_block_released_content(self):
        self.change(LmsLearningItem, 101, item_type="pdf")
        self.change(LmsModuleAccess, 1, is_unlocked=False)
        await self.assert_access(True)
        self.change(LmsModuleAccess, 1, is_unlocked=True)
        self.change(LmsLearningItem, 101, status="draft")
        await self.assert_access(True)


if __name__ == "__main__":
    unittest.main()
