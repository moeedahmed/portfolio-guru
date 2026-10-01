"""Until public launch every user is an unlimited beta user and payments are off.

Moeed, 2026-09-28. ``PG_PAYMENTS_ENABLED=1`` brings the paid plans back.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

import pytest
from telegram.ext import ConversationHandler


@pytest.fixture
def beta_usage(tmp_path, monkeypatch):
    import usage

    monkeypatch.delenv("PG_PAYMENTS_ENABLED", raising=False)
    monkeypatch.delenv("PG_FREE_TRIAL_ENABLED", raising=False)
    monkeypatch.setattr(usage, "DB_PATH", str(tmp_path / "usage.db"))
    return usage


@pytest.fixture
def trial_usage(beta_usage, monkeypatch):
    monkeypatch.setenv("PG_PAYMENTS_ENABLED", "1")
    monkeypatch.setenv("PG_FREE_TRIAL_ENABLED", "1")
    clock = {"now": datetime(2026, 10, 1, 12, tzinfo=timezone.utc)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].astimezone(tz) if tz else clock["now"].replace(tzinfo=None)

    monkeypatch.setattr(beta_usage, "datetime", Clock)
    return beta_usage, clock


async def test_trial_starts_on_first_use_and_expires_at_exact_14_days(trial_usage):
    usage, clock = trial_usage
    assert await usage.get_trial_started_at(301) is None
    assert await usage.check_can_file(301) == (True, 0, -1, "trial")
    started = await usage.get_trial_started_at(301)
    assert started == clock["now"]
    for _ in range(8):
        await usage.record_case_filed(301, "CBD")
    clock["now"] = started + timedelta(days=14) - timedelta(microseconds=1)
    assert await usage.check_can_file(301) == (True, 8, -1, "trial")
    clock["now"] += timedelta(microseconds=1)
    assert await usage.check_can_file(301) == (False, 8, 0, "trial")
    clock["now"] += timedelta(days=40)
    assert not (await usage.check_can_file(301))[0]
    assert await usage.get_trial_started_at(301) == started


@pytest.mark.parametrize("tier,limit", [("pro", 100), ("pro_plus", -1)])
async def test_existing_subscribers_do_not_start_a_trial(trial_usage, tier, limit):
    usage, clock = trial_usage
    await usage.set_user_tier(302, tier)
    assert await usage.check_can_file(302) == (True, 0, limit, tier)
    clock["now"] += timedelta(days=30)
    assert (await usage.check_can_file(302))[0]
    assert await usage.get_trial_started_at(302) is None


async def test_trial_cannot_cap_beta_or_enable_payments(trial_usage, monkeypatch):
    usage, clock = trial_usage
    await usage.set_beta_tester(303, True)
    assert await usage.check_can_file(303) == (True, 0, -1, "beta")
    assert await usage.get_trial_started_at(303) is None
    monkeypatch.delenv("PG_PAYMENTS_ENABLED")
    clock["now"] += timedelta(days=30)
    assert await usage.check_can_file(304) == (True, 0, -1, "beta")
    assert await usage.get_trial_started_at(304) is None
    assert not usage.payments_enabled()


async def test_trial_clock_is_not_restarted_by_concurrent_first_use(trial_usage):
    import asyncio
    usage, clock = trial_usage
    assert all(row[0] for row in await asyncio.gather(*(usage.check_can_file(305) for _ in range(3))))
    assert await usage.get_trial_started_at(305) == clock["now"]


async def test_trial_flag_defaults_off_and_only_accepts_one(beta_usage, monkeypatch):
    for value in (None, "0", "true"):
        if value is None:
            monkeypatch.delenv("PG_FREE_TRIAL_ENABLED", raising=False)
        else:
            monkeypatch.setenv("PG_FREE_TRIAL_ENABLED", value)
        assert not beta_usage.free_trial_enabled()


async def test_trial_read_only_access_check_does_not_start_clock(trial_usage):
    usage, clock = trial_usage
    assert not await usage.has_unlimited_access(306)
    assert await usage.get_trial_started_at(306) is None
    await usage.check_can_file(306)
    assert await usage.has_unlimited_access(306)
    clock["now"] += timedelta(days=14)
    assert not await usage.has_unlimited_access(306)


async def test_beta_access_overrides_an_expired_trial(trial_usage, monkeypatch):
    usage, clock = trial_usage
    await usage.check_can_file(307)
    started = await usage.get_trial_started_at(307)
    clock["now"] += timedelta(days=14)
    assert not (await usage.check_can_file(307))[0]
    monkeypatch.delenv("PG_PAYMENTS_ENABLED")
    assert await usage.check_can_file(307) == (True, 0, -1, "beta")
    assert await usage.get_trial_started_at(307) == started


async def test_expired_trial_cannot_save_an_open_draft(trial_usage, monkeypatch):
    usage, clock = trial_usage
    import bot
    await usage.check_can_file(308)
    clock["now"] += timedelta(days=14)
    context = _context()
    context.user_data["draft_data"] = {"_type": "FORM", "form_type": "DOPS", "fields": {"summary": "synthetic"}}
    update = _update()
    update.effective_user.id = 308
    update.callback_query = None
    with patch("bot.route_filing", AsyncMock()) as file, patch("bot.get_credentials", return_value=("fake", "fake")):
        await bot.handle_approval_approve(update, context)
    file.assert_not_awaited()
    assert context.user_data["draft_data"]
    assert update.message.reply_text.await_args.args[0] == bot._TRIAL_EXPIRED_TEXT



@pytest.mark.asyncio
async def test_free_user_past_the_old_cap_can_still_file(beta_usage):
    usage = beta_usage
    for _ in range(usage.TIER_LIMITS["free"] + 3):
        await usage.record_case_filed(202, "CBD")

    allowed, used, limit, tier = await usage.check_can_file(202)

    assert (allowed, used, limit, tier) == (True, 8, -1, "beta")
    assert await usage.has_unlimited_access(202)


@pytest.mark.asyncio
async def test_payments_setting_restores_the_free_cap(beta_usage, monkeypatch):
    usage = beta_usage
    monkeypatch.setenv("PG_PAYMENTS_ENABLED", "1")
    for _ in range(usage.TIER_LIMITS["free"]):
        await usage.record_case_filed(203, "CBD")

    allowed, _, _, tier = await usage.check_can_file(203)

    assert (allowed, tier) == (False, "free")
    assert not await usage.has_unlimited_access(203)


@pytest.mark.asyncio
async def test_new_checkout_is_refused_while_payments_are_off(monkeypatch):
    import stripe_handler

    monkeypatch.delenv("PG_PAYMENTS_ENABLED", raising=False)
    create = MagicMock()
    monkeypatch.setattr(stripe_handler.stripe.checkout.Session, "create", create)

    with pytest.raises(stripe_handler.PaymentsDisabledError):
        await stripe_handler.create_checkout_session(1, "pro_plus")
    create.assert_not_called()


def _update(callback: str | None = None):
    update = MagicMock()
    update.effective_user.id = 204
    update.effective_user.username = "beta_doc"
    update.message.reply_text = AsyncMock()
    if callback:
        update.callback_query.data = callback
        update.callback_query.answer = AsyncMock()
    return update


def _context():
    context = MagicMock()
    context.user_data = {}
    return context


@pytest.mark.asyncio
async def test_upgrade_command_says_beta_and_offers_no_payment(monkeypatch):
    import bot

    monkeypatch.delenv("PG_PAYMENTS_ENABLED", raising=False)
    with patch("bot.get_user_tier", AsyncMock(return_value="free")), \
         patch("bot.get_cases_this_month", AsyncMock(return_value=9)), \
         patch("bot._flow_msg", AsyncMock()) as flow_msg:
        result = await bot.upgrade_command(_update(), _context())

    assert result == ConversationHandler.END
    text = flow_msg.await_args.args[2]
    assert text == bot._BETA_PLAN_TEXT
    assert "£" not in text
    assert "reply_markup" not in flow_msg.await_args.kwargs
    assert bot._upgrade_buttons("free") == []


@pytest.mark.asyncio
async def test_old_upgrade_button_opens_no_checkout(monkeypatch):
    import bot

    monkeypatch.delenv("PG_PAYMENTS_ENABLED", raising=False)
    checkout = AsyncMock(return_value="https://checkout.example")
    with patch("bot.get_user_tier", AsyncMock(return_value="free")), \
         patch("stripe_handler.create_checkout_session", checkout), \
         patch("bot._flow_edit", AsyncMock()) as flow_edit:
        await bot.handle_upgrade_button(_update("UPGRADE|pro_plus"), _context())

    checkout.assert_not_awaited()
    assert flow_edit.await_args.args[2] == bot._BETA_PLAN_TEXT


@pytest.mark.asyncio
async def test_settings_callback_shows_unlimited_beta_with_payments_off(beta_usage, monkeypatch):
    import bot

    monkeypatch.setattr(bot, "_kaizen_connected", lambda _uid: False)
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))
    update = _update("ACTION|settings")
    update.callback_query.message.edit_text = AsyncMock()

    await bot.handle_action_button(update, _context())

    text = update.callback_query.message.edit_text.await_args.args[0]
    assert "Plan: Beta (unlimited)" in text
    assert "Usage: 0/5" not in text
    assert "Plan: Free" not in text


@pytest.mark.asyncio
async def test_retired_beta_command_needs_no_request(monkeypatch):
    import bot
    store = MagicMock()
    monkeypatch.setattr("supabase_sync.store_beta_request", store)
    update = _update()
    await bot.unknown_command(update, _context())
    store.assert_not_called()
    update.message.reply_text.assert_awaited_once_with(bot.UNKNOWN_COMMAND_MSG)


@pytest.mark.asyncio
async def test_free_user_gets_portfolio_health_without_upgrade(beta_usage):
    import bot

    assert await bot._health_gate_check(205)


@pytest.mark.asyncio
@pytest.mark.parametrize("passwordless", [False, True])
@pytest.mark.parametrize("command", ["unsigned", "health"])
async def test_health_entry_opens_requested_view_for_every_beta_user(beta_usage, monkeypatch, passwordless, command):
    import bot
    monkeypatch.setattr(bot, "_is_passwordless_user", lambda _uid: passwordless)
    context = _context()
    landing = "📊 Portfolio Health"
    awaiting = "📬 With assessor — synthetic evidence"
    async def run_health(**kwargs):
        assert kwargs["context_store"] is context
        bot._store_health_report_context(context, views={"priorities": landing}, action_pages=[],
            action_queue_pages={"awaiting": [awaiting]}, action_queue_totals={"awaiting": 1},
            needs_review_month=False)
        await kwargs["send_result"](landing, None)
    monkeypatch.setattr(bot, "_run_health_with_optional_kaizen_sync", run_health)
    update = _update()
    await getattr(bot, command + "_command")(update, context)
    reply = update.message.reply_text.await_args
    assert reply.args[0] == (awaiting if command == "unsigned" else landing)
    assert reply.kwargs["parse_mode"] == "Markdown"
    if command == "unsigned":
        assert reply.kwargs["reply_markup"] == bot._health_view_payload(context, "action_queue", page=0, queue="awaiting")[1]


@pytest.mark.asyncio
async def test_unsigned_respects_health_gate(monkeypatch):
    import bot
    gate = AsyncMock(return_value=False)
    run = AsyncMock()
    monkeypatch.setattr(bot, '_health_gate_check', gate)
    monkeypatch.setattr(bot, '_run_health_with_optional_kaizen_sync', run)
    update = _update()
    await bot.unsigned_command(update, _context())
    gate.assert_awaited_once_with(update.effective_user.id)
    run.assert_not_awaited()
    assert 'Portfolio Health is included' in update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_unsigned_without_queue_keeps_health_landing(monkeypatch):
    import bot
    monkeypatch.setattr(bot, '_health_gate_check', AsyncMock(return_value=True))
    async def run(**kwargs):
        await kwargs['send_result']('📊 Portfolio Health', None)
    monkeypatch.setattr(bot, '_run_health_with_optional_kaizen_sync', run)
    update = _update()
    await bot.unsigned_command(update, _context())
    assert update.message.reply_text.await_args.args[0] == '📊 Portfolio Health'


@pytest.mark.asyncio
async def test_upgrade_copy_never_advertises_retired_bulk_or_chase(monkeypatch):
    import bot
    monkeypatch.setenv('PG_PAYMENTS_ENABLED', '1')
    with patch('bot.get_user_tier', AsyncMock(return_value='free')), \
         patch('bot.get_cases_this_month', AsyncMock(return_value=0)), \
         patch('bot._flow_msg', AsyncMock()) as flow_msg:
        await bot.upgrade_command(_update(), _context())
    text = flow_msg.await_args.args[2].lower()
    assert 'bulk' not in text
    assert 'chase' not in text
    assert 'coming soon' not in text
