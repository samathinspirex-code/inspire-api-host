import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.core.errors import ForbiddenError
from app.modules.lms.zoom_service import lecturer_start_url, reconcile_lecturer_meeting


class _Rows:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class _Database:
    def __init__(self):
        self.rows = iter((
            {"meeting_id": 7, "class_id": 3, "lecturer_user_id": 12,
             "provider_meeting_id": "123456789", "zoom_host_connection_id": 2},
            {"connection_id": 2, "enabled": True},
        ))

    async def execute(self, *_args, **_kwargs):
        return _Rows(next(self.rows))


class LecturerZoomStartTests(unittest.TestCase):
    def test_students_and_administrators_cannot_request_host_link(self):
        for role in ("STUDENT", "ADMIN", "SUPER_ADMIN"):
            with self.subTest(role=role), self.assertRaises(ForbiddenError):
                asyncio.run(lecturer_start_url(_Database(), 7, 12, role))

    def test_unassigned_lecturer_cannot_request_host_link(self):
        with patch("app.modules.lms.zoom_service._lecturer_can_host", new=AsyncMock(return_value=False)):
            with self.assertRaises(ForbiddenError):
                asyncio.run(lecturer_start_url(_Database(), 7, 99, "LECTURER"))

    def test_assigned_lecturer_receives_fresh_zoom_host_link(self):
        class _Response:
            is_error = False

            def json(self):
                return {"start_url": "https://zoom.us/s/123456789?zak=private"}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url, **_kwargs):
                self.requested_url = url
                return _Response()

        with patch("app.modules.lms.zoom_service._lecturer_can_host", new=AsyncMock(return_value=True)), \
             patch("app.modules.lms.zoom_service._access_token", new=AsyncMock(return_value="token")), \
             patch("app.modules.lms.zoom_service.httpx.AsyncClient", return_value=_Client()):
            result = asyncio.run(lecturer_start_url(_Database(), 7, 12, "LECTURER"))
        self.assertEqual(result["start_url"], "https://zoom.us/s/123456789?zak=private")


class _ReconcileDatabase:
    def __init__(self):
        self.queries = []

    async def execute(self, query, parameters):
        self.queries.append((str(query), parameters))
        return _Rows({"meeting_id": 7, "class_id": 3,
                      "lecturer_user_id": 12, "status": "scheduled"})

    async def scalar(self, *_args, **_kwargs):
        return "completed"


class LecturerReconcileTests(unittest.TestCase):
    def test_only_assigned_lecturer_can_reconcile(self):
        with self.assertRaises(ForbiddenError):
            asyncio.run(reconcile_lecturer_meeting(_ReconcileDatabase(), 7, 12, "STUDENT"))
        with patch("app.modules.lms.zoom_service._lecturer_can_host", new=AsyncMock(return_value=False)):
            with self.assertRaises(ForbiddenError):
                asyncio.run(reconcile_lecturer_meeting(_ReconcileDatabase(), 7, 99, "LECTURER"))

    def test_reconcile_checks_the_selected_meeting(self):
        database = _ReconcileDatabase()
        with patch("app.modules.lms.zoom_service._lecturer_can_host", new=AsyncMock(return_value=True)), \
             patch("app.modules.lms.zoom_service.sweep_finished_meetings", new=AsyncMock(return_value=1)) as sweep:
            result = asyncio.run(reconcile_lecturer_meeting(database, 7, 12, "LECTURER"))
        self.assertEqual(result, {"meeting_id": 7, "status": "completed"})
        self.assertEqual(sweep.await_args.kwargs["meeting_id"], 7)
        self.assertEqual(sweep.await_args.kwargs["grace_minutes"], 0)
