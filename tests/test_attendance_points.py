import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.modules.lms import attendance_service, points_ledger


def item(record_id, status):
    return SimpleNamespace(attendance_record_id=record_id, status=status, course_title="Managing Finance", meeting_title=f"Week {record_id}")


class AttendancePlan(unittest.TestCase):
    def test_each_attended_class_earns_ten_once(self):
        rows = points_ledger.plan({5: "Attended: A"}, [], 9, "att", 10, "Attendance changed to absent")
        self.assertEqual([(row["points"], row["event_key"]) for row in rows], [(10, "att:5:9:0")])
        self.assertEqual(points_ledger.plan({5: "Attended: A"}, [("att:5:9:0", 10)], 9, "att", 10, "x"), [])

    def test_override_to_absent_takes_points_back(self):
        rows = points_ledger.plan({}, [("att:5:9:0", 10)], 9, "att", 10, "Attendance changed to absent")
        self.assertEqual([(row["points"], row["event_key"], row["reason"]) for row in rows], [(-10, "att:5:9:1", "Attendance changed to absent")])

    def test_prefixes_do_not_mix(self):
        self.assertEqual(points_ledger.plan({}, [("grade:5:9:0", 100)], 9, "att", 10, "x"), [])


class AttendanceSync(unittest.IsolatedAsyncioTestCase):
    async def test_only_present_classes_count_and_result_is_committed(self):
        db = MagicMock()
        db.commit = AsyncMock()
        with patch.object(points_ledger, "sync", AsyncMock(return_value=20)) as sync:
            total = await attendance_service.sync_attendance_points(db, 9, [item(1, "present"), item(2, "absent"), item(3, "present")])
        self.assertEqual(total, 20)
        self.assertEqual(sorted(sync.await_args.kwargs["current"]), [1, 3])
        self.assertEqual(sync.await_args.kwargs["amount"], 10)
        db.commit.assert_awaited_once()

    async def test_points_failure_never_breaks_the_page(self):
        db = MagicMock()
        db.rollback = AsyncMock()
        with patch.object(points_ledger, "sync", AsyncMock(side_effect=RuntimeError("down"))):
            self.assertEqual(await attendance_service.sync_attendance_points(db, 9, [item(1, "present")]), 0)
        db.rollback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
