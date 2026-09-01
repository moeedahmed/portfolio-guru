import pytest
import asyncio
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import AsyncMock, MagicMock, patch

class TestBotImport:
    """Smoke test — verify bot imports cleanly."""

    def test_bot_imports(self):
        """Bot must import without errors."""
        import bot
        assert bot is not None

    def test_conversation_states_defined(self):
        """All conversation states must be importable."""
        from bot import (AWAIT_CASE_INPUT, AWAIT_FORM_CHOICE, AWAIT_APPROVAL,
                         AWAIT_EDIT_FIELD, AWAIT_EDIT_VALUE, AWAIT_TRAINING_LEVEL)
        for state in [AWAIT_CASE_INPUT, AWAIT_FORM_CHOICE, AWAIT_APPROVAL,
                      AWAIT_EDIT_FIELD, AWAIT_EDIT_VALUE, AWAIT_TRAINING_LEVEL]:
            assert isinstance(state, int)

    def test_form_emojis_defined(self):
        """FORM_EMOJIS must exist and be a dict."""
        from bot import FORM_EMOJIS
        assert isinstance(FORM_EMOJIS, dict)
        assert len(FORM_EMOJIS) > 0

class TestKeyboardBuilding:
    """Verify keyboards are built with correct layout."""

    def test_form_choice_keyboard_two_per_row(self):
        """Recommendation keyboard must use 2-per-row layout."""
        from bot import _build_form_choice_keyboard
        from models import FormTypeRecommendation
        from extractor import FORM_UUIDS

        recs = [
            FormTypeRecommendation(form_type="CBD", rationale="test", uuid=FORM_UUIDS.get("CBD")),
            FormTypeRecommendation(form_type="REFLECT_LOG", rationale="test", uuid=FORM_UUIDS.get("REFLECT_LOG")),
            FormTypeRecommendation(form_type="LAT", rationale="test", uuid=FORM_UUIDS.get("LAT")),
            FormTypeRecommendation(form_type="ACAT", rationale="test", uuid=FORM_UUIDS.get("ACAT")),
        ]
        keyboard = _build_form_choice_keyboard(recs, curriculum="2025")
        rows = keyboard.inline_keyboard

        assert [(button.text, button.callback_data) for button in rows[0]] == [
            ('🩺 CBD', "FORM|best"),
            ('💭 Reflective log', "FORM|REFLECT_LOG"),
        ]
        assert [(button.text, button.callback_data) for button in rows[-1]] == [
            ('📋 Forms', "FORM|show_all"),
            ('🔄 Restart', "CANCEL|form"),
        ]
        assert all(len(row) <= 2 for row in rows)

    def test_single_strong_recommendation_keeps_manual_escape_hatch(self):
        from bot import _build_form_choice_keyboard
        from models import FormTypeRecommendation
        from extractor import FORM_UUIDS

        keyboard = _build_form_choice_keyboard([
            FormTypeRecommendation(
                form_type="QIAT",
                rationale="Run-chart quality improvement work.",
                uuid=FORM_UUIDS.get("QIAT"),
            )
        ])

        button_data = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]
        assert "FORM|best" in button_data
        assert "FORM|show_all" in button_data

    def test_best_fit_stays_first_when_an_earlier_recommendation_is_unavailable(self):
        from bot import _build_form_choice_keyboard
        from extractor import FORM_UUIDS
        from models import FormTypeRecommendation

        keyboard = _build_form_choice_keyboard([
            FormTypeRecommendation(
                form_type="TEACH_OBS",
                rationale="Unavailable candidate.",
                uuid=None,
            ),
            FormTypeRecommendation(
                form_type="CBD",
                rationale="Available best fit.",
                uuid=FORM_UUIDS["CBD"],
            ),
        ])

        assert (
            keyboard.inline_keyboard[0][0].text,
            keyboard.inline_keyboard[0][0].callback_data,
        ) == ('🩺 CBD', "FORM|best")

    def test_active_end_user_keyboards_never_exceed_two_buttons_per_row(self):
        from bot import (
            _build_amend_keyboard,
            _build_amend_new_case_choice_keyboard,
            _build_approval_keyboard,
            _build_attachment_confirm_keyboard,
            _build_consent_keyboard,
            _build_curriculum_keyboard,
            _build_doc_intent_keyboard,
            _build_edit_field_keyboard,
            _build_explicit_form_keyboard,
            _build_failed_filing_input_gate_keyboard,
            _build_image_intent_keyboard,
            _build_open_case_new_case_keyboard,
            _build_pathway_keyboard,
            _build_post_filing_keyboard,
            _build_template_review_keyboard,
            _build_video_intent_keyboard,
            _gathering_done_keyboard,
            _health_sync_recovery_keyboard,
            _health_view_keyboard,
            _refresh_portfolio_confirm_keyboard,
            _refresh_portfolio_result_keyboard,
            _voice_choice_keyboard,
            _voice_kaizen_sample_keyboard,
            _voice_post_activation_keyboard,
            _voice_rebuild_keyboard,
        )

        keyboards = [
            _gathering_done_keyboard(),
            _build_curriculum_keyboard(),
            _build_template_review_keyboard(),
            _build_explicit_form_keyboard("CBD"),
            _build_approval_keyboard(),
            _build_approval_keyboard(can_back_to_missing=True),
            _build_amend_keyboard(),
            _build_doc_intent_keyboard(),
            _build_image_intent_keyboard(),
            _build_video_intent_keyboard(),
            _build_amend_new_case_choice_keyboard(),
            _build_failed_filing_input_gate_keyboard(),
            _build_open_case_new_case_keyboard(),
            _build_edit_field_keyboard(),
            _build_attachment_confirm_keyboard(),
            _health_view_keyboard("priorities", needs_review_month=True),
            _health_view_keyboard("actions", page=1, page_count=4),
            _health_view_keyboard("coverage"),
            _health_view_keyboard("scan"),
            _health_sync_recovery_keyboard("failed"),
            _refresh_portfolio_confirm_keyboard(),
            _refresh_portfolio_result_keyboard("failed"),
            _build_pathway_keyboard(from_settings=True),
            _voice_choice_keyboard(),
            _voice_rebuild_keyboard(),
            _voice_kaizen_sample_keyboard(),
            _voice_post_activation_keyboard(),
            _build_consent_keyboard(123),
        ]
        keyboards.extend(
            _build_post_filing_keyboard("CBD", status, **kwargs)
            for status, kwargs in (
                ("success", {"same_case_available": True}),
                ("partial", {"uncertain": True}),
                ("failed", {}),
            )
        )

        for keyboard in keyboards:
            assert all(len(row) <= 2 for row in keyboard.inline_keyboard)


