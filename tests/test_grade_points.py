import unittest
from unittest.mock import AsyncMock, MagicMock

from app.modules.lms import grade_points_service as points


class PlanChanges(unittest.TestCase):
    def test_new_distinction_earns_points_once(self):
        rows = points.plan_changes({10: "Essay 1"}, [], 7)
        self.assertEqual(rows, [{"assignment_id": 10, "points": 100, "reason": "Distinction: Essay 1", "event_key": "grade:10:7:0"}])
        self.assertEqual(points.plan_changes({10: "Essay 1"}, [("grade:10:7:0", 100)], 7), [])

    def test_changed_or_withdrawn_result_takes_points_back(self):
        rows = points.plan_changes({}, [("grade:10:7:0", 100)], 7)
        self.assertEqual([(row["points"], row["event_key"]) for row in rows], [(-100, "grade:10:7:1")])

    def test_distinction_again_after_withdrawal_earns_again(self):
        rows = points.plan_changes({10: "Essay 1"}, [("grade:10:7:0", 100), ("grade:10:7:1", -100)], 7)
        self.assertEqual([(row["points"], row["event_key"]) for row in rows], [(100, "grade:10:7:2")])

    def test_other_assessments_and_keys_are_left_alone(self):
        rows = points.plan_changes({11: "Quiz"}, [("grade:10:7:0", 100), ("sim:3:round:0:7", 30)], 7)
        self.assertEqual([row["assignment_id"] for row in rows], [11, 10])
        self.assertEqual([row["points"] for row in rows], [100, -100])


class Sync(unittest.IsolatedAsyncioTestCase):
    async def test_returns_net_points_and_writes_only_changes(self):
        db = MagicMock()
        existing = MagicMock()
        existing.all.return_value = [("grade:10:7:0", 100)]
        db.execute = AsyncMock(side_effect=[existing, MagicMock()])
        total = await points.sync_distinction_points(db, 7, {10: "Essay 1", 11: "Quiz"})
        self.assertEqual(total, 200)
        self.assertEqual(db.execute.await_count, 2)


if __name__ == "__main__":
    unittest.main()
