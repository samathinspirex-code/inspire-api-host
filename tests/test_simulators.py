import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError as SchemaError
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, ForbiddenError, ValidationError
from app.modules.lms import simulator_engine as engine, simulator_service as service
from app.modules.lms.schemas.simulator import SimContent, SimRunCreate, SimTemplateWrite, SimVote, SimXpAdjustment

NOW = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
STARTERS = json.loads((Path(__file__).resolve().parents[1] / "app/modules/lms/data/simulator_starters.json").read_text(encoding="utf-8"))
CONTENT = {
    "kpis": [
        {"key": "cash", "label": "Cash", "start": 100, "direction": 1, "scale": 40, "prefix": "", "suffix": "", "decimals": 0},
        {"key": "waste", "label": "Waste", "start": 12, "direction": -1, "scale": 6, "prefix": "", "suffix": "%", "decimals": 0},
    ],
    "rounds": [
        {"title": "One", "brief": "First", "event": None, "decisions": [{"question": "Pick", "options": [{"text": "A", "effects": {"cash": 10}}, {"text": "B", "effects": {"waste": -2}}, {"text": "C", "effects": {}}]}]},
        {"title": "Two", "brief": "Second", "event": "Twist", "decisions": [{"question": "Again", "options": [{"text": "X", "effects": {"cash": -5}}, {"text": "Y", "effects": {"waste": 3}}]}]},
    ],
}


def ballot(student, decision, option, seconds):
    return engine.Ballot(student, decision, option, NOW + timedelta(seconds=seconds))


def make_run(**overrides):
    values = dict(run_id=7, class_id=3, title="Sim", tone="student", content=CONTENT, roles=[{"key": "a", "name": "Lead", "responsibility": ""}, {"key": "b", "name": "Money", "responsibility": ""}],
                  min_team_size=2, max_team_size=2, allow_solo=False, opens_at=NOW - timedelta(days=1), closes_at=NOW + timedelta(days=1), xp_per_round=100, points_per_round=30, status="open")
    return SimpleNamespace(**{**values, **overrides})


def make_team(**overrides):
    values = dict(team_id=5, run_id=7, name="Crew", is_solo=False, status="forming", current_round=0, kpi_state=engine.initial_kpi_state(CONTENT), final_score=None, final_band=None)
    return SimpleNamespace(**{**values, **overrides})


class MajorityVote(unittest.TestCase):
    def test_majority_wins(self):
        winners = engine.tally(1, [ballot(1, 0, 2, 0), ballot(2, 0, 1, 1), ballot(3, 0, 1, 2)])
        self.assertEqual((winners[0]["option_index"], winners[0]["votes"], winners[0]["tied"]), (1, 2, False))

    def test_tie_goes_to_option_that_reached_the_count_first(self):
        winners = engine.tally(1, [ballot(1, 0, 0, 0), ballot(2, 0, 1, 1), ballot(3, 0, 1, 2), ballot(4, 0, 0, 3)])
        self.assertEqual((winners[0]["option_index"], winners[0]["tied"]), (1, True))

    def test_team_of_two_tie_goes_to_first_voter(self):
        self.assertEqual(engine.tally(1, [ballot(1, 0, 2, 5), ballot(2, 0, 0, 9)])[0]["option_index"], 2)

    def test_auto_close_needs_every_member_on_every_decision(self):
        partial = [ballot(1, 0, 0, 0), ballot(1, 1, 0, 0), ballot(2, 0, 0, 0)]
        self.assertFalse(engine.should_auto_close(2, [1, 2], partial))
        self.assertTrue(engine.should_auto_close(2, [1, 2], [*partial, ballot(2, 1, 1, 0)]))

    def test_absent_member_close_needs_more_than_half(self):
        two_of_four = [ballot(1, 0, 0, 0), ballot(2, 0, 0, 0)]
        self.assertFalse(engine.can_close_manually(1, [1, 2, 3, 4], two_of_four))
        self.assertTrue(engine.can_close_manually(1, [1, 2, 3, 4], [*two_of_four, ballot(3, 0, 1, 0)]))
        self.assertTrue(engine.can_close_manually(1, [1, 2, 3], two_of_four))

    def test_undecided_vote_is_rejected(self):
        with self.assertRaises(ValueError):
            engine.tally(2, [ballot(1, 0, 0, 0)])


