"""Offline E2E tests — exercises the real PTB handler stack via process_update().

Uses OfflineRequest to block any accidental network calls. All Gemini/store
interactions are monkeypatched. Tests run without API keys or tokens.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Message, User
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ConversationHandler, MessageHandler, filters

from tests.helpers import (
    BOT_USER,
    TEST_CHAT,
    TEST_USER,
    OfflineRequest,
    make_callback_update,
    make_command_update,
    make_text_update,
)

# ---------------------------------------------------------------------------
# Collected bot responses
# ---------------------------------------------------------------------------

class ResponseCollector:
    """Collects messages sent by the bot during process_update()."""

    def __init__(self):
        self.sent: list[dict] = []

    def _make_fake_message(self, chat_id, text, **kwargs):
        msg = MagicMock(spec=Message)
        msg.message_id = len(self.sent) + 5000
        msg.chat_id = chat_id
        msg.text = text
        return msg

    async def fake_send_message(self, chat_id=None, text="", **kwargs):
        record = {"method": "send_message", "chat_id": chat_id, "text": text, **kwargs}
        self.sent.append(record)
        return self._make_fake_message(chat_id, text)

    async def fake_edit_message_text(self, text="", chat_id=None, message_id=None, **kwargs):
        record = {"method": "edit_message_text", "chat_id": chat_id, "text": text, "message_id": message_id, **kwargs}
        self.sent.append(record)
        return True

    async def fake_answer_callback_query(self, callback_query_id=None, **kwargs):
        return True

    async def fake_delete_message(self, chat_id=None, message_id=None, **kwargs):
        return True

    @property
    def texts(self) -> list[str]:
        return [r["text"] for r in self.sent if r.get("text")]


# ---------------------------------------------------------------------------
# Offline Application fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def offline_app(monkeypatch, tmp_path):
    """Build a real PTB Application with OfflineRequest and all handlers registered."""
    import bot

    collector = ResponseCollector()

    # Build application with offline request — no network, no persistence
    app = (
        Application.builder()
        .token("0:FAKE")
        .updater(None)
        .request(OfflineRequest())
        .get_updates_request(OfflineRequest())
        .build()
    )

    # Patch bot internals so initialize() doesn't hit the network
    real_bot = app.bot
    real_bot._unfreeze()
    real_bot._bot_user = BOT_USER
    real_bot._bot_initialized = True
    real_bot._requests_initialized = True

    # Patch bot methods at the class level (instances are frozen / slotted)
    bot_cls = type(real_bot)
    patches = contextlib.ExitStack()

    async def _fake_send(self_bot, chat_id=None, text="", **kwargs):
        return await collector.fake_send_message(chat_id=chat_id, text=text, **kwargs)

    async def _fake_edit(self_bot, text="", chat_id=None, message_id=None, **kwargs):
        return await collector.fake_edit_message_text(text=text, chat_id=chat_id, message_id=message_id, **kwargs)

    async def _fake_answer_cq(self_bot, callback_query_id=None, **kwargs):
        return True

    async def _fake_delete(self_bot, chat_id=None, message_id=None, **kwargs):
        return True

    async def _fake_get_me(self_bot, **kwargs):
        return BOT_USER

    async def _fake_send_action(self_bot, chat_id=None, action=None, **kwargs):
        return True

    patches.enter_context(patch.object(bot_cls, "send_message", _fake_send))
    patches.enter_context(patch.object(bot_cls, "edit_message_text", _fake_edit))
    patches.enter_context(patch.object(bot_cls, "answer_callback_query", _fake_answer_cq))
    patches.enter_context(patch.object(bot_cls, "delete_message", _fake_delete))
    patches.enter_context(patch.object(bot_cls, "get_me", _fake_get_me))
    patches.enter_context(patch.object(bot_cls, "delete_webhook", AsyncMock(return_value=True)))
    patches.enter_context(patch.object(bot_cls, "send_chat_action", _fake_send_action))
    patches.enter_context(patch.object(bot_cls, "edit_message_reply_markup", AsyncMock(return_value=True)))

    # ---- Register all handlers (mirrors build_application in bot.py) ----
    case_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(bot.handle_callback, pattern=r"^ACTION\|(?:file|reset|cancel|add_detail|continue_thin)$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_case_input),
            MessageHandler(filters.VOICE, bot.handle_case_input),
            MessageHandler(filters.PHOTO, bot.handle_case_input),
            MessageHandler(filters.Document.ALL, bot.handle_case_input),
        ],
        states={
            bot.AWAIT_CASE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_case_input),
                MessageHandler(filters.VOICE, bot.handle_case_input),
                MessageHandler(filters.PHOTO, bot.handle_case_input),
                MessageHandler(filters.Document.ALL, bot.handle_case_input),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^ACTION\|add_detail$"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^ACTION\|continue_thin$"),
            ],
            bot.AWAIT_FORM_CHOICE: [
                CallbackQueryHandler(bot.handle_form_choice, pattern=r"^FORM\|"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^CANCEL\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_mid_conversation_text),
            ],
            bot.AWAIT_TEMPLATE_REVIEW: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_template_review_text),
                MessageHandler(filters.VOICE, bot.handle_case_input),
                MessageHandler(filters.PHOTO, bot.handle_case_input),
                MessageHandler(filters.Document.ALL, bot.handle_case_input),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^CASE\|"),
                CallbackQueryHandler(bot.handle_form_choice, pattern=r"^FORM\|"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^ACTION\|add_detail$"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^ACTION\|continue_thin$"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^CANCEL\|"),
            ],
            bot.AWAIT_APPROVAL: [
                CallbackQueryHandler(bot.handle_approval_approve, pattern=r"^APPROVE\|"),
                CallbackQueryHandler(bot.handle_approval_edit, pattern=r"^EDIT\|"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^CANCEL\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_mid_conversation_text),
            ],
            bot.AWAIT_EDIT_FIELD: [
                CallbackQueryHandler(bot.handle_edit_field, pattern=r"^FIELD\|"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^CANCEL\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_mid_conversation_text),
            ],
            bot.AWAIT_EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_edit_value_with_intent),
                MessageHandler(~filters.COMMAND & ~filters.TEXT, bot.handle_edit_value),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^CANCEL\|"),
            ],
        },
        fallbacks=[
            CommandHandler("start", bot.start),
            CommandHandler("help", bot.help_command),
            CommandHandler("status", bot.status),
            CommandHandler("reset", bot.reset),
            CommandHandler("cancel", bot.setup_cancel),
            CallbackQueryHandler(
                bot.handle_callback,
                pattern=r"^(?:INFO\|.*|CANCEL\|.*|ACTION\|(?:file|setup|reset|cancel|add_detail|continue_thin|retry_filing))$",
            ),
        ],
        per_message=False,
        allow_reentry=False,
    )

    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", bot.setup_start)],
        states={
            bot.AWAIT_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.setup_username)],
            bot.AWAIT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, bot.setup_password)],
            bot.AWAIT_TRAINING_LEVEL: [CallbackQueryHandler(bot.setup_training_level, pattern=r"^LEVEL\|")],
            bot.AWAIT_CURRICULUM: [CallbackQueryHandler(bot.setup_curriculum, pattern=r"^SET_CURRICULUM\|")],
        },
        fallbacks=[CommandHandler("cancel", bot.setup_cancel)],
        allow_reentry=True,
    )

    voice_conv = ConversationHandler(
        entry_points=[CommandHandler("voice", bot.voice_start)],
        states={
            bot.AWAIT_VOICE_EXAMPLES: [
                CallbackQueryHandler(bot.voice_collect_example, pattern=r"^VOICE\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.voice_collect_example),
                MessageHandler(filters.PHOTO, bot.voice_collect_example),
                MessageHandler(filters.VOICE, bot.voice_collect_example),
            ],
        },
        fallbacks=[CommandHandler("cancel", bot.setup_cancel)],
        allow_reentry=True,
    )

    # Top-level command handlers
    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CommandHandler("status", bot.status))
    app.add_handler(CommandHandler("reset", bot.reset))
    app.add_handler(CommandHandler("cancel", bot.cancel_command))
    app.add_handler(CommandHandler("delete", bot.delete_data))
    app.add_handler(CommandHandler("help", bot.help_command))
    app.add_handler(CommandHandler("bulk", bot.bulk_command))
    app.add_handler(CommandHandler("unsigned", bot.unsigned_command))
    app.add_handler(CommandHandler("chase", bot.chase_command))
    app.add_handler(CommandHandler("curriculum", bot.curriculum_command))
    app.add_handler(CallbackQueryHandler(bot.handle_set_curriculum, pattern=r"^SET_CURRICULUM\|"))
    app.add_handler(CallbackQueryHandler(bot.handle_chase_log, pattern=r"^CHASE_LOG\|"))
    app.add_handler(CallbackQueryHandler(bot.handle_info_button, pattern=r"^INFO\|"))
    app.add_handler(
        CallbackQueryHandler(
            bot.handle_action_button,
            pattern=r"^ACTION\|(?!file$|reset$|cancel$|add_detail$|continue_thin$|retry_filing$).+",
        )
    )
    app.add_handler(CallbackQueryHandler(bot.handle_feedback, pattern=r"^FEEDBACK\|"))
    app.add_handler(CallbackQueryHandler(bot.handle_filing_feedback, pattern=r"^FILING\|feedback\|"))
    app.add_handler(CallbackQueryHandler(bot.handle_pushback, pattern=r"^PUSHBACK\|"))

    # Conversation handlers (order matters)
    app.add_handler(setup_conv)
    app.add_handler(voice_conv)
    app.add_handler(case_conv)

    await app.initialize()

    yield app, collector

    await app.shutdown()
    patches.close()


# ---------------------------------------------------------------------------
# Helper to set bot on Update objects so PTB routing works
# ---------------------------------------------------------------------------

def _prepare_update(update, bot):
    """Set the bot reference on all nested objects so PTB can route properly."""
    update.set_bot(bot)
    if update.message:
        update.message.set_bot(bot)
        if update.message.chat:
            update.message.chat.set_bot(bot)
        if update.message.from_user:
            update.message.from_user.set_bot(bot)
    if update.callback_query:
        update.callback_query.set_bot(bot)
        if update.callback_query.message:
            update.callback_query.message.set_bot(bot)
            if update.callback_query.message.chat:
                update.callback_query.message.chat.set_bot(bot)
        if update.callback_query.from_user:
            update.callback_query.from_user.set_bot(bot)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@pytest.mark.asyncio
class TestOfflineE2E:

    async def test_start_no_credentials_shows_setup(self, offline_app, monkeypatch):
        """Send /start to a user with no stored credentials → bot sends Connect Kaizen button."""
        app, collector = offline_app
        monkeypatch.setattr("bot.has_credentials", lambda uid: False)
        monkeypatch.setattr("bot.get_training_level", lambda uid: None)

        update = make_command_update("start")
        _prepare_update(update, app.bot)
        await app.process_update(update)

        assert len(collector.texts) >= 1
        text = collector.texts[0]
        assert "Portfolio Guru" in text
        # Should have a Connect Kaizen button (via reply_markup)
        sent = collector.sent[0]
        markup = sent.get("reply_markup")
        assert markup is not None
        # Check inline keyboard contains setup button
        buttons = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "ACTION|setup" in buttons

    async def test_start_with_credentials_shows_file(self, offline_app, monkeypatch):
        """Send /start to a connected user → welcome guides them to send a case directly.
        The keyboard surfaces secondary destinations (Status, Help, Settings, Health)."""
        app, collector = offline_app
        monkeypatch.setattr("bot.has_credentials", lambda uid: True)
        monkeypatch.setattr("bot.get_training_level", lambda uid: "ST5")
        monkeypatch.setattr("bot.get_curriculum", lambda uid: "2025")

        update = make_command_update("start")
        _prepare_update(update, app.bot)
        await app.process_update(update)

        assert len(collector.texts) >= 1
        text = collector.texts[0]
        assert "ready when you are" in text
        sent = collector.sent[0]
        markup = sent.get("reply_markup")
        assert markup is not None
        buttons = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        # Filing is initiated by sending the case; no re-prompt button needed.
        assert "ACTION|file" not in buttons
        # But the user still has direct access to status/help/settings.
        assert "ACTION|status" in buttons
        assert "ACTION|settings" in buttons

    async def test_case_text_reaches_form_choice(self, offline_app, monkeypatch):
        """Send clinical case text → bot processes through extraction → shows form buttons."""
        app, collector = offline_app
        monkeypatch.setattr("bot.has_credentials", lambda uid: True)
        monkeypatch.setattr("bot.get_training_level", lambda uid: "ST5")
        monkeypatch.setattr("bot.get_curriculum", lambda uid: "2025")
        monkeypatch.setattr("bot.get_voice_profile", lambda uid: None)

        from models import FormTypeRecommendation
        from kaizen_form_filer import FORM_UUIDS

        mock_recs = [
            FormTypeRecommendation(form_type="CBD", rationale="Reflective case", uuid=FORM_UUIDS.get("CBD")),
            FormTypeRecommendation(form_type="ACAT", rationale="Acute take", uuid=FORM_UUIDS.get("ACAT")),
        ]

        async def fake_recommend(*args, **kwargs):
            return mock_recs

        monkeypatch.setattr("bot.recommend_form_types", fake_recommend)
        monkeypatch.setattr("bot.classify_intent", AsyncMock(return_value="case"))
        monkeypatch.setattr("bot.extract_explicit_form_type", lambda text: None)

        update = make_text_update("45M chest pain, troponin positive, managed ACS, reflected on escalation")
        _prepare_update(update, app.bot)
        await app.process_update(update)

        # Bot should have sent a message with form recommendation buttons
        assert len(collector.sent) >= 1
        all_text = " ".join(collector.texts)
        # Should mention recommended forms
        assert "CBD" in all_text or "FORM|" in str(collector.sent)
        # Check for FORM| buttons in any sent message
        form_buttons = []
        for msg in collector.sent:
            markup = msg.get("reply_markup")
            if markup and hasattr(markup, "inline_keyboard"):
                for row in markup.inline_keyboard:
                    for btn in row:
                        if btn.callback_data and btn.callback_data.startswith("FORM|"):
                            form_buttons.append(btn.callback_data)
        assert len(form_buttons) >= 1, f"Expected FORM| buttons, got: {collector.sent}"

    async def test_callback_form_selection_shows_draft(self, offline_app, monkeypatch):
        """User taps a form button (FORM|CBD) → bot shows draft preview."""
        app, collector = offline_app
        monkeypatch.setattr("bot.has_credentials", lambda uid: True)
        monkeypatch.setattr("bot.get_training_level", lambda uid: "ST5")
        monkeypatch.setattr("bot.get_curriculum", lambda uid: "2025")
        monkeypatch.setattr("bot.get_voice_profile", lambda uid: None)

        from models import FormDraft, FormTypeRecommendation
        from kaizen_form_filer import FORM_UUIDS

        mock_recs = [
            FormTypeRecommendation(form_type="CBD", rationale="Reflective case", uuid=FORM_UUIDS.get("CBD")),
        ]

        async def fake_recommend(*args, **kwargs):
            return mock_recs

        mock_draft = FormDraft(
            form_type="CBD",
            uuid=FORM_UUIDS.get("CBD"),
            fields={
                "date_of_encounter": "17/3/2026",
                "clinical_setting": "ED",
                "patient_presentation": "Chest pain",
                "clinical_reasoning": "Managed as ACS",
                "reflection": "Need faster ECG review",
                "curriculum_links": ["SLO1"],
                "key_capabilities": ["SLO1 KC1: Assess and stabilise"],
            },
        )

        async def fake_extract(*args, **kwargs):
            return mock_draft

        monkeypatch.setattr("bot.recommend_form_types", fake_recommend)
        monkeypatch.setattr("bot.classify_intent", AsyncMock(return_value="case"))
        monkeypatch.setattr("bot.extract_explicit_form_type", lambda text: None)
        monkeypatch.setattr("bot.extract_form_data", fake_extract)
        monkeypatch.setattr("bot.extract_cbd_data", fake_extract)

        # Step 1: send case text to enter the conversation
        update1 = make_text_update("45M chest pain, troponin positive, managed ACS")
        _prepare_update(update1, app.bot)
        await app.process_update(update1)

        # Step 2: tap FORM|CBD button
        collector.sent.clear()
        update2 = make_callback_update("FORM|CBD")
        _prepare_update(update2, app.bot)
        await app.process_update(update2)

        # Should show a draft with approval buttons or template review
        assert len(collector.sent) >= 1

    async def test_callback_approve_triggers_filing(self, offline_app, monkeypatch):
        """User taps approve after template review → bot files draft."""
        app, collector = offline_app
        monkeypatch.setattr("bot.has_credentials", lambda uid: True)
        monkeypatch.setattr("bot.get_training_level", lambda uid: "ST5")
        monkeypatch.setattr("bot.get_curriculum", lambda uid: "2025")
        monkeypatch.setattr("bot.get_voice_profile", lambda uid: None)
        monkeypatch.setattr("bot.get_credentials", lambda uid: ("test@test.com", "pass"))

        from models import FormDraft, CBDData, FormTypeRecommendation
        from kaizen_form_filer import FORM_UUIDS

        mock_recs = [
            FormTypeRecommendation(form_type="CBD", rationale="Reflective case", uuid=FORM_UUIDS.get("CBD")),
        ]

        cbd_draft = CBDData(
            date_of_encounter="17/3/2026",
            clinical_setting="ED",
            patient_presentation="Chest pain, troponin positive",
            clinical_reasoning="Managed as ACS with dual antiplatelet",
            reflection="Need faster ECG review and escalation",
            curriculum_links=["SLO1"],
            key_capabilities=["SLO1 KC1: Assess and stabilise"],
        )

        form_draft = FormDraft(
            form_type="CBD",
            uuid=FORM_UUIDS.get("CBD"),
            fields={
                "date_of_encounter": "17/3/2026",
                "clinical_setting": "ED",
                "patient_presentation": "Chest pain",
                "clinical_reasoning": "Managed as ACS",
                "reflection": "Need faster ECG review",
                "curriculum_links": ["SLO1"],
                "key_capabilities": ["SLO1 KC1: Assess and stabilise"],
            },
        )

        async def fake_recommend(*args, **kwargs):
            return mock_recs

        async def fake_extract_cbd(*args, **kwargs):
            return cbd_draft

        async def fake_extract_form(*args, **kwargs):
            return form_draft

        async def fake_route_filing(*args, **kwargs):
            return {"status": "draft_saved", "message": "Draft saved"}

        monkeypatch.setattr("bot.recommend_form_types", fake_recommend)
        monkeypatch.setattr("bot.classify_intent", AsyncMock(return_value="case"))
        monkeypatch.setattr("bot.extract_explicit_form_type", lambda text: None)
        monkeypatch.setattr("bot.extract_form_data", fake_extract_form)
        monkeypatch.setattr("bot.extract_cbd_data", fake_extract_cbd)
        monkeypatch.setattr("bot.route_filing", fake_route_filing)

        # Step 1: send case text → enters AWAIT_FORM_CHOICE
        update1 = make_text_update("45M chest pain, troponin positive, managed ACS")
        _prepare_update(update1, app.bot)
        await app.process_update(update1)

        # Step 2: tap FORM|CBD → enters AWAIT_TEMPLATE_REVIEW
        collector.sent.clear()
        update2 = make_callback_update("FORM|CBD")
        _prepare_update(update2, app.bot)
        await app.process_update(update2)

        # Step 3: tap ACTION|continue_thin to accept template → enters AWAIT_APPROVAL
        collector.sent.clear()
        update3 = make_callback_update("ACTION|continue_thin")
        _prepare_update(update3, app.bot)
        await app.process_update(update3)

        # Step 4: tap APPROVE|draft → triggers filing
        collector.sent.clear()
        update4 = make_callback_update("APPROVE|draft")
        _prepare_update(update4, app.bot)
        await app.process_update(update4)

        # Bot should have responded with filing result
        assert len(collector.sent) >= 1

    async def test_gibberish_input_handled(self, offline_app, monkeypatch):
        """Send random text → bot responds gracefully, doesn't crash."""
        app, collector = offline_app
        monkeypatch.setattr("bot.has_credentials", lambda uid: True)
        monkeypatch.setattr("bot.get_training_level", lambda uid: "ST5")
        monkeypatch.setattr("bot.get_curriculum", lambda uid: "2025")
        monkeypatch.setattr("bot.get_voice_profile", lambda uid: None)

        # classify_intent returns "unclear" for gibberish
        monkeypatch.setattr("bot.classify_intent", AsyncMock(return_value="unclear"))
        monkeypatch.setattr("bot.extract_explicit_form_type", lambda text: None)
        monkeypatch.setattr("bot.answer_question", AsyncMock(return_value="I'm not sure what you mean. Try describing a clinical case."))

        update = make_text_update("asdfghjkl random weather bananas")
        _prepare_update(update, app.bot)
        await app.process_update(update)

        # Should get a response (not crash)
        assert len(collector.sent) >= 1

    async def test_cancel_resets_conversation(self, offline_app, monkeypatch):
        """User sends /cancel → conversation resets cleanly."""
        app, collector = offline_app
        monkeypatch.setattr("bot.has_credentials", lambda uid: True)
        monkeypatch.setattr("bot.get_training_level", lambda uid: "ST5")
        monkeypatch.setattr("bot.get_curriculum", lambda uid: "2025")

        update = make_command_update("cancel")
        _prepare_update(update, app.bot)
        await app.process_update(update)

        assert len(collector.texts) >= 1
        text = collector.texts[0]
        assert "Cancel" in text or "cancel" in text or "❌" in text

    async def test_setup_flow_stores_credentials(self, offline_app, monkeypatch):
        """Walk through full setup flow → credentials stored."""
        app, collector = offline_app
        monkeypatch.setattr("bot.has_credentials", lambda uid: False)
        monkeypatch.setattr("bot.get_training_level", lambda uid: None)

        stored = {}

        def fake_store_credentials(uid, username, password):
            stored["uid"] = uid
            stored["username"] = username
            stored["password"] = password

        monkeypatch.setattr("bot.store_credentials", fake_store_credentials)
        monkeypatch.setattr("bot.store_training_level", lambda uid, level: None)
        monkeypatch.setattr("bot.store_curriculum", lambda uid, cur: None)

        # Mock credential validation — always passes in tests
        async def fake_login_test(u, p):
            return True
        monkeypatch.setattr("bot._test_kaizen_login", fake_login_test)

        # Step 1: /setup
        update1 = make_command_update("setup")
        _prepare_update(update1, app.bot)
        await app.process_update(update1)
        assert any("email" in t.lower() or "username" in t.lower() for t in collector.texts)

        # Step 2: send username
        collector.sent.clear()
        update2 = make_text_update("doctor@hospital.nhs.uk")
        _prepare_update(update2, app.bot)
        await app.process_update(update2)
        assert any("password" in t.lower() for t in collector.texts)

        # Step 3: send password
        collector.sent.clear()
        update3 = make_text_update("s3cret!")
        _prepare_update(update3, app.bot)
        await app.process_update(update3)

        # Credentials should have been stored
        assert stored.get("username") == "doctor@hospital.nhs.uk"
        assert stored.get("password") == "s3cret!"

        # Step 4: select training level
        collector.sent.clear()
        update4 = make_callback_update("LEVEL|ST5")
        _prepare_update(update4, app.bot)
        await app.process_update(update4)

        # Step 5: select curriculum
        collector.sent.clear()
        update5 = make_callback_update("SET_CURRICULUM|2025")
        _prepare_update(update5, app.bot)
        await app.process_update(update5)

    async def test_stale_button_handled_gracefully(self, offline_app, monkeypatch):
        """Tap a button from an old conversation → bot handles without crashing."""
        app, collector = offline_app
        monkeypatch.setattr("bot.has_credentials", lambda uid: True)
        monkeypatch.setattr("bot.get_training_level", lambda uid: "ST5")
        monkeypatch.setattr("bot.get_curriculum", lambda uid: "2025")

        # Tap a FORM| button when no conversation is active — should be handled gracefully
        update = make_callback_update("FORM|CBD")
        _prepare_update(update, app.bot)

        # Should not raise — the bot should handle stale buttons gracefully
        await app.process_update(update)
        # No crash = success. Bot may or may not send a message depending on state.
