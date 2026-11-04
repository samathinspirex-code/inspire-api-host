import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import ForbiddenError, ValidationError
from app.modules.crm import arena_rewards, arena_service, router, service
from app.modules.crm.arena_schemas import ArenaAwardIn, ArenaDecisionIn
from app.modules.crm.schemas import CrmActivityCreate

COUNSELLOR = SimpleNamespace(user_id=17, email="c@example.com", access=["COUNSELLOR"])
ADMIN = SimpleNamespace(user_id=1, email="a@example.com", access=["CMS_ADMIN"])
ADMIN_WITH_LEADS = SimpleNamespace(user_id=2, email="b@example.com", access=["COUNSELLOR", "CRM"])
DIALLER = next(quest for quest in arena_service.QUESTS if quest["id"] == "dialler")


def _result(scalar=None, first=None, mappings_first=None, mappings_all=None):
    result = MagicMock()
    result.scalar.return_value = scalar
    result.first.return_value = first
    result.mappings.return_value.first.return_value = mappings_first
    result.mappings.return_value.all.return_value = mappings_all or []
    return result


def test_colombo_day_turns_over_at_midnight_sri_lanka_time():
    assert arena_service.colombo_today(datetime(2026, 11, 3, 18, 29)) == date(2026, 11, 3)
    assert arena_service.colombo_today(datetime(2026, 11, 3, 18, 30)) == date(2026, 11, 4)
    assert arena_service.colombo_day_start(date(2026, 11, 4)) == datetime(2026, 11, 3, 18, 30)


def test_streak_ignores_a_missed_sunday_and_an_unfinished_today():
    # 2 Nov 2026 is a Monday, 1 Nov a Sunday.
    days = {date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 2)}
    assert arena_service.arena_streak(days, date(2026, 11, 2)) == 3
    assert arena_service.arena_streak(days, date(2026, 11, 3)) == 3
    assert arena_service.arena_streak(days, date(2026, 11, 4)) == 0


def test_streak_breaks_on_a_missed_weekday():
    days = {date(2026, 10, 28), date(2026, 10, 30)}
    assert arena_service.arena_streak(days, date(2026, 10, 30)) == 1


def test_streak_bonus_grows_then_caps():
    assert arena_service.streak_bonus_percent(0) == 0
    assert arena_service.streak_bonus_xp(100, 3) == 30
    assert arena_service.streak_bonus_xp(100, 5) == 50
    assert arena_service.streak_bonus_xp(100, 30) == 50


def test_season_xp_uses_the_stage_values():
    counts = {"assigned": 2, "reached": 1, "follow_up": 1, "future_prospect": 1, "potential": 1, "enrolled": 1, "claimed": 100}
    assert arena_service.season_xp_total(counts) == 10 + 40 + 20 + 30 + 60 + 500 + 100


def test_quest_levels_unlock_one_at_a_time():
    reached_two = arena_service.quest_state(DIALLER, 12, set(), 20)
    assert reached_two["claimable_level"] == 1
    assert reached_two["bonus_xp"] == 10
    after_first = arena_service.quest_state(DIALLER, 12, {1})
    assert after_first["claimable_level"] == 2
    assert after_first["target"] == 10
    not_yet = arena_service.quest_state(DIALLER, 12, {1, 2})
    assert not_yet["claimable_level"] is None
    assert not_yet["target"] == 20
    finished = arena_service.quest_state(DIALLER, 25, {1, 2, 3})
    assert finished["done"] is True
    assert finished["value"] == 20


def test_claim_pays_the_level_plus_the_streak_bonus():
    async def run():
        db = SimpleNamespace(execute=AsyncMock(return_value=_result(scalar=9)), commit=AsyncMock(), rollback=AsyncMock())
        with (
            patch.object(arena_service, "colombo_today", return_value=date(2026, 11, 3)),
            patch.object(arena_service, "_progress", new_callable=AsyncMock, return_value={"dialler": 5}),
            patch.object(arena_service, "_active_days", new_callable=AsyncMock, return_value={17: {date(2026, 11, 3): 5, date(2026, 11, 2): 1}}),
        ):
            paid = await arena_service.claim(db, 17, "dialler", 1)
        assert paid == {"quest_id": "dialler", "level": 1, "base_xp": 50, "streak_bonus_xp": 10, "xp": 60}
        db.commit.assert_awaited_once()

    asyncio.run(run())


def test_claim_refuses_an_unfinished_level():
    async def run():
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        with patch.object(arena_service, "_progress", new_callable=AsyncMock, return_value={"dialler": 9}):
            with pytest.raises(ValidationError):
                await arena_service.claim(db, 17, "dialler", 2)
        db.execute.assert_not_awaited()

    asyncio.run(run())