class Scoring(unittest.TestCase):
    def test_same_choices_give_same_result(self):
        values = engine.initial_kpi_state(CONTENT)["values"]
        self.assertEqual(engine.resolve_round(CONTENT, 1, values, [0], 5), engine.resolve_round(CONTENT, 1, values, [0], 5))

    def test_winning_effect_is_applied_with_small_noise(self):
        values = engine.initial_kpi_state(CONTENT)["values"]
        after, delta = engine.resolve_round(CONTENT, 0, values, [0], 5)
        self.assertAlmostEqual(after["cash"], 110, delta=40 * 0.06 + 0.001)
        self.assertEqual(set(delta), {"cash", "waste"})

    def test_percent_is_clamped(self):
        low = {"cash": 100.0, "waste": 0.0}
        self.assertGreaterEqual(engine.resolve_round(CONTENT, 0, low, [1], 1)[0]["waste"], 0.0)

    def test_score_formula_and_bands(self):
        self.assertEqual(engine.score(CONTENT, {"cash": 100, "waste": 12}), 50)
        self.assertEqual(engine.score(CONTENT, {"cash": 140, "waste": 6}), 66)
        self.assertEqual(engine.score(CONTENT, {"cash": 9999, "waste": 0}), 100)
        self.assertEqual([engine.band(value) for value in (75, 60, 45, 44)], ["Distinction", "Merit", "Pass", "Refer"])

    def test_completion_rewards(self):
        self.assertEqual(engine.completion_rewards(80), (160, 100))
        self.assertEqual(engine.completion_rewards(44), (88, 20))

    def test_public_round_hides_effects(self):
        public = engine.public_round(CONTENT, 0, 5)
        self.assertNotIn("effects", json.dumps(public))
        self.assertEqual(sorted(option["option_index"] for option in public["decisions"][0]["options"]), [0, 1, 2])
        self.assertEqual(public, engine.public_round(CONTENT, 0, 5))


class Schemas(unittest.TestCase):
    def test_all_starters_are_valid(self):
        self.assertEqual(len(STARTERS), 14)
        for item in STARTERS:
            SimTemplateWrite(**{key: item[key] for key in ("title", "tagline", "programme", "level", "tone", "content", "roles")})

    def test_option_cannot_change_unknown_kpi(self):
        broken = json.loads(json.dumps(CONTENT))
        broken["rounds"][0]["decisions"][0]["options"][0]["effects"] = {"nope": 1}
        with self.assertRaises(SchemaError):
            SimContent(**broken)

    def test_max_team_cannot_exceed_roles(self):
        with self.assertRaises(SchemaError):
            SimRunCreate(template_id=1, roles=[{"name": "Only"}], min_team_size=1, max_team_size=2, opens_at=NOW, closes_at=NOW + timedelta(days=1))

    def test_adjustment_needs_one_target_and_a_value(self):
        with self.assertRaises(SchemaError):
            SimXpAdjustment(student_user_id=1, team_id=2, xp=5, reason="Great work")
        with self.assertRaises(SchemaError):
            SimXpAdjustment(student_user_id=1, reason="Great work")

    def test_generated_role_keys_avoid_existing(self):
        roles = service._normalise_roles([SimpleNamespace(key="role_1", name="A", responsibility=""), SimpleNamespace(key=None, name="B", responsibility="")])
        self.assertEqual([role["key"] for role in roles], ["role_1", "role_2"])


class Windows(unittest.TestCase):
    def test_window_states_and_incomplete_team(self):
        self.assertEqual(service._window(make_run(), NOW), "open")
        self.assertEqual(service._window(make_run(opens_at=NOW + timedelta(hours=1)), NOW), "upcoming")
        self.assertEqual(service._window(make_run(closes_at=NOW), NOW), "closed")
        self.assertEqual(service._window(make_run(status="closed"), NOW), "closed")

    def test_expired_run_rejected(self):
        with patch.object(service, "_now", return_value=NOW):
            with self.assertRaises(ValidationError):
                service._ensure_open(make_run(closes_at=NOW - timedelta(seconds=1)))
            self.assertEqual(service._team_status(make_team(status="playing"), make_run(closes_at=NOW)), "incomplete")
            self.assertEqual(service._team_status(make_team(status="completed"), make_run(closes_at=NOW)), "completed")