class TestMessagePolicy:
    def test_policy_has_no_raw_markdown_in_plain_templates(self):
        from message_policy import plain_text_policy_violations

        assert plain_text_policy_violations() == []

    def test_policy_has_no_decorative_emoji_in_templates(self):
        from message_policy import decorative_emoji_policy_violations

        assert decorative_emoji_policy_violations() == []

    def test_policy_classifies_fixed_templated_and_llm_assisted(self):
        from message_policy import MessageClass, message_audit_summary

        summary = message_audit_summary()

        assert summary[MessageClass.FIXED.value] >= 1
        assert summary[MessageClass.TEMPLATED.value] >= 1
        assert MessageClass.LLM_ASSISTED.value in summary

    def test_bot_profile_description_matches_trust_positioning(self):
        from message_policy import render_message

        profile = render_message("bot_profile_description")
        assert len(profile) <= 280
        assert "rough case notes" in profile
        assert "fills only supported details" in profile
        assert "Draft-only until approval" in profile
        assert "encrypted" in profile
        assert "files your medical WPBA entries in seconds" not in profile

    def test_style_grounded_answer_applies_house_emoji_standard(self):
        from message_policy import HOUSE_EMOJI, style_grounded_answer

        assert style_grounded_answer("It costs £9.") == f"{HOUSE_EMOJI} It costs £9."
        # Already-emoji answers and empties pass through unchanged.
        assert style_grounded_answer("📋 45 forms.") == "📋 45 forms."
        assert style_grounded_answer("✅ Yes, CBD is supported.") == "✅ Yes, CBD is supported."
        assert style_grounded_answer("   ") == ""

    def test_kaizen_setup_guide_is_short_structured_and_plain_text(self):
        from message_policy import render_message

        text = render_message("kaizen_setup_guide")

        assert text.startswith("🔗 Connect Kaizen")
        assert "1. Tap Connect Kaizen" in text
        assert "send /start" in text
        assert "/login" not in text
        assert "Safety notes:" in text
        assert "review and approve" in text
        assert "supervisor" in text
        assert "**" not in text
        assert "`" not in text

    def test_form_recommendation_template_is_mobile_first_and_privacy_safe(self):
        from bot import _build_form_recommendation_text
        from extractor import FORM_UUIDS
        from models import FormTypeRecommendation

        text = _build_form_recommendation_text(
            [
                FormTypeRecommendation(
                    form_type="PROC_LOG",
                    rationale="Procedure note: ultrasound-guided regional block for rib fractures.",
                    uuid=FORM_UUIDS["PROC_LOG"],
                )
            ],
            input_source="photo",
        )

        assert "Procedural Log" in text
        assert "Privacy check" in text
        assert "Best fit: Procedural Log" in text
        assert "Select a form to draft it." in text
        assert "*" not in text

    def test_draft_approval_keeps_kaizen_save_explicit(self):
        from bot import _build_approval_keyboard

        rows = _build_approval_keyboard().inline_keyboard

        assert [(button.text, button.callback_data) for button in rows[0]] == [
            ('💾 Save to Kaizen', "APPROVE|draft"),
        ]
        assert [(button.text, button.callback_data) for button in rows[1]] == [
            ('✏️ Improve reflection', "IMPROVE|reflection"),
            ('❌ Cancel', "CANCEL|draft"),
        ]

    def test_failed_filing_actions_are_compact_without_losing_choices(self):
        from bot import _build_failed_filing_input_gate_keyboard

        rows = _build_failed_filing_input_gate_keyboard().inline_keyboard

        assert all(len(row) <= 2 for row in rows)
        assert [
            (button.text, button.callback_data)
            for row in rows
            for button in row
        ] == [
            ('🔄 Retry', "ACTION|retry_filing"),
            ('✏️ Edit', "CASE|improve"),
            ('➕ New case', "CASE|new"),
            ('❌ Cancel', "ACTION|cancel"),
        ]

    def test_form_recommendation_copy_hides_default_curriculum_and_finishes_lines(self):
        from bot import _build_form_recommendation_text
        from extractor import FORM_UUIDS
        from models import FormTypeRecommendation

        text = _build_form_recommendation_text(
            [
                FormTypeRecommendation(
                    form_type="REFLECT_LOG",
                    rationale="Reflective case about fixation bias, escalation, learning, and how the trainee would change practice next time...",
                    uuid=FORM_UUIDS["REFLECT_LOG"],
                ),
                FormTypeRecommendation(
                    form_type="ESLE_ASSESS",
                    rationale="Could fit if this was observed across a shift...",
                    uuid=FORM_UUIDS["ESLE_ASSESS"],
                ),
            ],
            curriculum="2025",
        )

        assert "2025 Update" not in text
        assert "(2025)" not in text
        assert "..." not in text
        assert "…" not in text
        assert "Strongest match" in text
        assert "Reflective Practice Log:" in text

    def test_form_recommendation_copy_does_not_show_dangling_fragments(self):
        from bot import _build_form_recommendation_text
        from extractor import FORM_UUIDS
        from models import FormTypeRecommendation

        text = _build_form_recommendation_text(
            [
                FormTypeRecommendation(
                    form_type="CBD",
                    rationale="The trainee managed diagnostic uncertainty and assessment of a",
                    uuid=FORM_UUIDS["CBD"],
                ),
                FormTypeRecommendation(
                    form_type="LAT",
                    rationale="the",
                    uuid=FORM_UUIDS["LAT"],
                ),
                FormTypeRecommendation(
                    form_type="REFLECT_LOG",
                    rationale="The trainee reflected on escalation and documentation.",
                    uuid=FORM_UUIDS["REFLECT_LOG"],
                ),
            ]
        )

        assert "assessment of a." not in text
        assert "the." not in text
        assert "Fits the main portfolio evidence in this case." in text
        assert "The trainee reflected on escalation and documentation." in text

