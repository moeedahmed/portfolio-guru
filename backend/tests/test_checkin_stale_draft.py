"""Beta feedback, 27 Sep 2026: the Sunday check-in arrived as two messages,
and the tester's "let me get on with my work" reply was added to an old
Reflective Practice Log draft. These pin both fixes."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import bot
from bot import AWAIT_TEMPLATE_REVIEW, handle_case_input
from tests.bot_simulator import BotSimulator


def _patches(analyse):
    return (
        patch("bot.has_credentials", return_value=True),
        patch("bot.consent.has_current_consent", new=AsyncMock(return_value=True)),
        patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))),
        patch("bot._analyse_selected_form", new=analyse),
    )


def _open_draft_context(sim, started_at):
    context = sim._make_context()
    context.user_data["chosen_form"] = "REFLECT_LOG"
    context.user_data["awaiting_detail"] = True
    context.user_data["case_text"] = "Initial reflective practice log notes."
    if started_at is not None:
        context.user_data["awaiting_detail_at"] = started_at
    return context


@pytest.mark.asyncio
async def test_reply_to_old_open_draft_asks_before_changing_it(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = _open_draft_context(sim, time.time() - 7 * 24 * 3600)
    analyse = AsyncMock()
    p1, p2, p3, p4 = _patches(analyse)
    with p1, p2, p3, p4:
        result = await handle_case_input(sim._make_text_update("Bc kaam krny de"), context)

    assert result == AWAIT_TEMPLATE_REVIEW
    analyse.assert_not_awaited()
    assert context.user_data["case_text"] == "Initial reflective practice log notes."
    assert context.user_data["pending_new_case_text"] == "Bc kaam krny de"
    text = sim.get_last_text()
    assert "Reflective Practice Log draft from earlier is still open" in text
    assert "Updating" not in text
    assert sim.get_last_buttons() == [
        ("➕ New case", "CASE|new"),
        ("✏️ Add to draft", "CASE|improve"),
        ("❌ Cancel", "ACTION|cancel"),
    ]


@pytest.mark.asyncio
async def test_detail_for_recent_open_draft_is_still_added(monkeypatch):
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    sim = BotSimulator()
    context = _open_draft_context(sim, time.time() - 60)
    analyse = AsyncMock(return_value={})
    p1, p2, p3, p4 = _patches(analyse)
    with p1, p2, p3, p4, \
         patch("bot._essentials_gate_before_draft", new=AsyncMock(return_value=None)), \
         patch("bot._draft_has_useful_content", return_value=False), \
         patch("bot._ask_for_more_detail_before_draft", new=AsyncMock(return_value=0)):
        await handle_case_input(sim._make_text_update("It was a night shift in resus."), context)

    assert "It was a night shift in resus." in context.user_data["case_text"]
    assert "still open" not in (sim.get_last_text() or "")


def test_restored_drafts_without_a_timestamp_are_marked_old():
    restored = {
        1: {"awaiting_detail": True, "chosen_form": "REFLECT_LOG"},
        2: {"awaiting_detail": True, "chosen_form": "CBD", "awaiting_detail_at": 123.0},
        3: {"chosen_form": "CBD"},
    }
    assert bot._stamp_undated_open_drafts(restored) == 1
    assert restored[1]["awaiting_detail_at"] == 0.0
    assert restored[2]["awaiting_detail_at"] == 123.0
    assert "awaiting_detail_at" not in restored[3]
    assert bot._open_draft_is_stale(SimpleNamespace(user_data=restored[1]))


@pytest.mark.asyncio
async def test_weekly_digest_is_one_message_with_the_card(monkeypatch, tmp_path):
    chart = tmp_path / "card.png"
    chart.write_bytes(b"png")
    monkeypatch.setattr(bot, "data_path", lambda name: tmp_path / name)
    monkeypatch.setattr(bot, "get_all_active_users", AsyncMock(return_value=[42]))
    monkeypatch.setattr(bot, "_proactive_owns", lambda _uid: False)
    monkeypatch.setattr(bot, "_compute_weekly_stats", AsyncMock(return_value={"cases": 0, "gap": None}))
    import portfolio_chart
    monkeypatch.setattr(portfolio_chart, "generate_weekly_nudge_chart_async", AsyncMock(return_value=str(chart)))
    fake_bot = SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock())

    await bot.weekly_push(SimpleNamespace(bot=fake_bot))

    fake_bot.send_photo.assert_awaited_once()
    assert "No Portfolio Guru cases filed this week" in fake_bot.send_photo.await_args.kwargs["caption"]
    fake_bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_weekly_digest_falls_back_to_text_when_card_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "data_path", lambda name: tmp_path / name)
    monkeypatch.setattr(bot, "get_all_active_users", AsyncMock(return_value=[42]))
    monkeypatch.setattr(bot, "_proactive_owns", lambda _uid: False)
    monkeypatch.setattr(bot, "_compute_weekly_stats", AsyncMock(return_value={"cases": 0, "gap": None}))
    import portfolio_chart
    monkeypatch.setattr(portfolio_chart, "generate_weekly_nudge_chart_async", AsyncMock(side_effect=RuntimeError("x")))
    fake_bot = SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock())

    await bot.weekly_push(SimpleNamespace(bot=fake_bot))

    fake_bot.send_photo.assert_not_awaited()
    fake_bot.send_message.assert_awaited_once()