class Access(unittest.IsolatedAsyncioTestCase):
    async def test_student_not_in_class_rejected(self):
        db = MagicMock()
        db.get = AsyncMock(side_effect=[SimpleNamespace(status="active", course_id=9), None])
        with self.assertRaises(ForbiddenError):
            await service._ensure_class_student(db, 3, 42)

    async def test_withdrawn_student_rejected(self):
        db = MagicMock()
        db.get = AsyncMock(side_effect=[SimpleNamespace(status="active", course_id=9), SimpleNamespace(), SimpleNamespace(status="withdrawn")])
        with self.assertRaises(ForbiddenError):
            await service._ensure_class_student(db, 3, 42)

    async def test_lecturer_of_another_class_rejected(self):
        db = MagicMock()
        db.get = AsyncMock(side_effect=[SimpleNamespace(status="active"), None])
        with patch.object(service, "is_global_admin", AsyncMock(return_value=False)), self.assertRaises(ForbiddenError):
            await service._ensure_class_manager(db, 3, 42)

    async def test_other_lecturers_private_template_rejected(self):
        db = MagicMock()
        db.get = AsyncMock(return_value=SimpleNamespace(starter_key=None, created_by=1))
        with patch.object(service, "is_global_admin", AsyncMock(return_value=False)), self.assertRaises(ForbiddenError):
            await service._readable_template(db, 2, 42)

    async def test_starter_cannot_be_edited(self):
        db = MagicMock()
        db.get = AsyncMock(return_value=SimpleNamespace(starter_key="found", created_by=None))
        with patch.object(service, "is_global_admin", AsyncMock(return_value=True)), self.assertRaises(ForbiddenError):
            await service.update_template(db, 2, MagicMock(), 42)


