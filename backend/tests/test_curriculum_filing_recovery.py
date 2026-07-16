"""Regression coverage for curriculum-safe Kaizen filing recovery."""

from unittest.mock import AsyncMock, patch

import pytest
from telegram.ext import CallbackQueryHandler, ConversationHandler

from tests.bot_simulator import BotSimulator


def _active_cbd_draft():
    return {
        "_type": "FORM",
        "form_type": "CBD",
        "uuid": "uuid-cbd",
        "fields": {
            "date_of_encounter": "2026-07-16",
            "clinical_setting": "ED",
            "patient_presentation": "Chest pain",
            "clinical_reasoning": "Assessed and managed as possible ACS.",
            "reflection": "I would escalate earlier if symptoms recurred.",
            "curriculum_links": ["SLO1"],
        },
    }


def _failed_result(error: str):
    return {
        "status": "failed",
        "filled": [],
        "skipped": [],
        "method": "deterministic",
        "error": error,
    }


def test_alternative_curriculum_variants_resolve_in_both_directions():
    from bot import _alternative_curriculum_variant

    assert _alternative_curriculum_variant("CBD") == ("2021", "CBD_2021")
    assert _alternative_curriculum_variant("CBD_2021") == ("2025", "CBD")
    assert _alternative_curriculum_variant("ESLE_ASSESS") == ("2021", "ESLE_2021")
    assert _alternative_curriculum_variant("ESLE_2021") == ("2025", "ESLE_ASSESS")


def test_production_approval_state_routes_curriculum_choice_callbacks(monkeypatch, tmp_path):
    import bot

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("HOME", str(tmp_path))
    app = bot.build_application()
    case_conv = next(
        handler
        for handler in app.handlers[0]
        if isinstance(handler, ConversationHandler)
        and bot.AWAIT_APPROVAL in handler.states
    )
    handlers = case_conv.states[bot.AWAIT_APPROVAL]

    for callback_data in (
        "FILING_CURRICULUM|select|2021",
        "FILING_CURRICULUM|select|2025",
        "FILING_CURRICULUM|retry|2021",
        "FILING_CURRICULUM|retry|2025",
    ):
        matches = [
            handler
            for handler in handlers
            if isinstance(handler, CallbackQueryHandler)
            and handler.pattern.match(callback_data)
        ]
        assert matches, f"AWAIT_APPROVAL does not route {callback_data}"
        assert matches[0].callback is bot.handle_callback


@pytest.mark.asyncio
async def test_legacy_higher_profile_must_choose_curriculum_before_filing():
    from bot import AWAIT_APPROVAL, handle_approval_approve, handle_callback

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["draft_data"] = _active_cbd_draft()
    saved = {"curriculum": None}

    route_filing = AsyncMock(return_value=_failed_result("Browser unavailable"))

    def store_curriculum(_user_id, curriculum):
        saved["curriculum"] = curriculum

    with patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot.get_training_level", return_value="HIGHER"), \
         patch("bot.get_curriculum", side_effect=lambda _user_id: saved["curriculum"]), \
         patch("bot.store_curriculum", side_effect=store_curriculum) as store, \
         patch("bot.route_filing", new=route_filing), \
         patch("bot._alert_filing_failure", new=AsyncMock()):
        first = await handle_approval_approve(
            sim._make_callback_update("APPROVE|draft"), context
        )

        assert first == AWAIT_APPROVAL
        route_filing.assert_not_awaited()
        assert context.user_data["draft_data"] == _active_cbd_draft()
        assert "Nothing has been sent to Kaizen yet" in sim.get_last_text()
        assert ("📗 2021 Curriculum", "FILING_CURRICULUM|select|2021") in sim.get_last_buttons()
        assert ("📘 2025 Update", "FILING_CURRICULUM|select|2025") in sim.get_last_buttons()

        second = await handle_callback(
            sim._make_callback_update("FILING_CURRICULUM|select|2021"), context
        )

    assert second == AWAIT_APPROVAL
    store.assert_called_once_with(sim.user_id, "2021")
    route_filing.assert_awaited_once()
    assert route_filing.await_args.kwargs["form_type"] == "CBD_2021"
    assert route_filing.await_args.kwargs["reuse_draft"] is False