class TestExplicitFormRouting:
    QI_AUDIT_SCREENSHOT_TEXT = (
        "Please create the best-fit kaizen draft for an intermediate portfolio account.\n"
        "Quality improvement project in ED: improving time-to-antibiotics for "
        "adult sepsis alerts. Baseline audit showed delays from triage "
        "recognition to first antibiotic dose. Intervention included a sepsis "
        "prompt sticker on triage notes, short teaching for nurses and junior "
        "doctors, and a resus-room antibiotic grab-list. Re-audit after two "
        "weeks showed improved time to antibiotics and better documentation "
        "of lactate and blood cultures."
    )

    @pytest.mark.asyncio
    async def test_photo_recommendation_copy_is_plain_and_includes_privacy_nudge(self, monkeypatch):
        import bot
        from extractor import FORM_UUIDS
        from models import FormTypeRecommendation

        message = MagicMock()
        message.chat.send_action = AsyncMock()
        sent_message = MagicMock()
        sent_message.message_id = 123
        sent_message.chat_id = 456
        message.reply_text = AsyncMock(return_value=sent_message)

        context = MagicMock()
        context.user_data = {}
        context.bot = MagicMock()

        recommendations = [
            FormTypeRecommendation(
                form_type="CBD",
                rationale="Best *fit* for an ED_case [review] with reflection on escalation and team learning.",
                uuid=FORM_UUIDS.get("CBD"),
            )
        ]

        monkeypatch.setattr(bot, "recommend_form_types", AsyncMock(return_value=recommendations))
        monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST5")
        monkeypatch.setattr(bot, "get_curriculum", lambda _user_id: "2025")

        state = await bot._process_case_text(
            message,
            context,
            99999,
            "45M with chest pain, ECG changes, ACS treatment, cardiology escalation, and reflection.",
            "photo",
        )

        assert state == bot.AWAIT_FORM_CHOICE
        sent_text = message.reply_text.await_args.args[0]
        assert "*" not in sent_text
        assert "[" not in sent_text
        assert "]" not in sent_text
        assert "ED case" in sent_text
        assert "Privacy check" in sent_text
        assert "names, NHS numbers, DOBs, or addresses" in sent_text

    @pytest.mark.asyncio
    async def test_intermediate_qi_audit_prefers_qiat_not_teaching(self, monkeypatch):
        import bot
        from extractor import FORM_UUIDS
        from models import FormTypeRecommendation

        message = MagicMock()
        message.chat.send_action = AsyncMock()
        sent_message = MagicMock()
        sent_message.message_id = 123
        sent_message.chat_id = 456
        message.reply_text = AsyncMock(return_value=sent_message)

        context = MagicMock()
        context.user_data = {}
        context.bot = MagicMock()

        recommendations = [
            FormTypeRecommendation(
                form_type="QIAT",
                rationale="QI project with baseline, intervention and re-audit.",
                uuid=FORM_UUIDS.get("QIAT"),
            ),
            FormTypeRecommendation(
                form_type="TEACH",
                rationale="Teaching was delivered as part of the intervention.",
                uuid=FORM_UUIDS.get("TEACH"),
            ),
        ]

        monkeypatch.setattr(bot, "recommend_form_types", AsyncMock(return_value=recommendations))
        monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "INTERMEDIATE")
        monkeypatch.setattr(bot, "get_curriculum", lambda _user_id: "2025")

        state = await bot._process_case_text(
            message,
            context,
            99999,
            self.QI_AUDIT_SCREENSHOT_TEXT,
            "photo",
        )

        assert state == bot.AWAIT_FORM_CHOICE
        assert context.user_data["form_recommendations"][0].form_type == "QIAT"
        sent_keyboard = message.reply_text.await_args.kwargs["reply_markup"]
        first_button = sent_keyboard.inline_keyboard[0][0]
        assert first_button.callback_data == "FORM|best"
        assert first_button.text == "🎓 QIAT"
        assert "Teaching Session" not in first_button.text

    @pytest.mark.asyncio
    async def test_hst_qi_audit_still_prefers_qiat(self, monkeypatch):
        import bot
        from extractor import FORM_UUIDS
        from models import FormTypeRecommendation

        message = MagicMock()
        message.chat.send_action = AsyncMock()
        sent_message = MagicMock()
        sent_message.message_id = 123
        sent_message.chat_id = 456
        message.reply_text = AsyncMock(return_value=sent_message)

        context = MagicMock()
        context.user_data = {}
        context.bot = MagicMock()

        recommendations = [
            FormTypeRecommendation(
                form_type="QIAT",
                rationale="QI project with baseline, intervention and re-audit.",
                uuid=FORM_UUIDS.get("QIAT"),
            ),
            FormTypeRecommendation(
                form_type="TEACH",
                rationale="Teaching was delivered as part of the intervention.",
                uuid=FORM_UUIDS.get("TEACH"),
            ),
        ]

        monkeypatch.setattr(bot, "recommend_form_types", AsyncMock(return_value=recommendations))
        monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST5")
        monkeypatch.setattr(bot, "get_curriculum", lambda _user_id: "2025")

        state = await bot._process_case_text(
            message,
            context,
            99999,
            self.QI_AUDIT_SCREENSHOT_TEXT,
            "photo",
        )

        assert state == bot.AWAIT_FORM_CHOICE
        assert context.user_data["form_recommendations"][0].form_type == "QIAT"
        sent_keyboard = message.reply_text.await_args.kwargs["reply_markup"]
        assert sent_keyboard.inline_keyboard[0][0].text == "🎓 QIAT"

    @pytest.mark.asyncio
    async def test_question_about_case_recommendation_uses_same_plain_photo_copy(self, monkeypatch):
        import bot
        from extractor import FORM_UUIDS
        from models import FormTypeRecommendation
        from tests.bot_simulator import BotSimulator

        sim = BotSimulator()
        update = sim._make_text_update("Is CBD really the right form?")
        context = sim._make_context()
        context.user_data["case_text"] = "45M ACS case with escalation, treatment, outcome, and reflection."
        context.user_data["chosen_form"] = "CBD"
        context.user_data["case_input_source"] = "photo"

        recommendations = [
            FormTypeRecommendation(
                form_type="ACAT",
                rationale="Better *fit* for a multi_patient shift and team-flow reflection.",
                uuid=FORM_UUIDS.get("ACAT"),
            )
        ]

        monkeypatch.setattr(bot, "classify_intent", AsyncMock(return_value="question_about_case"))
        monkeypatch.setattr(bot, "recommend_form_types", AsyncMock(return_value=recommendations))
        monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST5")
        monkeypatch.setattr(bot, "get_curriculum", lambda _user_id: "2025")

        state = await bot.handle_template_review_text(update, context)

        assert state == bot.AWAIT_FORM_CHOICE
        sent_text = sim.get_last_text()
        assert "Other options that fit:" in sent_text
        assert "*" not in sent_text
        assert "multi patient" in sent_text
        assert "Privacy check" in sent_text

    @pytest.mark.asyncio
    async def test_photo_case_with_procedure_log_instruction_skips_recommender(self, monkeypatch):
        import bot

        message = MagicMock()
        message.chat.send_action = AsyncMock()
        ack = MagicMock()
        ack.message_id = 123
        ack.chat_id = 456
        ack.edit_text = AsyncMock()
        message.reply_text = AsyncMock(return_value=ack)

        context = MagicMock()
        context.user_data = {}
        context.bot = MagicMock()

        async def fail_recommend(*args, **kwargs):
            raise AssertionError("recommend_form_types should not run for explicit procedure log requests")

        async def fail_analyse(*args, **kwargs):
            raise AssertionError("_analyse_selected_form should wait until the user taps Draft")

        monkeypatch.setattr(bot, "recommend_form_types", fail_recommend)
        monkeypatch.setattr(bot, "_analyse_selected_form", fail_analyse)

        state = await bot._process_case_text(
            message,
            context,
            99999,
            "Add this case as procedural log for adult procedural sedation\n\nSedation for shoulder reduction.",
            "photo",
        )

        assert state == bot.AWAIT_FORM_CHOICE
        assert context.user_data["chosen_form"] == "PROC_LOG"
        message.reply_text.assert_awaited_once()
        sent_text = message.reply_text.await_args.args[0]
        sent_keyboard = message.reply_text.await_args.kwargs["reply_markup"]
        button_data = [
            button.callback_data
            for row in sent_keyboard.inline_keyboard
            for button in row
            if button.callback_data
        ]
        assert "Procedural Log" in sent_text
        assert "FORM|PROC_LOG" in button_data

    @pytest.mark.asyncio
    async def test_explicit_form_reuses_existing_status_message(self, monkeypatch):
        import bot

        message = MagicMock()
        message.chat_id = 456
        message.chat.id = 456
        message.reply_text = AsyncMock()

        context = MagicMock()
        context.user_data = {
            "last_bot_msg_id": 123,
            "last_bot_chat_id": 456,
        }
        context.bot.edit_message_text = AsyncMock()

        async def fail_recommend(*args, **kwargs):
            raise AssertionError("recommend_form_types should not run for explicit procedure log requests")

        async def fail_analyse(*args, **kwargs):
            raise AssertionError("_analyse_selected_form should wait until the user taps Draft")

        monkeypatch.setattr(bot, "recommend_form_types", fail_recommend)
        monkeypatch.setattr(bot, "_analyse_selected_form", fail_analyse)

        state = await bot._process_case_text(
            message,
            context,
            99999,
            "Add this case as procedural log for adult procedural sedation\n\nSedation notes.",
            "photo",
        )

        assert state == bot.AWAIT_FORM_CHOICE
        message.reply_text.assert_not_awaited()
        context.bot.edit_message_text.assert_awaited_once()
        edited = context.bot.edit_message_text.await_args.kwargs
        assert edited["message_id"] == 123
        assert "Procedural Log" in edited["text"]

    @pytest.mark.asyncio
    async def test_status_tracking_uses_chat_object_when_chat_id_missing(self, monkeypatch):
        import bot

        message = MagicMock()
        message.chat_id = 456
        message.chat.id = 456
        message.reply_text = AsyncMock()

        ack = MagicMock()
        ack.message_id = 123
        ack.chat_id = None
        ack.chat.id = 456

        context = MagicMock()
        context.user_data = {}
        context.bot.edit_message_text = AsyncMock()

        bot._track_latest_message(context, ack)

        async def fail_recommend(*args, **kwargs):
            raise AssertionError("recommend_form_types should not run for explicit procedure log requests")

        async def fail_analyse(*args, **kwargs):
            raise AssertionError("_analyse_selected_form should wait until the user taps Draft")

        monkeypatch.setattr(bot, "recommend_form_types", fail_recommend)
        monkeypatch.setattr(bot, "_analyse_selected_form", fail_analyse)

        state = await bot._process_case_text(
            message,
            context,
            99999,
            (
                "Add this case as procedural log for adult procedural sedation\n\n"
                "A 45M patient attended ED with a shoulder dislocation. I assessed "
                "analgesia and neurovascular status, performed reduction under sedation "
                "with senior support, monitored observations, and reflected on using a "
                "clearer pre-sedation checklist next time."
            ),
            "voice",
        )

        assert state == bot.AWAIT_FORM_CHOICE
        message.reply_text.assert_not_awaited()
        context.bot.edit_message_text.assert_awaited_once()
        edited = context.bot.edit_message_text.await_args.kwargs
        assert edited["chat_id"] == 456
        assert edited["message_id"] == 123
        assert "Procedural Log" in edited["text"]