class Invites(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MagicMock()
        self.db.flush, self.db.commit, self.db.rollback, self.db.execute, self.db.scalar, self.db.get = (AsyncMock() for _ in range(6))
        self.detail = patch.object(service, "student_run_detail", AsyncMock(return_value={"ok": True}))
        self.detail.start()
        self.addCleanup(self.detail.stop)
        self.clock = patch.object(service, "_now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def _context(self, team=None, run=None):
        return patch.object(service, "_member_context", AsyncMock(return_value=(team or make_team(), run or make_run(), SimpleNamespace())))

    def _no_current_team(self):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        self.db.execute.return_value = result

    async def test_cannot_invite_outside_class(self):
        with self._context(), patch.object(service, "_class_student_ids", AsyncMock(return_value=[1])), self.assertRaises(ValidationError):
            await service.invite_student(self.db, 5, 99, 1)

    async def test_student_already_in_team_is_named(self):
        result = MagicMock()
        result.scalar_one_or_none.return_value = 11
        self.db.execute.return_value = result
        self.db.get.return_value = SimpleNamespace(name="Rivals")
        with self._context(), patch.object(service, "_class_student_ids", AsyncMock(return_value=[1, 2])):
            with self.assertRaisesRegex(ConflictError, "Rivals"):
                await service.invite_student(self.db, 5, 2, 1)

    async def test_limit_counts_pending_invites(self):
        self._no_current_team()
        self.db.scalar.return_value = 1
        with self._context(), patch.object(service, "_class_student_ids", AsyncMock(return_value=[1, 2])), patch.object(service, "_members", AsyncMock(return_value=[SimpleNamespace()])):
            with self.assertRaises(ValidationError):
                await service.invite_student(self.db, 5, 2, 1)

    async def test_double_invite_race_becomes_conflict(self):
        self._no_current_team()
        self.db.scalar.return_value = 0
        self.db.flush.side_effect = IntegrityError("x", {}, Exception())
        with self._context(), patch.object(service, "_class_student_ids", AsyncMock(return_value=[1, 2])), patch.object(service, "_members", AsyncMock(return_value=[SimpleNamespace()])):
            with self.assertRaises(ConflictError):
                await service.invite_student(self.db, 5, 2, 1)
        self.db.rollback.assert_awaited()

    async def test_accept_race_with_other_team_becomes_conflict(self):
        invite = SimpleNamespace(invite_id=1, student_user_id=2, team_id=5, run_id=7, status="pending", responded_at=None)
        self.db.get.return_value = invite
        self.db.refresh = AsyncMock()
        self.db.flush.side_effect = IntegrityError("x", {}, Exception())
        with patch.object(service, "_lock_team", AsyncMock(return_value=make_team())), patch.object(service, "_get_run", AsyncMock(return_value=make_run())), patch.object(service, "_ensure_class_student", AsyncMock()), patch.object(service, "_members", AsyncMock(return_value=[SimpleNamespace()])):
            with self.assertRaises(ConflictError):
                await service.respond_invite(self.db, 1, True, 2)

    async def test_full_team_rejects_accept(self):
        invite = SimpleNamespace(invite_id=1, student_user_id=2, team_id=5, run_id=7, status="pending", responded_at=None)
        self.db.get.return_value = invite
        self.db.refresh = AsyncMock()
        with patch.object(service, "_lock_team", AsyncMock(return_value=make_team())), patch.object(service, "_get_run", AsyncMock(return_value=make_run())), patch.object(service, "_ensure_class_student", AsyncMock()), patch.object(service, "_members", AsyncMock(return_value=[SimpleNamespace(), SimpleNamespace()])):
            with self.assertRaisesRegex(ConflictError, "full"):
                await service.respond_invite(self.db, 1, True, 2)

    async def test_start_needs_minimum_and_roles(self):
        with self._context(), patch.object(service, "_members", AsyncMock(return_value=[SimpleNamespace(role_key="a")])):
            with self.assertRaisesRegex(ValidationError, "at least 2"):
                await service.start_team(self.db, 5, 1)
        with self._context(), patch.object(service, "_members", AsyncMock(return_value=[SimpleNamespace(role_key="a"), SimpleNamespace(role_key=None)])):
            with self.assertRaisesRegex(ValidationError, "pick a role"):
                await service.start_team(self.db, 5, 1)


class Rounds(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MagicMock()
        self.db.add = MagicMock()
        self.db.commit = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [1, 2]
        self.db.execute = AsyncMock(return_value=result)
        self.clock = patch.object(service, "_now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    async def test_round_rewards_only_voters_with_stable_keys(self):
        team = make_team(status="playing")
        with patch.object(service, "_award", AsyncMock()) as award:
            await service._close_round(self.db, team, make_run(), 1, [ballot(1, 0, 0, 0)])
        self.assertEqual(team.current_round, 1)
        self.assertEqual(award.await_count, 1)
        self.assertEqual(award.await_args.kwargs["event_key"], "sim:5:round:0:1")
        self.assertEqual((award.await_args.kwargs["xp"], award.await_args.kwargs["points"]), (100, 30))

    async def test_final_round_completes_team_and_pays_voters(self):
        team = make_team(status="playing", current_round=1)
        with patch.object(service, "_award", AsyncMock()) as award, patch.object(service, "_active_member_ids", AsyncMock(return_value=[1])):
            await service._close_round(self.db, team, make_run(), 1, [ballot(1, 0, 0, 0)])
        self.assertEqual(team.status, "completed")
        self.assertEqual(team.final_band, engine.band(team.final_score))
        self.assertEqual([call.kwargs["source_type"] for call in award.await_args_list], ["sim_round", "sim_completion"])
        self.assertEqual(award.await_args_list[1].kwargs["event_key"], "sim:5:complete:1")

    async def test_vote_after_close_date_rejected(self):
        context = (make_team(status="playing"), make_run(closes_at=NOW - timedelta(seconds=1)), SimpleNamespace())
        with patch.object(service, "_member_context", AsyncMock(return_value=context)), self.assertRaises(ValidationError):
            await service.cast_vote(self.db, 5, SimVote(decision_index=0, option_index=0), 1)

    async def test_invalid_option_rejected(self):
        context = (make_team(status="playing"), make_run(), SimpleNamespace())
        with patch.object(service, "_member_context", AsyncMock(return_value=context)), self.assertRaises(ValidationError):
            await service.cast_vote(self.db, 5, SimVote(decision_index=0, option_index=9), 1)

    async def test_close_voting_needs_more_than_half(self):
        context = (make_team(status="playing"), make_run(), SimpleNamespace())
        with patch.object(service, "_member_context", AsyncMock(return_value=context)), patch.object(service, "_active_member_ids", AsyncMock(return_value=[1, 2])), patch.object(service, "_ballots", AsyncMock(return_value=[ballot(1, 0, 0, 0)])):
            with self.assertRaises(ValidationError):
                await service.close_voting(self.db, 5, 1)

    async def test_state_never_exposes_option_effects(self):
        team = make_team(status="playing")
        members = [SimpleNamespace(student_user_id=1, role_key="a")]
        with patch.object(service, "_members", AsyncMock(return_value=members)), patch.object(service, "_names", AsyncMock(return_value={1: "Ann"})), patch.object(service, "_class_student_ids", AsyncMock(return_value=[1])), patch.object(service, "_ballots", AsyncMock(return_value=[])), patch.object(service, "_results", AsyncMock(return_value=[])), patch.object(service, "_rewards_for", AsyncMock(return_value={})), patch.object(service, "_messages", AsyncMock(return_value=[])):
            state = await service._team_state(self.db, team, make_run(), 1)
        self.assertNotIn("effects", json.dumps(state, default=str))
        self.assertEqual(state["round"]["title"], "One")


if __name__ == "__main__":
    unittest.main()