@pytest.mark.asyncio
async def test_known_curriculum_filing_behaviour_is_unchanged():
    from bot import AWAIT_APPROVAL, handle_approval_approve

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["draft_data"] = _active_cbd_draft()
    route_filing = AsyncMock(return_value=_failed_result("Browser unavailable"))

    with patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot.get_training_level", return_value="HIGHER"), \
         patch("bot.get_curriculum", return_value="2025"), \
         patch("bot.route_filing", new=route_filing), \
         patch("bot._alert_filing_failure", new=AsyncMock()):
        result = await handle_approval_approve(
            sim._make_callback_update("APPROVE|draft"), context
        )

    assert result == AWAIT_APPROVAL
    route_filing.assert_awaited_once()
    assert route_filing.await_args.kwargs["form_type"] == "CBD"


@pytest.mark.asyncio
async def test_form_unavailable_requires_explicit_alternative_curriculum_retry():
    from bot import AWAIT_APPROVAL, handle_approval_approve, handle_callback

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["draft_data"] = _active_cbd_draft()
    saved = {"curriculum": "2025"}
    route_filing = AsyncMock(side_effect=[
        _failed_result(
            "CBD is not available on your Kaizen profile or curriculum right now; "
            "Kaizen redirected to https://kaizenep.com/events/list instead of opening the form. "
            "No draft was written."
        ),
        _failed_result("Browser unavailable"),
    ])

    def store_curriculum(_user_id, curriculum):
        saved["curriculum"] = curriculum

    with patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot.get_training_level", return_value="HIGHER"), \
         patch("bot.get_curriculum", side_effect=lambda _user_id: saved["curriculum"]), \
         patch("bot.store_curriculum", side_effect=store_curriculum) as store, \
         patch("bot.route_filing", new=route_filing), \
         patch("bot._alert_filing_failure", new=AsyncMock()):
        first = await handle_approval_approve(
            sim._make_callback_update("APPROVE|draft"), context
        )

        assert first == AWAIT_APPROVAL
        assert route_filing.await_count == 1
        assert route_filing.await_args_list[0].kwargs["form_type"] == "CBD"
        assert ("🔄 Try 2021 curriculum", "FILING_CURRICULUM|retry|2021") in sim.get_last_buttons()
        assert "Nothing was written" in sim.get_last_text()
        assert context.user_data["draft_data"] == _active_cbd_draft()

        second = await handle_callback(
            sim._make_callback_update("FILING_CURRICULUM|retry|2021"), context
        )

    assert second == AWAIT_APPROVAL
    store.assert_called_once_with(sim.user_id, "2021")
    assert route_filing.await_count == 2
    assert route_filing.await_args_list[1].kwargs["form_type"] == "CBD_2021"
    # The rejected 2025 navigation wrote nothing, so the alternative is a new
    # form attempt. It must never search for and overwrite an older CBD draft.
    assert route_filing.await_args_list[1].kwargs["reuse_draft"] is False


@pytest.mark.asyncio
async def test_stale_curriculum_retry_button_cannot_switch_or_file_a_draft():
    from bot import AWAIT_APPROVAL, handle_callback

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["draft_data"] = _active_cbd_draft()

    with patch("bot.store_curriculum") as store, \
         patch("bot.route_filing", new=AsyncMock()) as route_filing:
        result = await handle_callback(
            sim._make_callback_update("FILING_CURRICULUM|retry|2021"), context
        )

    assert result == AWAIT_APPROVAL
    store.assert_not_called()
    route_filing.assert_not_awaited()
