"""Until public launch every user is an unlimited beta user and payments are off.

Moeed, 2026-09-28. ``PG_PAYMENTS_ENABLED=1`` brings the paid plans back.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler


@pytest.fixture
def beta_usage(tmp_path, monkeypatch):
    import usage

    monkeypatch.delenv("PG_PAYMENTS_ENABLED", raising=False)
    monkeypatch.setattr(usage, "DB_PATH", str(tmp_path / "usage.db"))
    return usage


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
async def test_beta_command_needs_no_request(monkeypatch):
    import bot

    monkeypatch.delenv("PG_PAYMENTS_ENABLED", raising=False)
    store = MagicMock()
    monkeypatch.setattr("supabase_sync.store_beta_request", store)
    update = _update()
    await bot.beta_command(update, _context())

    store.assert_not_called()
    update.message.reply_text.assert_awaited_once_with(bot._BETA_PLAN_TEXT)


@pytest.mark.asyncio
async def test_free_user_gets_portfolio_health_without_upgrade(beta_usage):
    import bot

    assert await bot._health_gate_check(205)


@pytest.mark.asyncio
async def test_free_user_gets_unsigned_scan_without_upgrade(beta_usage, monkeypatch):
    """/unsigned is open to every beta user (Moeed, 2026-09-29)."""
    import bot

    monkeypatch.setattr(bot, "_is_passwordless_user", lambda _uid: False)
    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    picker = AsyncMock()
    monkeypatch.setattr(bot, "_show_unsigned_range_picker", picker)
    update = _update()

    await bot.unsigned_command(update, _context())

    picker.assert_awaited_once()
    update.message.reply_text.assert_not_awaited()
