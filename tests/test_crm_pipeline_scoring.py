from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from app.modules.crm import router, service
from app.modules.crm.schemas import CrmActivityCreate
from app.modules.insights import scoring

NOW = datetime(2026, 11, 3, 12)


def _row(lead_id, stage="contacted", raw=None, touched=2, **extra):
    return {
        "lead_id": lead_id, "stage": stage, "raw_stage": raw or stage, "source": "manual", "followup_count": 0,
        "payment_plan": None, "last_contacted_at": NOW - timedelta(hours=touched), "last_activity_at": None,
        "created_at": NOW - timedelta(days=30), **extra,
    }


def test_priority_queue_lifts_new_leads_and_keeps_only_the_top_four():
    rows = [_row(1, "uncontactable"), _row(2, "new_lead", touched=2), _row(3), _row(4, "future_prospect"), _row(5, "follow_up"), _row(6, "potential")]
    top = service.rank_priority_rows(rows, NOW)
    assert len(top) == 4
    assert top[0]["lead_id"] == 3
    assert top[1]["lead_id"] == 2
    assert 1 not in [row["lead_id"] for row in top]


def test_a_lead_going_quiet_ranks_above_an_identical_one_just_touched():
    # Recency earns the idle one less, so compare leads whose recency band matches.
    quiet, fresh = _row(1, touched=30), _row(2, touched=30)
    quiet["last_contacted_at"] = NOW - timedelta(hours=100)
    fresh["last_contacted_at"] = NOW - timedelta(hours=500)
    assert service.rank_priority_rows([fresh, quiet], NOW, 1)[0]["lead_id"] == 1


def test_legacy_stage_keeps_its_own_score():
    legacy = service._scored_card(_row(1, "not_interested", raw="cant_afford"), NOW)
    plain = service._scored_card(_row(2, "not_interested"), NOW)
    assert legacy["score"] > plain["score"]


def _call(outcome="connected", kind="call"):
    return CrmActivityCreate(activity_type=kind, call_outcome=outcome if kind == "call" else None, content="" if kind == "call" else "x")


def _run_activity(payload, reached_today=False):
    async def run():
        first = MagicMock()
        first.scalar.return_value = 1 if reached_today else None
        db = SimpleNamespace(execute=AsyncMock(return_value=first), commit=AsyncMock())
        activity = SimpleNamespace(activity_id=1, lead_id=5, activity_type=payload.activity_type, content="x", call_outcome=payload.call_outcome, counsellor_name="C", counsellor_id=17, created_at=NOW)
        with (
            patch.object(service, "CrmLeadRepository", return_value=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(lead_id=5)))),
            patch.object(service, "CrmActivityRepository", return_value=SimpleNamespace(create=AsyncMock(return_value=activity))),
            patch.object(service, "_refresh_auto_priority", new_callable=AsyncMock) as refresh,
        ):
            await service.add_activity(db, 5, payload, counsellor_id=17)
        return db.execute.await_args_list, refresh

    return asyncio.run(run())


def test_each_connected_call_adds_score_until_engagement_caps():
    lead = {"stage": "contacted", "source": "manual", "last_touch": NOW, "created_at": NOW - timedelta(days=5)}
    fresh = scoring.score_lead({**lead, "followup_count": 0}, NOW)
    one = scoring.score_lead({**lead, "followup_count": 1}, NOW)
    two = scoring.score_lead({**lead, "followup_count": 2}, NOW)
    assert (fresh, one, two) == (46, 59, 64)
    assert scoring.score_lead({**lead, "followup_count": 50}, NOW) == 79


def test_unanswered_calls_lower_the_score_up_to_a_limit():
    lead = {"stage": "contacted", "source": "manual", "last_touch": NOW, "created_at": NOW - timedelta(days=5)}
    assert scoring.score_lead({**lead, "unanswered_calls": 2}, NOW) == 40
    assert scoring.score_lead({**lead, "unanswered_calls": 40}, NOW) == 34


def test_auto_priority_rules():
    open_lead = {"source": "manual", "last_touch": NOW, "created_at": NOW - timedelta(days=5)}
    pick = lambda **extra: scoring.auto_priority({**open_lead, **extra}, NOW)
    assert pick(stage="new_lead") == "medium"
    assert pick(stage="new_lead", source="referral") == "high"
    assert pick(stage="new_lead", source="website_admission") == "high"
    assert pick(stage="contacted") == "medium"
    assert pick(stage="contacted", followup_count=2) == "high"
    assert pick(stage="contacted", unanswered_calls=40, last_touch=NOW - timedelta(days=30)) == "low"
    for closed in ("uncontactable", "enrolled", "not_interested", "cant_afford", "lost_to_competitor"):
        assert pick(stage=closed, followup_count=9) == "low"


def test_priority_lifts_a_lead_in_the_queue():
    plain, urgent = _row(1, priority="medium"), _row(2, priority="high")
    assert service.rank_priority_rows([plain, urgent], NOW, 1)[0]["lead_id"] == 2


def test_connected_call_counts_once_a_day_and_clears_missed_calls():
    calls, refresh = _run_activity(_call())
    sql, values = calls[-1].args
    assert "unanswered_calls=0" in str(sql) and values["touch"] == 1
    refresh.assert_awaited_once()
    calls, _ = _run_activity(_call(), reached_today=True)
    assert calls[-1].args[1]["touch"] == 0


def test_missed_calls_count_and_a_wrong_number_counts_double():
    for outcome, missed in (("no_answer", 1), ("busy", 1), ("wrong_number", 2)):
        calls, refresh = _run_activity(_call(outcome))
        sql, values = calls[-1].args
        assert "last_contacted_at" not in str(sql) and values["missed"] == missed
        refresh.assert_awaited_once()


def test_a_note_does_not_change_priority():
    _, refresh = _run_activity(_call(kind="note"))
    refresh.assert_not_awaited()


def test_refresh_skips_a_lead_with_a_manual_priority():
    async def run():
        locked = MagicMock()
        locked.mappings.return_value.first.return_value = None
        db = SimpleNamespace(flush=AsyncMock(), execute=AsyncMock(return_value=locked))
        await service._refresh_auto_priority(db, 5)
        assert "priority_locked = false" in str(db.execute.await_args.args[0])
        assert db.execute.await_count == 1

    asyncio.run(run())


def test_refresh_saves_the_calculated_priority():
    async def run():
        row = MagicMock()
        row.mappings.return_value.first.return_value = {"stage": "enrolled", "source": "manual", "followup_count": 0, "unanswered_calls": 0, "payment_plan": None, "last_contacted_at": None, "created_at": NOW}
        db = SimpleNamespace(flush=AsyncMock(), execute=AsyncMock(return_value=row))
        await service._refresh_auto_priority(db, 5)
        assert db.execute.await_args.args[1] == {"priority": "low", "id": 5}

    asyncio.run(run())


def test_counsellor_queue_is_locked_to_their_own_leads():
    async def run():
        user = SimpleNamespace(user_id=17, email="c@example.test", access=["CMS", "COUNSELLOR"])
        with patch.object(service, "get_priority_queue", new_callable=AsyncMock) as queue:
            await router.get_priority_queue(counsellor_id=99, awarding_body=None, programme=None, unassigned=True, date_from=None, date_to=None, db=SimpleNamespace(), current_user=user)
        assert queue.await_args.args[1] == 17
        assert queue.await_args.args[4] is False

    asyncio.run(run())