def test_repeat_claim_is_an_error_not_a_second_payout():
    async def run():
        db = SimpleNamespace(execute=AsyncMock(return_value=_result(scalar=None)), commit=AsyncMock(), rollback=AsyncMock())
        with (
            patch.object(arena_service, "_progress", new_callable=AsyncMock, return_value={"dialler": 5}),
            patch.object(arena_service, "_active_days", new_callable=AsyncMock, return_value={}),
        ):
            with pytest.raises(ValidationError):
                await arena_service.claim(db, 17, "dialler", 1)
        db.commit.assert_not_awaited()

    asyncio.run(run())


def test_raid_only_pays_once_it_is_cleared():
    async def run():
        db = SimpleNamespace(execute=AsyncMock(return_value=_result(scalar=3)), commit=AsyncMock(), rollback=AsyncMock())
        with patch.object(arena_service, "_raid", new_callable=AsyncMock, return_value={"complete": False}):
            with pytest.raises(ValidationError):
                await arena_service.claim(db, 17, arena_service.RAID_QUEST_ID)
        db.commit.assert_not_awaited()

    asyncio.run(run())


def test_counsellor_feed_hides_lead_names_and_notes():
    async def run():
        snap = {"activity": [
            {"text": "Jennifer · Parent asked about fees · Hafsa Perera", "kind": "call", "counsellor": "Jennifer", "hours_ago": 1.0},
            {"text": "Jennifer · Stage changed from 'potential' to 'enrolled' · Hafsa Perera", "kind": "stage_change", "counsellor": "Jennifer", "hours_ago": 2.0},
        ]}
        claims = MagicMock()
        claims.all.return_value = []
        db = SimpleNamespace(execute=AsyncMock(return_value=claims))
        public = await arena_service._feed(db, snap, [], full_detail=False)
        assert [item["text"] for item in public] == ["logged a call", "enrolled a student"]
        assert "Hafsa" not in str(public)
        admin = await arena_service._feed(db, snap, [], full_detail=True)
        assert "Hafsa" in admin[0]["text"]

    asyncio.run(run())


def test_admins_without_the_counsellor_role_cannot_claim_or_collect():
    async def run():
        with pytest.raises(ForbiddenError):
            await router.arena_claim(router.ArenaClaimIn(quest_id="dialler"), current_user=ADMIN, db=SimpleNamespace())
        with pytest.raises(ForbiddenError):
            await router.arena_collect_reward(1, current_user=ADMIN, db=SimpleNamespace())
        with pytest.raises(ForbiddenError):
            await router.arena_my_rewards(current_user=ADMIN, db=SimpleNamespace())

    asyncio.run(run())


def test_counsellor_who_is_also_an_admin_can_claim_and_collect():
    async def run():
        db = SimpleNamespace()
        with patch("app.modules.crm.arena_service.claim", new_callable=AsyncMock) as claim:
            await router.arena_claim(router.ArenaClaimIn(quest_id="past_work"), current_user=ADMIN_WITH_LEADS, db=db)
        claim.assert_awaited_once_with(db, 2, "past_work", 1)
        with patch.object(arena_rewards, "collect", new_callable=AsyncMock) as collect:
            await router.arena_collect_reward(4, current_user=ADMIN_WITH_LEADS, db=db)
        collect.assert_awaited_once_with(db, 2, 4)

    asyncio.run(run())


def test_counsellors_cannot_manage_or_distribute_rewards():
    async def run():
        for call in (
            router.arena_list_rewards(current_user=COUNSELLOR, db=SimpleNamespace()),
            router.arena_reward_collections(current_user=COUNSELLOR, db=SimpleNamespace()),
            router.arena_void_reward_code(1, current_user=COUNSELLOR, db=SimpleNamespace()),
            router.arena_distribute_reward(1, ArenaDecisionIn(), current_user=COUNSELLOR, db=SimpleNamespace()),
            router.arena_decline_reward(1, ArenaDecisionIn(), current_user=COUNSELLOR, db=SimpleNamespace()),
            router.arena_award_reward(1, ArenaAwardIn(user_id=9), current_user=COUNSELLOR, db=SimpleNamespace()),
        ):
            with pytest.raises(ForbiddenError):
                await call

    asyncio.run(run())


