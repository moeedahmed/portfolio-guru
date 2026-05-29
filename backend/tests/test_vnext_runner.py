"""Tests for the vNext polling runner.

Covers:
- per-chat workspace management
- /start and /reset command handlers
- message handler routing through the engine
- voice/image/document acknowledgment (no downloads, no extraction)
- save-request dogfood-safe reply
- main() safety guards
- reply builder for all ActionKind values
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import vnext_runner
from conversational_case_engine import (
    ActionKind,
    CaseState,
    EngineSnapshot,
    NextAction,
    new_workspace,
)
from conversational_vnext_bot import PRODUCTION_TOKEN_ENVS, VNEXT_TOKEN_ENV


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_update(chat_id: int = 42, message_text: str | None = "hello") -> MagicMock:
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    msg.text = message_text
    msg.caption = None
    msg.voice = None
    msg.audio = None
    msg.photo = []
    msg.document = None
    msg.message_id = 1
    msg.chat = SimpleNamespace(id=chat_id)

    update = MagicMock()
    update.effective_chat = SimpleNamespace(id=chat_id)
    update.message = msg
    return update


def _make_context(handler=None) -> MagicMock:
    ctx = MagicMock()
    ctx.bot_data = {"vnext_handler": handler} if handler is not None else {}
    return ctx


def _snapshot_with_action(*action_kinds: ActionKind, **payloads) -> EngineSnapshot:
    ws = new_workspace()
    actions = tuple(
        NextAction(kind=k, payload=payloads.get(k.value, {})) for k in action_kinds
    )
    return EngineSnapshot(workspace=ws, actions=actions)


@pytest.fixture(autouse=True)
def _clear_workspaces():
    vnext_runner._workspaces.clear()
    yield
    vnext_runner._workspaces.clear()


# ---------------------------------------------------------------------------
# Workspace management
# ---------------------------------------------------------------------------


def test_get_workspace_creates_fresh_for_unknown_chat():
    ws = vnext_runner._get_workspace(99)
    assert ws.state is CaseState.IDLE
    assert ws.facts == ()


def test_get_workspace_returns_same_object_for_same_chat():
    ws1 = vnext_runner._get_workspace(5)
    ws2 = vnext_runner._get_workspace(5)
    assert ws1 is ws2


def test_reset_workspace_replaces_existing():
    ws1 = vnext_runner._get_workspace(7)
    ws2 = vnext_runner._reset_workspace(7)
    assert ws1 is not ws2
    assert ws2.state is CaseState.IDLE
    assert vnext_runner._workspaces[7] is ws2


# ---------------------------------------------------------------------------
# /start command
# ---------------------------------------------------------------------------


async def test_start_command_sends_intro():
    update = _make_update(chat_id=10)
    ctx = _make_context()
    await vnext_runner.start_command(update, ctx)
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.call_args[0][0]
    assert "vNext" in text
    assert "Kaizen filing is not wired" in text


async def test_start_command_resets_workspace():
    # Prime workspace with something non-idle
    vnext_runner._workspaces[10] = new_workspace()
    update = _make_update(chat_id=10)
    await vnext_runner.start_command(update, _make_context())
    # After /start, the workspace should be fresh
    assert vnext_runner._workspaces[10].state is CaseState.IDLE


# ---------------------------------------------------------------------------
# /reset command
# ---------------------------------------------------------------------------


async def test_reset_command_sends_confirmation():
    update = _make_update(chat_id=20)
    await vnext_runner.reset_command(update, _make_context())
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.call_args[0][0]
    assert "cleared" in text.lower()


async def test_reset_command_creates_fresh_workspace():
    old_ws = vnext_runner._get_workspace(20)
    update = _make_update(chat_id=20)
    await vnext_runner.reset_command(update, _make_context())
    assert vnext_runner._workspaces[20] is not old_ws


# ---------------------------------------------------------------------------
# handle_message — no handler set
# ---------------------------------------------------------------------------


async def test_handle_message_without_handler_sends_error():
    update = _make_update(chat_id=30)
    ctx = _make_context(handler=None)
    await vnext_runner.handle_message(update, ctx)
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.call_args[0][0]
    assert "handler" in text.lower()


# ---------------------------------------------------------------------------
# handle_message — routes through engine
# ---------------------------------------------------------------------------


async def test_handle_message_routes_text_through_engine():
    """A text case description should update the workspace and reply."""
    # Use a sentence the router reliably classifies as new_case (not UNKNOWN/SIDE_QUESTION)
    update = _make_update(
        chat_id=50,
        message_text="62 year old man, chest pain in ED, I performed RSI under supervision",
    )

    # Build a real handler using the env guard
    with patch.dict(
        "os.environ",
        {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}},
        clear=False,
    ):
        import conversational_vnext_bot

        handler = conversational_vnext_bot.build_handler(
            {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}}
        )
    assert handler is not None

    ctx = _make_context(handler=handler)
    await vnext_runner.handle_message(update, ctx)
    update.message.reply_text.assert_awaited_once()
    # Workspace should now be non-idle
    ws = vnext_runner._workspaces[50]
    assert ws.state is not CaseState.IDLE


async def test_handle_message_with_voice_acks_without_download():
    """A voice message should be acknowledged; no transcription attempted."""
    update = _make_update(chat_id=60, message_text=None)
    update.message.voice = MagicMock()  # non-None voice attribute
    update.message.audio = None
    update.message.text = None

    with patch.dict(
        "os.environ",
        {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}},
        clear=False,
    ):
        import conversational_vnext_bot

        handler = conversational_vnext_bot.build_handler(
            {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}}
        )
    assert handler is not None

    ctx = _make_context(handler=handler)
    await vnext_runner.handle_message(update, ctx)
    # Bot must reply (ack) but the workspace should not have draft-eligible facts
    update.message.reply_text.assert_awaited_once()
    ws = vnext_runner._workspaces[60]
    assert ws.draft_eligible_facts() == ()


async def test_handle_message_with_photo_acks_without_extraction():
    """A photo message should be acknowledged; no vision call made."""
    update = _make_update(chat_id=70, message_text=None)
    update.message.text = None
    update.message.photo = [MagicMock()]  # non-empty photo list
    update.message.voice = None
    update.message.audio = None
    update.message.document = None

    with patch.dict(
        "os.environ",
        {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}},
        clear=False,
    ):
        import conversational_vnext_bot

        handler = conversational_vnext_bot.build_handler(
            {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}}
        )
    assert handler is not None

    ctx = _make_context(handler=handler)
    await vnext_runner.handle_message(update, ctx)
    update.message.reply_text.assert_awaited_once()
    ws = vnext_runner._workspaces[70]
    # Photo facts are unconfirmed stricter source — not draft-eligible
    assert ws.draft_eligible_facts() == ()


# ---------------------------------------------------------------------------
# _build_reply — one test per significant ActionKind
# ---------------------------------------------------------------------------


def test_build_reply_save_draft_explains_not_wired():
    snap = _snapshot_with_action(ActionKind.SAVE_DRAFT)
    text = vnext_runner._build_reply(snap)
    assert "Kaizen filing is not wired" in text
    assert "vNext" in text or "test bot" in text.lower()


def test_build_reply_request_case_confirmation_shows_state():
    snap = _snapshot_with_action(ActionKind.REQUEST_CASE_CONFIRMATION)
    text = vnext_runner._build_reply(snap)
    assert "Captured" in text
    assert "Done" in text
    assert "state" not in text


def test_build_reply_ack_case_details_shows_state():
    snap = _snapshot_with_action(ActionKind.ACK_CASE_DETAILS)
    text = vnext_runner._build_reply(snap)
    assert "Captured" in text
    assert "Done" in text
    assert "state" not in text


def test_build_reply_offer_draft_shows_dogfood_note():
    snap = _snapshot_with_action(ActionKind.OFFER_DRAFT)
    text = vnext_runner._build_reply(snap, completion_requested=True)
    assert "Draft ready" in text
    assert "dogfood" in text.lower()


def test_build_reply_draft_not_ready_shows_reason():
    ws = new_workspace()
    actions = (
        NextAction(
            kind=ActionKind.DRAFT_NOT_READY, payload={"reason": "no_source_backed_facts"}
        ),
    )
    snap = EngineSnapshot(workspace=ws, actions=actions)
    text = vnext_runner._build_reply(snap)
    assert "not enough" in text.lower()
    assert "no_source_backed_facts" not in text


def test_build_reply_answer_chat_prompts_case():
    snap = _snapshot_with_action(ActionKind.ANSWER_CHAT)
    text = vnext_runner._build_reply(snap, message_text="blurple")
    assert "case" in text.lower()
    assert "state" not in text


def test_build_reply_answer_chat_handles_greeting():
    snap = _snapshot_with_action(ActionKind.ANSWER_CHAT)
    text = vnext_runner._build_reply(snap, message_text="hello there")

    assert "Hi" in text
    assert "say 'done'" not in text


def test_build_reply_answer_chat_handles_features_question():
    snap = _snapshot_with_action(ActionKind.ANSWER_CHAT)
    text = vnext_runner._build_reply(snap, message_text="what are your features")

    assert "collect a case over multiple messages" in text
    assert "Kaizen filing is deliberately not connected" in text
    assert "say 'done'" not in text


def test_build_reply_abandon_case():
    snap = _snapshot_with_action(ActionKind.ABANDON_CASE)
    text = vnext_runner._build_reply(snap)
    assert "abandoned" in text.lower()


def test_build_reply_request_fact_confirmation_mentions_strict():
    snap = _snapshot_with_action(ActionKind.REQUEST_FACT_CONFIRMATION)
    text = vnext_runner._build_reply(snap)
    assert "strict" in text.lower() or "confirmation" in text.lower()


def test_build_reply_noop_returns_state_fallback():
    snap = _snapshot_with_action(ActionKind.NOOP)
    text = vnext_runner._build_reply(snap)
    assert "I'm listening" in text


def test_build_reply_empty_actions_returns_fallback():
    ws = new_workspace()
    snap = EngineSnapshot(workspace=ws, actions=())
    text = vnext_runner._build_reply(snap)
    assert "I'm listening" in text


async def test_rich_case_collects_first_and_previews_only_when_done():
    """Dogfood complaint: vNext should collect conversationally, not dump a preview immediately."""
    with patch.dict(
        "os.environ",
        {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}},
        clear=False,
    ):
        import conversational_vnext_bot

        handler = conversational_vnext_bot.build_handler(
            {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}}
        )
    assert handler is not None

    case_update = _make_update(
        chat_id=88,
        message_text=(
            "62M chest pain in ED, STEMI on ECG, cath lab activated, "
            "consultant supervised, learned to escalate early"
        ),
    )
    ctx = _make_context(handler=handler)

    await vnext_runner.handle_message(case_update, ctx)

    first_reply = case_update.message.reply_text.call_args[0][0]
    assert "Draft ready" not in first_reply
    assert "Done" in first_reply
    assert "state" not in first_reply

    done_update = _make_update(chat_id=88, message_text="done")
    await vnext_runner.handle_message(done_update, ctx)

    done_reply = done_update.message.reply_text.call_args[0][0]
    assert "Draft ready" in done_reply
    assert "Recommended form: CBD" in done_reply
    assert "not a Kaizen draft" in done_reply


async def test_collected_case_does_not_duplicate_done_instruction():
    with patch.dict(
        "os.environ",
        {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}},
        clear=False,
    ):
        import conversational_vnext_bot

        handler = conversational_vnext_bot.build_handler(
            {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}}
        )
    assert handler is not None

    ctx = _make_context(handler=handler)
    update = _make_update(
        chat_id=90,
        message_text=(
            "62M chest pain in ED, STEMI on ECG, cath lab activated, "
            "consultant supervised, learned to escalate early"
        ),
    )

    await vnext_runner.handle_message(update, ctx)

    reply = update.message.reply_text.call_args[0][0]
    assert reply.count("Done") == 1


async def test_file_request_after_collected_case_shows_preview_not_filing():
    with patch.dict(
        "os.environ",
        {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}},
        clear=False,
    ):
        import conversational_vnext_bot

        handler = conversational_vnext_bot.build_handler(
            {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}}
        )
    assert handler is not None

    ctx = _make_context(handler=handler)
    await vnext_runner.handle_message(
        _make_update(
            chat_id=89,
            message_text="62M chest pain in ED, STEMI on ECG, consultant supervised",
        ),
        ctx,
    )

    file_update = _make_update(chat_id=89, message_text="file this")
    await vnext_runner.handle_message(file_update, ctx)

    reply = file_update.message.reply_text.call_args[0][0]
    assert "Draft ready" in reply
    assert "Kaizen filing not wired" in reply
    assert "Private vNext test bot: Kaizen filing is not wired" not in reply


async def test_side_chat_does_not_get_case_collection_prompt_or_pollute_workspace():
    with patch.dict(
        "os.environ",
        {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}},
        clear=False,
    ):
        import conversational_vnext_bot

        handler = conversational_vnext_bot.build_handler(
            {VNEXT_TOKEN_ENV: "vnext-only", **{k: "" for k in PRODUCTION_TOKEN_ENVS}}
        )
    assert handler is not None

    ctx = _make_context(handler=handler)
    for text in ("hello there", "how are you", "what are your features"):
        update = _make_update(chat_id=91, message_text=text)
        await vnext_runner.handle_message(update, ctx)
        reply = update.message.reply_text.call_args[0][0]
        assert "Got it. I have" not in reply
        assert "say 'done'" not in reply

    assert vnext_runner._workspaces[91].draft_eligible_facts() == ()


# ---------------------------------------------------------------------------
# main() safety guards
# ---------------------------------------------------------------------------


def test_main_exits_nonzero_when_disabled(monkeypatch):
    for env_name in (VNEXT_TOKEN_ENV, *PRODUCTION_TOKEN_ENVS):
        monkeypatch.delenv(env_name, raising=False)
    assert vnext_runner.main() != 0


def test_main_refuses_when_token_collides_with_production(monkeypatch, capsys):
    monkeypatch.setenv(VNEXT_TOKEN_ENV, "shared")
    monkeypatch.setenv("BOT_TOKEN", "shared")
    result = vnext_runner.main()
    assert result == 2
    assert "refused" in capsys.readouterr().err.lower()


def test_main_calls_run_when_enabled_with_distinct_token(monkeypatch):
    monkeypatch.setenv(VNEXT_TOKEN_ENV, "vnext-only")
    for prod_env in PRODUCTION_TOKEN_ENVS:
        monkeypatch.delenv(prod_env, raising=False)

    with patch.object(vnext_runner, "run") as mock_run:
        result = vnext_runner.main()

    assert result == 0
    mock_run.assert_called_once()
    token_arg = mock_run.call_args[0][0]
    assert token_arg == "vnext-only"