def test_counsellor_only_gets_their_own_card():
    async def run():
        with patch("app.modules.crm.arena_service.arena", new_callable=AsyncMock) as build:
            await router.arena(counsellor_id=99, mine=False, current_user=COUNSELLOR, db=SimpleNamespace())
            assert build.await_args.args[1:] == (17, True, 17)
            assert build.await_args.kwargs == {"full_detail": False}
            await router.arena(counsellor_id=99, mine=False, current_user=ADMIN, db=SimpleNamespace())
            assert build.await_args.args[1:] == (1, False, 99)
            assert build.await_args.kwargs == {"full_detail": True}
            await router.arena(counsellor_id=None, mine=True, current_user=ADMIN, db=SimpleNamespace())
            assert build.await_args.args[1:] == (1, False, None)
            await router.arena(counsellor_id=None, mine=True, current_user=ADMIN_WITH_LEADS, db=SimpleNamespace())
            assert build.await_args.args[1:] == (2, True, 2)
            assert build.await_args.kwargs == {"full_detail": True}
            await router.arena(counsellor_id=99, mine=False, current_user=ADMIN_WITH_LEADS, db=SimpleNamespace())
            assert build.await_args.args[1:] == (2, True, 99)

    asyncio.run(run())


def test_cms_admin_reaches_arena_reward_paths_only():
    request = SimpleNamespace(url=SimpleNamespace(path="/api/v1/cms/crm/arena/rewards"))
    assert router.require_crm_staff(request, ADMIN) is ADMIN
    other = SimpleNamespace(url=SimpleNamespace(path="/api/v1/cms/crm/leads"))
    with pytest.raises(ForbiddenError):
        router.require_crm_staff(other, ADMIN)


def test_reward_below_tier_cannot_be_collected():
    async def run():
        reward = {"reward_id": 4, "reward_type": "voucher", "title": "Coffee voucher", "tier_index": 2, "is_active": True}
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        with (
            patch.object(arena_rewards, "_get_reward", new_callable=AsyncMock, return_value=reward),
            patch.object(arena_rewards, "season_xp", new_callable=AsyncMock, return_value=1500),
        ):
            with pytest.raises(ValidationError):
                await arena_rewards.collect(db, 17, 4)
        db.execute.assert_not_awaited()

    asyncio.run(run())


def test_voucher_with_no_stock_tells_the_admin():
    async def run():
        db = SimpleNamespace(execute=AsyncMock(return_value=_result(first=None)))
        with pytest.raises(ValidationError, match="No codes left"):
            await arena_rewards._take_code(db, {"reward_id": 1, "reward_type": "voucher", "title": "Coffee"}, None)

    asyncio.run(run())


def test_gift_needs_no_code_and_typed_code_uses_no_stock():
    async def run():
        db = SimpleNamespace(execute=AsyncMock(return_value=_result(first=None)))
        assert await arena_rewards._take_code(db, {"reward_id": 1, "reward_type": "gift", "title": "Mug"}, None) == (None, None)
        db.execute.reset_mock()
        assert await arena_rewards._take_code(db, {"reward_id": 1, "reward_type": "voucher", "title": "Coffee"}, "TYPED") == (None, "TYPED")
        db.execute.assert_not_awaited()

    asyncio.run(run())


def test_code_is_marked_issued_when_taken():
    async def run():
        db = SimpleNamespace(execute=AsyncMock(side_effect=[_result(first=(7, "ABC123")), _result()]))
        taken = await arena_rewards._take_code(db, {"reward_id": 1, "reward_type": "voucher", "title": "Coffee"}, None)
        assert taken == (7, "ABC123")
        assert "SKIP LOCKED" in str(db.execute.await_args_list[0].args[0])
        assert "status = 'issued'" in str(db.execute.await_args_list[1].args[0])

    asyncio.run(run())


def test_a_decided_request_cannot_be_distributed_again():
    async def run():
        row = {"collection_id": 3, "reward_id": 1, "status": "given"}
        db = SimpleNamespace(execute=AsyncMock(return_value=_result(mappings_first=row)), commit=AsyncMock())
        with pytest.raises(ValidationError):
            await arena_rewards.distribute(db, 3, ArenaDecisionIn(), 1)
        db.commit.assert_not_awaited()

    asyncio.run(run())


def test_only_given_rewards_show_a_code_to_the_counsellor():
    async def run():
        base = {"season_number": 1, "reward_id": 1, "reward_type": "voucher", "brand": "Coffee", "title": "Voucher", "value_label": None, "description": None, "tier_index": 0, "is_active": True, "source": "tier", "note": None, "requested_at": None, "decided_at": None, "expires_at": None}
        declined = {**base, "collection_id": 1, "status": "declined", "issued_code": "SHOULD-HIDE"}
        given = {**base, "reward_id": 2, "collection_id": 2, "status": "given", "issued_code": "ABC123"}
        db = SimpleNamespace(execute=AsyncMock(side_effect=[_result(mappings_all=[]), _result(mappings_all=[declined, given])]))
        with (
            patch.object(arena_rewards, "season_xp", new_callable=AsyncMock, return_value=0),
            patch.object(arena_rewards, "current_season", new_callable=AsyncMock, return_value=(1, datetime(2026, 11, 4))),
        ):
            items = await arena_rewards.my_rewards(db, 17)
        assert {item["state"]: item["code"] for item in items} == {"declined": None, "given": "ABC123"}
        assert "SHOULD-HIDE" not in str(items)

    asyncio.run(run())


def test_call_needs_an_outcome_and_other_types_reject_one():
    assert CrmActivityCreate(activity_type="call", call_outcome="no_answer").content == ""
    with pytest.raises(PydanticValidationError):
        CrmActivityCreate(activity_type="call", content="Spoke to them")
    with pytest.raises(PydanticValidationError):
        CrmActivityCreate(activity_type="note", content="Hi", call_outcome="connected")
    with pytest.raises(PydanticValidationError):
        CrmActivityCreate(activity_type="call", call_outcome="voicemail")
    with pytest.raises(PydanticValidationError):
        CrmActivityCreate(activity_type="note", content="  ")


@pytest.mark.parametrize("outcome,marks_contacted", [("no_answer", False), ("busy", False), ("wrong_number", False), ("connected", True), ("callback_requested", True)])
def test_only_a_reached_call_marks_the_lead_as_contacted(outcome, marks_contacted):
    async def run():
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock())
        activity = SimpleNamespace(activity_id=1, lead_id=5, activity_type="call", content="x", call_outcome=outcome, counsellor_name="C", counsellor_id=17, created_at=datetime(2026, 11, 3))
        activity_repo = SimpleNamespace(create=AsyncMock(return_value=activity))
        with (
            patch.object(service, "CrmLeadRepository", return_value=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(lead_id=5)))),
            patch.object(service, "CrmActivityRepository", return_value=activity_repo),
            patch.object(service, "_refresh_auto_priority", new_callable=AsyncMock),
        ):
            await service.add_activity(db, 5, CrmActivityCreate(activity_type="call", call_outcome=outcome), counsellor_id=17)
        sql = str(db.execute.await_args.args[0])
        assert ("last_contacted_at" in sql) is marks_contacted
        saved = activity_repo.create.await_args.args[0]
        assert saved["call_outcome"] == outcome
        assert saved["content"]

    asyncio.run(run())


def test_past_work_rates_and_what_is_left_to_claim():
    totals = {"past_reached": 2139, "past_enrolled": 111}
    assert arena_service.past_work_total(2139, 111) == 9828
    assert arena_service._past_work_view(totals, 0)["available"] == 9828
    assert arena_service._past_work_view(totals, 9000)["available"] == 828
    assert arena_service._past_work_view(totals, 9999)["available"] == 0


def test_past_work_claim_pays_only_what_is_left_and_has_no_bonus():
    async def run():
        db = SimpleNamespace(execute=AsyncMock(return_value=_result(scalar=9)), commit=AsyncMock(), rollback=AsyncMock())
        with (
            patch.object(arena_service, "_lead_totals", new_callable=AsyncMock, return_value={17: {"past_reached": 100, "past_enrolled": 10}}),
            patch.object(arena_service, "_past_work_claimed", new_callable=AsyncMock, return_value={17: (200, 1)}),
        ):
            paid = await arena_service.claim(db, 17, arena_service.PAST_WORK_ID)
        assert paid == {"quest_id": "past_work", "level": 2, "base_xp": 500, "streak_bonus_xp": 0, "xp": 500}

    asyncio.run(run())


def test_nothing_left_to_claim_is_refused():
    async def run():
        db = SimpleNamespace(execute=AsyncMock(return_value=_result()), commit=AsyncMock(), rollback=AsyncMock())
        with (
            patch.object(arena_service, "_lead_totals", new_callable=AsyncMock, return_value={}),
            patch.object(arena_service, "_past_work_claimed", new_callable=AsyncMock, return_value={}),
        ):
            with pytest.raises(ValidationError):
                await arena_service.claim(db, 17, arena_service.PAST_WORK_ID)
        db.commit.assert_not_awaited()

    asyncio.run(run())


def test_only_arena_admins_can_reset_and_must_type_reset():
    async def run():
        with pytest.raises(ForbiddenError):
            await router.arena_reset_season(router.ArenaSeasonResetIn(confirm="RESET"), current_user=COUNSELLOR, db=SimpleNamespace())
        with pytest.raises(ForbiddenError):
            await router.arena_seasons(current_user=COUNSELLOR, db=SimpleNamespace())
        with patch("app.modules.crm.arena_service.reset_season", new_callable=AsyncMock) as reset:
            with pytest.raises(ValidationError):
                await router.arena_reset_season(router.ArenaSeasonResetIn(confirm="reset"), current_user=ADMIN, db=SimpleNamespace())
            reset.assert_not_awaited()
            db = SimpleNamespace()
            await router.arena_reset_season(router.ArenaSeasonResetIn(confirm="RESET"), current_user=ADMIN, db=db)
            reset.assert_awaited_once_with(db, 1)

    asyncio.run(run())
