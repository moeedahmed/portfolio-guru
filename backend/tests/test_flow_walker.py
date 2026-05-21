"""Flow walker tests for the Portfolio Guru bot."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

from tests.bot_simulator import BotSimulator


SAMPLE_CASES = {
    "valid": "45M with chest pain, troponin positive, managed as ACS and reflected on escalation.",
    "gibberish": "asdfghjkl random weather bananas",
    "empty": "",
}


@pytest.fixture
def recommended_forms():
    from extractor import FORM_UUIDS
    from models import FormTypeRecommendation

    return [
        FormTypeRecommendation(form_type="CBD", rationale="Best fit for a reflective case review.", uuid=FORM_UUIDS.get("CBD")),
        FormTypeRecommendation(form_type="ACAT", rationale="Acute take and team flow were relevant.", uuid=FORM_UUIDS.get("ACAT")),
    ]


@pytest.fixture
def thin_draft():
    from models import FormDraft

    return FormDraft(
        form_type="CBD",
        uuid="uuid-cbd",
        fields={
            "date_of_encounter": "2026-03-17",
            "clinical_setting": "ED",
            "patient_presentation": "Chest pain",
            "clinical_reasoning": "Managed as ACS.",
            "reflection": "Need faster ECG review.",
            "curriculum_links": ["SLO1"],
            "key_capabilities": ["SLO1 KC1: Assess and stabilise the patient"],
        },
    )


class TestFlowWalker:
    @pytest.mark.asyncio
    async def test_start_paths_offer_next_step(self):
        from bot import start

        sim = BotSimulator()
        update = sim._make_text_update('/start')
        context = sim._make_context()

        with patch('bot.has_credentials', return_value=False):
            result = await start(update, context)

        assert result == ConversationHandler.END
        assert any(data == 'ACTION|setup' for _, data in sim.get_last_buttons())

        sim = BotSimulator()
        update = sim._make_text_update('/start')
        context = sim._make_context()
        with patch('bot.has_credentials', return_value=True):
            result = await start(update, context)

        assert result == ConversationHandler.END
        # Connected user: the welcome message tells them to send a case. The
        # keyboard is intentionally empty — no inline buttons, no re-prompt.
        # Settings/Health/Help are reachable via the Telegram Menu (☰).
        assert 'send' in sim.get_last_text().lower()
        assert sim.get_last_buttons() == []

    @pytest.mark.asyncio
    async def test_case_input_walks_to_form_choice(self, recommended_forms):
        from bot import AWAIT_FORM_CHOICE, handle_case_input

        sim = BotSimulator()
        update = sim._make_text_update(SAMPLE_CASES['valid'])
        context = sim._make_context()

        with patch('bot.has_credentials', return_value=True),              patch('bot.classify_intent', new_callable=AsyncMock, return_value='case'),              patch('bot.recommend_form_types', new_callable=AsyncMock, return_value=recommended_forms),              patch('bot.get_training_level', return_value='ST5'),              patch('bot.get_curriculum', return_value='2025'),              patch('bot.check_can_file', new_callable=AsyncMock, return_value=(True, 0, 5, 'free')):
            result = await handle_case_input(update, context)

        assert result == AWAIT_FORM_CHOICE
        button_data = [data for _, data in sim.get_last_buttons()]
        assert 'FORM|best' in button_data
        assert 'FORM|show_all' in button_data
        assert context.user_data['last_funnel_event'] == 'recommendation_shown'
        assert context.user_data['case_text'] == SAMPLE_CASES['valid']
        assert context.user_data['status_msg_id']
        assert context.user_data['last_bot_msg_id'] == context.user_data['status_msg_id']

    @pytest.mark.asyncio
    async def test_explicit_form_waits_for_draft_button(self):
        from bot import AWAIT_FORM_CHOICE, handle_case_input

        sim = BotSimulator()
        update = sim._make_text_update(
            "Add this case as procedural log for adult procedural sedation. Shoulder reduction under sedation."
        )
        context = sim._make_context()

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.classify_intent', new_callable=AsyncMock, return_value='case'), \
             patch('bot.recommend_form_types', new_callable=AsyncMock) as recommend, \
             patch('bot._analyse_selected_form', new_callable=AsyncMock) as analyse, \
             patch('bot.check_can_file', new_callable=AsyncMock, return_value=(True, 0, 5, 'free')):
            result = await handle_case_input(update, context)

        assert result == AWAIT_FORM_CHOICE
        assert context.user_data['chosen_form'] == 'PROC_LOG'
        assert any(data == 'FORM|PROC_LOG' for _, data in sim.get_last_buttons())
        recommend.assert_not_awaited()
        analyse.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_form_choice_shows_partial_draft_first(self, thin_draft):
        from bot import AWAIT_APPROVAL, handle_form_choice

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']

        update = sim._make_callback_update('FORM|CBD')
        with patch('bot._analyse_selected_form', new_callable=AsyncMock, return_value=thin_draft):
            result = await handle_form_choice(update, context)

        assert result == AWAIT_APPROVAL
        button_data = {data for _, data in sim.get_last_buttons()}
        assert {'APPROVE|draft', 'IMPROVE|reflection', 'CANCEL|draft'} <= button_data
        assert 'ACTION|continue_thin' not in button_data
        assert 'ACTION|back_to_missing' not in button_data
        assert 'EDIT|draft' not in button_data
        assert 'APPROVE|submit' not in button_data
        text = sim.get_last_text()
        assert 'Case-Based Discussion draft ready' in text
        assert 'Needs review before this is complete' in text
        assert 'Stage of Training' in text
        assert 'I have left those fields blank rather than inventing them' in text

    @pytest.mark.asyncio
    async def test_form_choice_asks_for_detail_when_extraction_is_too_thin(self):
        from bot import AWAIT_CASE_INPUT, handle_form_choice
        from models import FormDraft

        empty_draft = FormDraft(form_type='CBD', uuid='uuid-cbd', fields={})
        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['case_text'] = 'Patient seen in ED with some symptoms and reviewed.'

        update = sim._make_callback_update('FORM|CBD')
        with patch('bot._analyse_selected_form', new_callable=AsyncMock, return_value=empty_draft):
            result = await handle_form_choice(update, context)

        assert result == AWAIT_CASE_INPUT
        assert 'need a bit more clinical detail before drafting' in sim.get_last_text()
        assert 'draft_data' not in context.user_data
        assert 'APPROVE|draft' not in {data for _, data in sim.get_last_buttons()}

    @pytest.mark.asyncio
    async def test_best_fit_button_uses_top_recommendation(self, thin_draft, recommended_forms):
        from bot import AWAIT_APPROVAL, handle_form_choice

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']
        context.user_data['form_recommendations'] = recommended_forms

        update = sim._make_callback_update('FORM|best')
        with patch('bot.get_curriculum', return_value='2025'), \
             patch('bot._analyse_selected_form', new_callable=AsyncMock, return_value=thin_draft), \
             patch('bot._missing_template_fields', return_value=([], [], [])):
            result = await handle_form_choice(update, context)

        assert result == AWAIT_APPROVAL
        assert context.user_data['chosen_form'] == 'CBD'
        assert context.user_data['last_funnel_event'] == 'draft_shown'
        assert {'APPROVE|draft', 'CANCEL|draft'} <= {data for _, data in sim.get_last_buttons()}

    @pytest.mark.asyncio
    async def test_optional_missing_fields_do_not_block_draft_preview(self, thin_draft):
        from bot import AWAIT_APPROVAL, handle_form_choice

        sim = BotSimulator()
        update = sim._make_callback_update('FORM|CBD')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']

        optional_field = {'label': 'Supervisor', 'key': 'supervisor_name'}
        present_field = {'label': 'Date', 'key': 'date_of_encounter'}
        with patch('bot._analyse_selected_form', new_callable=AsyncMock, return_value=thin_draft), \
             patch('bot._missing_template_fields', return_value=([], [optional_field], [present_field, present_field])):
            result = await handle_form_choice(update, context)

        assert result == AWAIT_APPROVAL
        assert 'draft ready' in sim.get_last_text().lower()
        assert 'Helpful detail if you have it: Supervisor' in sim.get_last_text()
        assert any(data == 'APPROVE|draft' for _, data in sim.get_last_buttons())

    def test_draft_preview_splits_long_narrative_without_mutating_fields(self, thin_draft):
        from bot import _format_draft_preview
        from models import FormDraft

        long_reflection = (
            "I initially focused on the abnormal ECG and chest pain pathway while the department was busy. "
            "I reviewed the observations, repeated the ECG, discussed the dynamic changes with the medical registrar, "
            "and escalated to cardiology when the symptoms persisted. "
            "The case reminded me to keep reassessing the working diagnosis when the initial treatment does not settle the symptoms. "
            "In future I will set an earlier review point for high-risk chest pain patients and document the escalation plan more clearly."
        )
        draft = FormDraft(
            form_type='CBD',
            uuid='uuid-cbd',
            fields={
                **thin_draft.fields,
                'reflection': long_reflection,
            },
        )

        preview = _format_draft_preview(draft)
        reflection_block = preview.split('💭 *Reflection of event:*', 1)[1].split('🎚️', 1)[0]
        paragraphs = [p.strip() for p in reflection_block.split('\n\n') if p.strip()]

        assert len(paragraphs) >= 2
        assert all(len(paragraph.split()) <= 55 for paragraph in paragraphs)
        assert draft.fields['reflection'] == long_reflection

    def test_draft_preview_keeps_missing_markers_for_blank_required_fields(self):
        from bot import _MISSING_MARKER, _format_draft_preview
        from models import FormDraft

        draft = FormDraft(
            form_type='DOPS',
            uuid='uuid-dops',
            fields={
                'date_of_encounter': '2026-03-17',
                'clinical_setting': '',
                'stage_of_training': 'ST5',
                'procedural_skill': '',
                'indication': 'Shoulder reduction under procedural sedation.',
                'trainee_performance': '',
                'level_of_supervision': 'Indirect',
            },
        )

        preview = _format_draft_preview(draft)

        assert _MISSING_MARKER in preview
        assert draft.fields['clinical_setting'] == ''
        assert draft.fields['procedural_skill'] == ''

    @pytest.mark.asyncio
    async def test_quick_improve_updates_reflection_only(self, thin_draft):
        from bot import AWAIT_APPROVAL, handle_quick_improve
        from models import FormDraft

        improved = FormDraft(
            form_type='CBD',
            uuid='uuid-cbd',
            fields={**thin_draft.fields, 'reflection': 'I will escalate dynamic ECG changes earlier and document the decision-making more clearly.'},
        )

        sim = BotSimulator()
        update = sim._make_callback_update('IMPROVE|reflection')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': 'CBD',
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        with patch('bot.get_voice_profile', return_value=''), \
             patch('bot.extract_form_data', new_callable=AsyncMock, return_value=improved):
            result = await handle_quick_improve(update, context)

        assert result == AWAIT_APPROVAL
        updated_fields = context.user_data['draft_data']['fields']
        assert updated_fields['reflection'] == improved.fields['reflection']
        assert updated_fields['clinical_reasoning'] == thin_draft.fields['clinical_reasoning']
        assert any(data == 'APPROVE|draft' for _, data in sim.get_last_buttons())

    @pytest.mark.asyncio
    async def test_all_forms_screen_has_navigation(self):
        from bot import AWAIT_FORM_CHOICE, FORM_CATEGORIES, _CAT_SLUGS, handle_form_choice

        sim = BotSimulator()
        update = sim._make_callback_update('FORM|show_all')
        context = sim._make_context()

        with patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'):
            result = await handle_form_choice(update, context)

        assert result == AWAIT_FORM_CHOICE
        button_data = [data for _, data in sim.get_last_buttons()]
        # Should show category buttons, search, and back
        for cat_name, slug in _CAT_SLUGS.items():
            assert f'FORM|cat_{slug}' in button_data, f"Missing category button for {cat_name}"
        assert 'FORM|search' in button_data
        assert 'FORM|back' in button_data

    @pytest.mark.asyncio
    async def test_category_shows_filtered_forms(self):
        from bot import AWAIT_FORM_CHOICE, FORM_CATEGORIES, handle_form_choice

        sim = BotSimulator()
        update = sim._make_callback_update('FORM|cat_CLINICAL')
        context = sim._make_context()

        with patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'):
            result = await handle_form_choice(update, context)

        assert result == AWAIT_FORM_CHOICE
        button_data = [data for _, data in sim.get_last_buttons()]
        # Should contain form buttons and back-to-categories
        assert 'FORM|show_all' in button_data  # back to categories
        assert any(d.startswith('FORM|') and d != 'FORM|show_all' for d in button_data)

    @pytest.mark.asyncio
    async def test_search_returns_to_form_choice(self):
        from bot import AWAIT_FORM_SEARCH, handle_form_choice

        sim = BotSimulator()
        update = sim._make_callback_update('FORM|search')
        context = sim._make_context()

        result = await handle_form_choice(update, context)
        assert result == AWAIT_FORM_SEARCH

    @pytest.mark.asyncio
    async def test_callback_buttons_have_guardrails(self, thin_draft):
        from bot import (
            handle_action_button,
            handle_approval_approve,
            handle_approval_edit,
            handle_callback,
            handle_edit_field,
            handle_form_choice,
            handle_info_button,
            handle_set_curriculum,
            setup_training_level,
            voice_collect_example,
        )

        callbacks = [
            'ACTION|setup', 'ACTION|voice', 'ACTION|status', 'ACTION|delete',
            'ACTION|back_to_missing',
            'INFO|what', 'FORM|show_all', 'FORM|disabled', 'FORM|switch_curriculum', 'FORM|back',
            'CANCEL|form', 'CANCEL|draft', 'APPROVE|draft',
            'FIELD|date_of_encounter', 'SET_CURRICULUM|2025', 'LEVEL|HIGHER',
            'VOICE|cancel', 'VOICE|remove', 'VOICE|rebuild', 'VOICE|more',
        ]

        for callback in callbacks:
            sim = BotSimulator()
            update = sim._make_callback_update(callback)
            context = sim._make_context()
            context.user_data.update({
                'case_text': SAMPLE_CASES['valid'],
                'chosen_form': 'CBD',
                'pending_draft_data': {'_type': 'FORM', 'form_type': 'CBD', 'fields': thin_draft.fields, 'uuid': 'uuid-cbd'},
                'draft_data': {'_type': 'FORM', 'form_type': 'CBD', 'fields': thin_draft.fields, 'uuid': 'uuid-cbd'},
                'form_recommendations': [],
                'form_recommendations_text': 'Choose a form',
                'voice_examples': ['one', 'two', 'three'],
            })

            with patch('bot.has_credentials', return_value=True),                  patch('bot.get_credentials', return_value=('user', 'pass')),                  patch('bot.get_training_level', return_value='ST5'),                  patch('bot.get_curriculum', return_value='2025'),                  patch('bot.store_curriculum'),                  patch('bot.store_training_level'),                  patch('bot.get_voice_profile', return_value=None),                  patch('bot.clear_voice_profile'),                  patch('bot.route_filing', new_callable=AsyncMock, return_value={'status': 'success', 'filled': [], 'skipped': [], 'method': 'deterministic'}),                  patch('bot._analyse_selected_form', new_callable=AsyncMock, return_value=thin_draft),                  patch('bot._missing_template_fields', return_value=([], [], [])),                  patch('bot._build_voice_profile', new_callable=AsyncMock, return_value=ConversationHandler.END):
                if callback.startswith('ACTION|') and callback not in {'ACTION|file', 'ACTION|reset', 'ACTION|cancel', 'ACTION|add_detail', 'ACTION|continue_thin', 'ACTION|back_to_missing', 'ACTION|retry_filing'}:
                    await handle_action_button(update, context)
                elif callback.startswith('INFO|'):
                    await handle_info_button(update, context)
                elif callback.startswith('FORM|'):
                    await handle_form_choice(update, context)
                elif callback.startswith('CANCEL|'):
                    await handle_callback(update, context)
                elif callback.startswith('APPROVE|'):
                    await handle_approval_approve(update, context)
                elif callback.startswith('EDIT|'):
                    await handle_approval_edit(update, context)
                elif callback.startswith('FIELD|'):
                    await handle_edit_field(update, context)
                elif callback.startswith('SET_CURRICULUM|'):
                    await handle_set_curriculum(update, context)
                elif callback.startswith('LEVEL|'):
                    await setup_training_level(update, context)
                elif callback.startswith('VOICE|'):
                    await voice_collect_example(update, context)

    @pytest.mark.asyncio
    async def test_guardrails_for_gibberish_and_empty_text(self):
        from bot import AWAIT_CASE_INPUT, ConversationHandler, handle_case_input, handle_mid_conversation_text

        sim = BotSimulator()
        update = sim._make_text_update(SAMPLE_CASES['gibberish'])
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']

        with patch('bot.classify_intent', new_callable=AsyncMock, return_value='chitchat'):
            result = await handle_mid_conversation_text(update, context)
        assert result in {AWAIT_CASE_INPUT, ConversationHandler.END, 2, 9, 3}
        assert sim.get_last_text()

        sim = BotSimulator()
        update = sim._make_text_update(SAMPLE_CASES['empty'])
        context = sim._make_context()
        with patch('bot.has_credentials', return_value=True):
            result = await handle_case_input(update, context)
        assert result == ConversationHandler.END
        assert sim.get_last_text()

    @pytest.mark.asyncio
    async def test_cancel_path_leaves_user_with_clear_next_step(self):
        from bot import handle_callback

        sim = BotSimulator()
        update = sim._make_callback_update('CANCEL|form')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.get_training_level', return_value='ST5'):
            result = await handle_callback(update, context)

        assert result == ConversationHandler.END
        assert 'cancelled' in sim.get_last_text().lower()
        # Post-cancel for a connected user shows NO inline buttons — the next
        # action is to type/send a fresh case, and Settings etc. live in the
        # Telegram Menu (☰).
        assert sim.get_last_buttons() == []

    @pytest.mark.asyncio
    async def test_action_cancel_ends_conversation_state(self):
        from bot import handle_callback

        sim = BotSimulator()
        update = sim._make_callback_update('ACTION|cancel')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.get_training_level', return_value='ST5'):
            result = await handle_callback(update, context)

        assert result == ConversationHandler.END
        assert context.user_data == {'post_reset': True}
        assert 'cancelled' in sim.get_last_text().lower()

    @pytest.mark.asyncio
    async def test_file_another_case_starts_from_clean_case_state(self, recommended_forms):
        from bot import AWAIT_CASE_INPUT, handle_callback

        sim = BotSimulator()
        update = sim._make_callback_update('ACTION|file')
        context = sim._make_context()
        context.user_data.update({
            'case_text': 'Old case: cardioversion and sedation.',
            'chosen_form': 'DOPS',
            'draft_data': {
                '_type': 'FORM',
                'form_type': 'DOPS',
                'fields': {'procedure_name': 'DC cardioversion'},
                'uuid': 'old',
            },
            'pending_draft_data': {'_type': 'FORM', 'form_type': 'DOPS', 'fields': {}, 'uuid': 'old'},
            'form_recommendations': recommended_forms,
            'form_recommendations_text': 'Old recommendations',
            'awaiting_detail': True,
            'quick_improve_used': True,
            'excluded_form_type': 'DOPS',
        })

        with patch('bot.has_credentials', return_value=True):
            result = await handle_callback(update, context)

        assert result == AWAIT_CASE_INPUT
        assert context.user_data == {}
        assert 'send' in sim.get_last_text().lower()

    @pytest.mark.asyncio
    async def test_cancel_path_uses_setup_button_when_setup_incomplete(self):
        from bot import handle_callback

        sim = BotSimulator()
        update = sim._make_callback_update('CANCEL|draft')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']

        with patch('bot.has_credentials', return_value=False), \
             patch('bot.get_training_level', return_value=None):
            result = await handle_callback(update, context)

        assert result == ConversationHandler.END
        assert 'cancelled' in sim.get_last_text().lower()
        assert any(data == 'ACTION|setup' for _, data in sim.get_last_buttons())

    @pytest.mark.asyncio
    async def test_stale_button_redirects_to_fresh_next_step(self):
        from bot import handle_form_choice

        sim = BotSimulator()
        update = sim._make_callback_update('FORM|CBD')
        context = sim._make_context()

        with patch('bot.has_credentials', return_value=True),              patch('bot.get_training_level', return_value='ST5'):
            result = await handle_form_choice(update, context)

        assert result == ConversationHandler.END
        assert sim.messages_sent[-1][0] == 'reply'
        assert 'start a new case' in sim.get_last_text().lower()
        # Connected user post-recovery: no inline buttons; user types the case.
        assert sim.get_last_buttons() == []

    @pytest.mark.asyncio
    async def test_form_choice_extra_case_text_refreshes_recommendation(self, recommended_forms):
        from bot import AWAIT_FORM_CHOICE, handle_mid_conversation_text

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data.update({
            'case_text': 'Initial case: hypotensive unstable AF, sedation, cardioversion and amiodarone.',
            'case_input_source': 'text',
            'form_recommendations': recommended_forms,
            'last_bot_msg_id': 42,
            'last_bot_chat_id': sim.user_id,
            'status_msg_id': 42,
            'status_msg_chat': sim.user_id,
        })
        extra_text = (
            'This is another section of the same case: the patient remained pale, '
            'we checked bedside echo, considered septic shock, gave antibiotics and admitted.'
        )
        update = sim._make_text_update(extra_text)

        with patch('bot.classify_intent', new=AsyncMock(return_value='new_case')), \
             patch('bot.has_credentials', return_value=True), \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.recommend_form_types', new=AsyncMock(return_value=recommended_forms)):
            result = await handle_mid_conversation_text(update, context)

        assert result == AWAIT_FORM_CHOICE
        assert 'It looks like you want to file a new case' not in sim.get_last_text()
        assert extra_text in context.user_data['case_text']
        assert 'Forms that fit your case' in sim.get_last_text()
        assert sim.messages_sent[-1][0] == 'bot_edit'

    @pytest.mark.asyncio
    async def test_expired_draft_recovery_updates_latest_template_message(self, thin_draft):
        from bot import AWAIT_APPROVAL, handle_approval_edit

        sim = BotSimulator()
        update = sim._make_callback_update('EDIT|draft')
        context = sim._make_context()
        context.user_data.update({
            'case_text': SAMPLE_CASES['valid'],
            'chosen_form': thin_draft.form_type,
            'pending_draft_data': {
                '_type': 'FORM',
                'form_type': thin_draft.form_type,
                'fields': thin_draft.fields,
                'uuid': thin_draft.uuid,
            },
        })

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.get_training_level', return_value='ST5'):
            result = await handle_approval_edit(update, context)

        assert result == AWAIT_APPROVAL
        assert sim.messages_sent[-1][0] == 'bot_edit'
        assert 'still ready' in sim.messages_sent[-2][1].lower()
        button_data = {data for _, data in sim.get_last_buttons()}
        assert {'APPROVE|draft', 'CANCEL|draft'} <= button_data
        assert 'ACTION|continue_thin' not in button_data

    @pytest.mark.asyncio
    async def test_paused_approval_button_recovers_latest_draft_message(self, thin_draft):
        from bot import AWAIT_APPROVAL, handle_approval_approve

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data.update({
            'case_text': SAMPLE_CASES['valid'],
            'chosen_form': thin_draft.form_type,
            'pending_draft_data': {
                '_type': 'FORM',
                'form_type': thin_draft.form_type,
                'fields': thin_draft.fields,
                'uuid': thin_draft.uuid,
            },
        })

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.has_credentials', return_value=True), \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot._missing_template_fields', return_value=([], [], [])):
            result = await handle_approval_approve(update, context)

        assert result == AWAIT_APPROVAL
        assert sim.messages_sent[-1][0] == 'bot_edit'
        assert 'still ready' in sim.messages_sent[-2][1].lower()
        button_data = {data for _, data in sim.get_last_buttons()}
        assert {'APPROVE|draft'} <= button_data
        assert 'EDIT|draft' not in button_data

    @pytest.mark.asyncio
    async def test_filing_completion_updates_progress_message(self, thin_draft):
        from bot import handle_approval_approve

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={'status': 'success', 'filled': [], 'skipped': [], 'method': 'deterministic'}):
            result = await handle_approval_approve(update, context)

        assert result == ConversationHandler.END
        # The original reviewed draft is not edited into a progress message.
        assert update.callback_query.message.edit_text.await_count == 0
        # The temporary progress message becomes the final report; no extra
        # "Filing finished" message is sent before the report.
        assert sim.messages_sent[-1][0] == 'edit'
        assert 'case-based discussion saved' in sim.get_last_text().lower()
        assert 'filing finished' not in sim.get_last_text().lower()
        buttons = sim.get_last_buttons()
        # First button may be File another case or the amend button row
        assert ('📋 File another case', 'ACTION|file') in buttons
        assert ('👍 It worked', 'FEEDBACK|good|CBD|success') in buttons
        assert ('✏️ Amend this draft', 'AMEND|amend') in buttons
        assert ("👎 Didn't work", 'FEEDBACK|bad|CBD|success') in buttons
        assert ('⋯ More options', 'ACTION|post_file_more|CBD|success') in buttons

    @pytest.mark.asyncio
    async def test_text_file_this_files_active_draft_when_state_reentered(self, thin_draft):
        from bot import handle_case_input

        sim = BotSimulator()
        update = sim._make_text_update('File this')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new=AsyncMock(return_value={
                 'status': 'success',
                 'filled': ['date_of_encounter'],
                 'skipped': [],
                 'method': 'deterministic',
             })):
            result = await handle_case_input(update, context)

        assert result == ConversationHandler.END
        assert 'case-based discussion saved' in sim.get_last_text().lower()
        assert context.user_data['last_filing_status'] == 'success'

    @pytest.mark.asyncio
    async def test_recent_filing_question_reports_saved_status(self):
        from bot import handle_case_input

        sim = BotSimulator()
        update = sim._make_text_update('What happened, were you stuck filing this case?')
        context = sim._make_context()
        context.user_data.update({
            'last_filing_status': 'success',
            'last_filing_form_name': 'Direct Observation of Procedural Skills',
        })

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))):
            result = await handle_case_input(update, context)

        assert result == ConversationHandler.END
        assert 'was saved to kaizen as a draft' in sim.get_last_text().lower()

    @pytest.mark.asyncio
    async def test_thin_dops_save_returns_to_approval_with_missing_detail_copy(self):
        """A thin DOPS draft must not reach route_filing. The user is kept on
        the draft-approval screen with a concise list of missing detail and
        the same Save/Quick improve/Cancel keyboard. The reviewed draft
        preview message stays intact — the blocker arrives as a separate
        message so the user can still see what they reviewed.
        """
        from bot import AWAIT_APPROVAL, handle_approval_approve
        from models import FormDraft

        thin_dops = FormDraft(
            form_type='DOPS',
            uuid='uuid-dops',
            fields={
                'date_of_encounter': '2026-05-19',
                'stage_of_training': 'Higher/ST4-ST6',
                'clinical_setting': 'Emergency Department',
                'procedure_name': 'DC cardioversion',
                # No indication, no trainee performance — label-only narrative.
                'case_observed': 'Procedure observed: DC cardioversion',
            },
        )

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_dops.form_type,
            'fields': thin_dops.fields,
            'uuid': thin_dops.uuid,
        }

        route_filing_mock = AsyncMock(return_value={
            'status': 'success', 'filled': [], 'skipped': [], 'method': 'deterministic',
        })

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new=route_filing_mock):
            result = await handle_approval_approve(update, context)

        # The gate fires BEFORE Kaizen is touched.
        route_filing_mock.assert_not_awaited()
        assert result == AWAIT_APPROVAL
        # Draft is preserved so the user can fix it.
        assert context.user_data.get('draft_data')
        # The reviewed draft preview message text must NOT be overwritten
        # with the blocker — only the approval keyboard is disarmed.
        assert update.callback_query.message.edit_text.await_count == 0
        text = (sim.get_last_text() or '').lower()
        assert 'indication' in text
        assert 'trainee performance' in text
        # The approval keyboard is still in place so user can edit and resave.
        buttons = {data for _, data in sim.get_last_buttons()}
        assert 'APPROVE|draft' in buttons
        assert 'CANCEL|draft' in buttons

    @pytest.mark.asyncio
    async def test_dops_save_with_missing_date_proceeds_with_separate_warning(self):
        """A DOPS draft missing only the date is useful enough to file — the
        bot must respect the user's explicit Save as draft, warn about the
        gap in a separate message, leave the reviewed draft preview intact,
        and call route_filing so Kaizen actually receives the draft.

        Dogfood ask: 'missing fields can be warned about, but explicit Save
        as draft should proceed unless the draft is genuinely unsafe/near-empty'.
        """
        from bot import handle_approval_approve
        from models import FormDraft

        useful_dops = FormDraft(
            form_type='DOPS',
            uuid='uuid-dops',
            fields={
                # Date intentionally absent.
                'stage_of_training': 'Higher/ST4-ST6',
                'clinical_setting': 'Emergency Department',
                'procedure_name': 'DC cardioversion',
                'indication': (
                    'Unstable atrial fibrillation with rapid ventricular '
                    'response and hypotension despite initial fluid '
                    'resuscitation.'
                ),
                'trainee_performance': (
                    'I led the synchronised cardioversion under ketamine '
                    'sedation, delivered three escalating shocks, and '
                    'escalated to ITU after the rhythm became refractory.'
                ),
                'reflection': (
                    'Reinforced the value of early ITU escalation when the '
                    'rhythm fails to convert and the patient remains '
                    'compromised.'
                ),
            },
        )

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': useful_dops.form_type,
            'fields': useful_dops.fields,
            'uuid': useful_dops.uuid,
        }

        route_filing_mock = AsyncMock(return_value={
            'status': 'success',
            'filled': ['stage_of_training', 'procedure_name', 'case_observed', 'reflection'],
            'skipped': [],
            'method': 'deterministic',
        })

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new=route_filing_mock):
            result = await handle_approval_approve(update, context)

        # The user's explicit Save was honoured — the save was attempted.
        route_filing_mock.assert_awaited_once()
        assert result == ConversationHandler.END
        # The reviewed draft preview was not overwritten with a blocker.
        assert update.callback_query.message.edit_text.await_count == 0
        # A separate warning message mentions the missing Date and frames
        # it as a recoverable gap (not the "saving..." progress ack).
        fresh_messages = [
            text for kind, text, _ in sim.messages_sent
            if kind in ('reply', 'send') and text
        ]
        warning_candidates = [
            text for text in fresh_messages
            if 'date' in text.lower()
            and ('gap' in text.lower() or 'add' in text.lower())
        ]
        assert warning_candidates, (
            'expected a separate warning mentioning the missing Date '
            'and framing it as a gap to add later; '
            f'got messages: {[(k, t) for k, t, _ in sim.messages_sent]}'
        )
        # And the warning lands BEFORE the save ack — the order matters so
        # the user reads the gap heads-up first.
        message_texts = [text for _, text, _ in sim.messages_sent if text]
        warning_idx = next(
            (i for i, t in enumerate(message_texts) if t in warning_candidates),
            -1,
        )
        ack_idx = next(
            (i for i, t in enumerate(message_texts) if 'kaizen draft…' in t.lower()),
            -1,
        )
        assert 0 <= warning_idx < ack_idx, (
            f'warning should precede save ack; warning idx={warning_idx}, '
            f'ack idx={ack_idx}, messages={message_texts}'
        )

    @pytest.mark.asyncio
    async def test_uncertain_save_keeps_draft_and_offers_compact_recovery(self, thin_draft):
        from bot import handle_approval_approve

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'partial',
                 'filled': ['date_of_encounter'],
                 'skipped': [],
                 'error': 'Save was clicked, but I could not confirm the entry in the activities list.',
                 'method': 'deterministic',
             }):
            result = await handle_approval_approve(update, context)

        assert result == ConversationHandler.END
        assert context.user_data.get('draft_data')
        assert 'may not have saved' in sim.get_last_text().lower()
        buttons = sim.get_last_buttons()
        assert ('👍 It worked', 'FEEDBACK|good|CBD|partial') not in buttons
        assert ("👎 Didn't work", 'FEEDBACK|bad|CBD|partial') not in buttons
        assert ('⋯ More options', 'ACTION|post_file_more|CBD|partial') in buttons

    @pytest.mark.asyncio
    async def test_manual_review_dops_partial_does_not_show_success_buttons(self):
        from bot import handle_approval_approve
        from models import FormDraft

        dops_draft = FormDraft(
            form_type='DOPS',
            uuid='uuid-dops',
            fields={
                'date_of_encounter': '2026-05-19',
                'stage_of_training': 'Higher/ST4-ST6',
                'clinical_setting': 'Emergency Department',
                'placement': 'Emergency Department',
                'procedure_name': 'Direct current cardioversion',
                'procedural_skill': 'Direct current cardioversion',
                'indication': 'unstable atrial fibrillation with hypotension despite initial treatment',
                'clinical_reasoning': 'I recognised instability, prepared sedation and escalation, and planned synchronised shocks.',
                'trainee_performance': (
                    'I led the team briefing, consent discussion, ketamine sedation, synchronised shocks, '
                    'post-procedure reassessment, and ITU escalation.'
                ),
                'reflection': 'I will continue to rehearse pre-sedation checks and closed-loop team communication.',
                'curriculum_links': ['SLO3', 'SLO6'],
                'key_capabilities': ['Manages critically ill patients', 'Safely performs practical procedures'],
            },
        )

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['case_text'] = 'DOPS cardioversion case'
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': dops_draft.form_type,
            'fields': dops_draft.fields,
            'uuid': dops_draft.uuid,
        }

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.record_case_filed', new=AsyncMock()), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 10, -1, 'pro_plus'))), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'partial',
                 'filled': [
                     'stage_of_training',
                     'date_of_encounter',
                     'end_date',
                     'procedural_skill',
                     'case_observed',
                     'reflection',
                     'curriculum_links',
                     'key_capabilities',
                 ],
                 'skipped': ['placement'],
                 'method': 'deterministic',
             }):
            result = await handle_approval_approve(update, context)

        assert result == ConversationHandler.END
        text = sim.get_last_text()
        assert 'saved as a draft, but needs manual review' in text
        assert '8 fields filled' in text
        assert 'needs your review: Placement' in text
        assert 'This is not complete yet' in text
        assert '10 cases this month (Unlimited)' in text
        assert '✅ *Direct Observation of Procedural Skills saved.*' not in text

        buttons = sim.get_last_buttons()
        callbacks = {callback for _, callback in buttons}
        assert 'FEEDBACK|good|DOPS|partial' not in callbacks
        assert 'FEEDBACK|bad|DOPS|partial' not in callbacks
        assert 'AMEND|amend' not in callbacks
        assert 'ACTION|file' in callbacks
        assert 'ACTION|post_file_more|DOPS|partial' in callbacks

        markup = sim.messages_sent[-1][2]
        url_buttons = [
            button.url
            for row in markup.inline_keyboard
            for button in row
            if getattr(button, 'url', None)
        ]
        assert any('kaizenep.com/events/new-section/' in url for url in url_buttons)

    @pytest.mark.asyncio
    async def test_post_filing_more_expands_secondary_actions(self, thin_draft):
        from bot import handle_action_button

        sim = BotSimulator()
        update = sim._make_callback_update('ACTION|post_file_more|CBD|partial')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        await handle_action_button(update, context)

        buttons = sim.get_last_buttons()
        assert ('🔄 Try again', 'ACTION|retry_filing') in buttons
        assert ('📋 File another case', 'ACTION|file') in buttons
        assert ('💬 Something missing?', 'FILING|feedback|CBD') in buttons
        assert ('⚙️ Settings', 'ACTION|settings') in buttons

    @pytest.mark.asyncio
    async def test_failed_filing_uses_llm_recovery_copy(self, thin_draft):
        from bot import handle_approval_approve

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        recovery_line = "Kaizen rejected the login — your password may have changed. Update credentials and retry."

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'failed', 'filled': [], 'skipped': [], 'method': 'deterministic',
                 'error': 'Login failed',
             }), \
             patch('bot.compose_filing_recovery_copy', new=AsyncMock(return_value=recovery_line)):
            await handle_approval_approve(update, context)

        text = sim.get_last_text()
        assert recovery_line in text
        assert "Filing didn't complete" in text

    @pytest.mark.asyncio
    async def test_failed_filing_falls_back_to_static_when_llm_empty(self, thin_draft):
        from bot import handle_approval_approve

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'failed', 'filled': [], 'skipped': [], 'method': 'deterministic',
                 'error': 'Something went wrong',
             }), \
             patch('bot.compose_filing_recovery_copy', new=AsyncMock(return_value="")):
            await handle_approval_approve(update, context)

        text = sim.get_last_text()
        assert 'Try again' in text or 'manually' in text
        assert 'Something went wrong' in text

    @pytest.mark.asyncio
    async def test_natural_language_edit_applies_to_draft_and_keeps_approval(self, thin_draft):
        from bot import handle_mid_conversation_text

        sim = BotSimulator()
        update = sim._make_text_update('change the date to 2026-05-12')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': dict(thin_draft.fields),
            'uuid': thin_draft.uuid,
        }
        context.user_data['chosen_form'] = thin_draft.form_type
        context.user_data['case_text'] = 'short context'

        with patch('bot.classify_intent', new=AsyncMock(return_value='edit_detail')), \
             patch('bot.extract_field_updates', new=AsyncMock(return_value={
                 'date_of_encounter': '2026-05-12',
                 '__summary__': 'Date moved to 12 May 2026.',
             })):
            result = await handle_mid_conversation_text(update, context)

        from bot import AWAIT_APPROVAL
        assert result == AWAIT_APPROVAL
        assert context.user_data['draft_data']['fields']['date_of_encounter'] == '2026-05-12'
        text = sim.get_last_text() or ''
        assert 'Updated: Date moved to 12 May 2026.' in text

    @pytest.mark.asyncio
    async def test_natural_language_edit_with_no_match_regenerates_draft(self, thin_draft):
        from bot import handle_mid_conversation_text, AWAIT_APPROVAL

        sim = BotSimulator()
        update = sim._make_text_update('make the reflection focus on leadership')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': dict(thin_draft.fields),
            'uuid': thin_draft.uuid,
        }
        context.user_data['chosen_form'] = thin_draft.form_type
        context.user_data['case_text'] = 'short context'

        with patch('bot.classify_intent', new=AsyncMock(return_value='edit_detail')), \
             patch('bot.extract_field_updates', new=AsyncMock(return_value={})), \
             patch('bot.extract_cbd_data', new=AsyncMock(return_value=thin_draft)):
            result = await handle_mid_conversation_text(update, context)

        text = (sim.get_last_text() or '').lower()
        assert result == AWAIT_APPROVAL
        # The regeneration succeeded — the ack message was replaced with the draft preview
        assert 'case-based discussion draft ready' in sim.get_last_text().lower()
        assert 'refine this draft' in sim.get_last_text().lower()

    @pytest.mark.asyncio
    async def test_amend_mode_locks_long_text_to_existing_draft(self, thin_draft):
        from bot import AWAIT_APPROVAL, handle_amend_draft, handle_mid_conversation_text

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data.update({
            'last_amend_draft': {
                '_type': 'FORM',
                'form_type': thin_draft.form_type,
                'fields': dict(thin_draft.fields),
                'uuid': thin_draft.uuid,
            },
            'last_amend_case_text': 'Original filed CBD context',
            'last_amend_chosen_form': thin_draft.form_type,
        })

        amend_update = sim._make_callback_update('AMEND|amend')
        result = await handle_amend_draft(amend_update, context)

        assert result == AWAIT_APPROVAL
        assert context.user_data['amend_mode'] is True
        buttons = sim.get_last_buttons()
        assert ('📤 Save updated draft', 'APPROVE|draft') in buttons
        assert ('❌ Cancel amend', 'AMEND|cancel') in buttons
        assert ('📋 File another case', 'ACTION|file') not in buttons

        updated = thin_draft.model_copy(update={
            'fields': {**thin_draft.fields, 'reflection': 'Updated with leadership learning.'}
        })
        text_update = sim._make_text_update(
            'Add that I escalated to the consultant, delegated nursing tasks, and reflected on leadership.'
        )
        with patch('bot.classify_intent', new=AsyncMock(return_value='new_case')), \
             patch('bot.extract_cbd_data', new=AsyncMock(return_value=updated)), \
             patch('bot._process_case_text', new_callable=AsyncMock) as process_case:
            result = await handle_mid_conversation_text(text_update, context)

        assert result == AWAIT_APPROVAL
        assert process_case.await_count == 0
        assert context.user_data['draft_data']['fields']['reflection'] == 'Updated with leadership learning.'
        assert 'Original filed CBD context' in context.user_data['case_text']
        assert 'delegated nursing tasks' in context.user_data['case_text']
        buttons = sim.get_last_buttons()
        assert ('📤 Save updated draft', 'APPROVE|draft') in buttons
        assert ('📋 File another case', 'ACTION|file') not in buttons

    @pytest.mark.asyncio
    async def test_amend_mode_explicit_new_case_requires_choice(self, thin_draft):
        from bot import AWAIT_APPROVAL, AWAIT_FORM_CHOICE, handle_amend_draft, handle_mid_conversation_text

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data.update({
            'last_amend_draft': {
                '_type': 'FORM',
                'form_type': thin_draft.form_type,
                'fields': dict(thin_draft.fields),
                'uuid': thin_draft.uuid,
            },
            'last_amend_case_text': 'Original filed CBD context',
            'last_amend_chosen_form': thin_draft.form_type,
        })
        await handle_amend_draft(sim._make_callback_update('AMEND|amend'), context)

        new_case_text = 'This is a new case: 72F septic shock needing vasopressors and ICU escalation.'
        text_update = sim._make_text_update(new_case_text)
        with patch('bot.classify_intent', new=AsyncMock(return_value='new_case')):
            result = await handle_mid_conversation_text(text_update, context)

        assert result == AWAIT_APPROVAL
        assert context.user_data['amend_pending_feedback'] == new_case_text
        assert 'update this draft or start a new case' in sim.get_last_text().lower()
        buttons = sim.get_last_buttons()
        assert ('✏️ Update this draft', 'AMEND|update_current') in buttons
        assert ('📋 Start new case', 'AMEND|start_new') in buttons

        choice_update = sim._make_callback_update('AMEND|start_new')
        with patch('bot._process_case_text', new=AsyncMock(return_value=AWAIT_FORM_CHOICE)) as process_case:
            result = await handle_amend_draft(choice_update, context)

        assert result == AWAIT_FORM_CHOICE
        assert process_case.await_count == 1
        assert context.user_data.get('amend_mode') is None

    @pytest.mark.asyncio
    async def test_cancel_amend_clears_active_amend_state(self, thin_draft):
        from bot import handle_amend_draft

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data.update({
            'amend_mode': True,
            'draft_data': {
                '_type': 'FORM',
                'form_type': thin_draft.form_type,
                'fields': dict(thin_draft.fields),
                'uuid': thin_draft.uuid,
            },
            'case_text': 'Original filed CBD context',
            'chosen_form': thin_draft.form_type,
        })

        result = await handle_amend_draft(sim._make_callback_update('AMEND|cancel'), context)

        assert result == ConversationHandler.END
        assert context.user_data.get('amend_mode') is None
        assert context.user_data.get('draft_data') is None
        assert 'unchanged' in sim.get_last_text().lower()

    @pytest.mark.asyncio
    async def test_nudge_uses_llm_copy_when_available(self):
        from bot import _build_nudge_message

        stats = {"cases": 3, "gap": ("Mini-CEX", 28)}
        llm_copy = "📋 Solid week — three cases logged.\n\nMini-CEX gap is showing — just send me what happened next time."

        with patch('bot.generate_nudge_copy', new=AsyncMock(return_value=llm_copy)):
            text, keyboard = await _build_nudge_message(stats)

        assert text == llm_copy
        # Nudge no longer carries a re-prompt button — the user starts a case
        # by sending text/voice/photo/document directly.
        assert keyboard is None

    @pytest.mark.asyncio
    async def test_nudge_falls_back_to_static_when_llm_empty(self):
        from bot import _build_nudge_message

        stats = {"cases": 0, "gap": None}

        with patch('bot.generate_nudge_copy', new=AsyncMock(return_value="")):
            text, _ = await _build_nudge_message(stats)

        assert 'Portfolio check-in' in text
        assert 'No cases filed this week' in text

    @pytest.mark.asyncio
    async def test_successful_filing_includes_observation_line(self, thin_draft):
        from bot import handle_approval_approve

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        observation = "Fourth CBD this month — strong CBD coverage, time to look at DOPS."

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'success', 'filled': ['date'], 'skipped': [], 'method': 'deterministic',
             }), \
             patch('bot.get_case_history', new=AsyncMock(return_value=[{'form_type': 'CBD', 'filed_at': '2026-05-01', 'status': 'filed'}] * 4)), \
             patch('bot.summarise_recent_activity', new=AsyncMock(return_value=observation)):
            await handle_approval_approve(update, context)

        text = sim.get_last_text()
        assert observation in text
        assert '💡' in text

    @pytest.mark.asyncio
    async def test_failed_filing_skips_observation_line(self, thin_draft):
        from bot import handle_approval_approve

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        summarise_mock = AsyncMock(return_value="should not be called")

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'failed', 'filled': [], 'skipped': [], 'method': 'deterministic',
                 'error': 'Save was clicked, but I could not confirm the entry.',
             }), \
             patch('bot.summarise_recent_activity', new=summarise_mock):
            await handle_approval_approve(update, context)

        summarise_mock.assert_not_called()
        assert '💡' not in (sim.get_last_text() or '')

    @pytest.mark.asyncio
    async def test_menu_intent_short_text_routes_to_settings_for_stats(self):
        """show_stats intent now routes to the merged settings/status dashboard."""
        from bot import handle_case_input

        sim = BotSimulator()
        update = sim._make_text_update('how many cases this month')
        context = sim._make_context()

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 5, 'free'))), \
             patch('bot.classify_menu_intent', new=AsyncMock(return_value='show_stats')), \
             patch('bot.get_user_tier', new=AsyncMock(return_value='free')), \
             patch('bot.get_cases_this_month', new=AsyncMock(return_value=2)), \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.get_voice_profile', return_value=None):
            await handle_case_input(update, context)

        text = sim.get_last_text()
        assert 'your settings' in text.lower()
        # The merged dashboard surfaces plan + usage that used to be in /status.
        assert 'plan: free' in text.lower()
        assert '2/25 cases' in text.lower()

    @pytest.mark.asyncio
    async def test_conversational_router_shadow_logs_text_without_routing(self):
        from conversational_router import ConversationalIntent, RouterResult
        from bot import handle_case_input

        sim = BotSimulator()
        update = sim._make_text_update('how many cases this month')
        context = sim._make_context()
        shadow_result = RouterResult(
            intent=ConversationalIntent.FILE_TO_KAIZEN,
            confidence=0.99,
            signals={'action': 'file_to_kaizen', 'form_type': 'CBD'},
        )

        with patch('bot.route_message', return_value=shadow_result) as route_mock, \
             patch('bot.logger.info') as log_mock, \
             patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.classify_menu_intent', new=AsyncMock(return_value='show_stats')), \
             patch('bot.get_user_tier', new=AsyncMock(return_value='free')), \
             patch('bot.get_cases_this_month', new=AsyncMock(return_value=2)), \
             patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_voice_profile', return_value=None):
            result = await handle_case_input(update, context)
            await asyncio.sleep(0)

        assert result == ConversationHandler.END
        assert 'your settings' in sim.get_last_text().lower()
        route_mock.assert_called_once_with('how many cases this month')
        log_mock.assert_called_once()
        assert log_mock.call_args.args[0].startswith('Conversational router shadow route')

    @pytest.mark.asyncio
    async def test_mid_conversation_shadow_preserves_existing_decision(self):
        from conversational_router import ConversationalIntent, RouterResult
        from bot import AWAIT_APPROVAL, handle_mid_conversation_text

        sim = BotSimulator()
        update = sim._make_text_update('thanks')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']
        shadow_result = RouterResult(
            intent=ConversationalIntent.UNKNOWN,
            confidence=0.2,
            signals={},
            clarification='shadow only',
        )

        with patch('bot.route_message', return_value=shadow_result) as route_mock, \
             patch('bot.logger.info'), \
             patch('bot._load_draft', return_value=MagicMock()), \
             patch('bot.classify_intent', new=AsyncMock(return_value='chitchat')):
            result = await handle_mid_conversation_text(update, context)
            await asyncio.sleep(0)

        assert result == AWAIT_APPROVAL
        assert 'your draft is ready above' in sim.get_last_text().lower()
        route_mock.assert_called_once_with('thanks')

    @pytest.mark.asyncio
    async def test_menu_intent_short_text_routes_to_settings(self):
        from bot import handle_case_input

        sim = BotSimulator()
        update = sim._make_text_update('open settings please')
        context = sim._make_context()

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.classify_menu_intent', new=AsyncMock(return_value='open_settings')), \
             patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_voice_profile', return_value=None):
            await handle_case_input(update, context)

        text = sim.get_last_text()
        assert 'your settings' in text.lower()
        buttons = sim.get_last_buttons()
        assert any(data == 'ACTION|change_level' for _, data in buttons)

    @pytest.mark.asyncio
    async def test_menu_intent_clinical_text_skips_router(self, recommended_forms):
        from bot import handle_case_input

        sim = BotSimulator()
        update = sim._make_text_update(
            '45 year old male presented with chest pain, diagnosed as ACS, '
            'management included aspirin and referral to cardiology. '
            'I reflected on early ECG escalation.'
        )
        context = sim._make_context()

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.classify_menu_intent', new=AsyncMock(return_value='show_stats')) as menu_mock, \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.recommend_form_types', new=AsyncMock(return_value=recommended_forms)):
            await handle_case_input(update, context)

        menu_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_menu_intent_ambiguous_falls_through_to_case_flow(self, recommended_forms):
        from bot import handle_case_input

        sim = BotSimulator()
        # Needs enough content to clear the anti-fabrication gate
        # (_looks_like_clinical_case requires >= 6 words).
        update = sim._make_text_update('quick chest pain note: 45M ED, ACS managed')
        context = sim._make_context()

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 5, 'free'))), \
             patch('bot.classify_menu_intent', new=AsyncMock(return_value='ambiguous')), \
             patch('bot.classify_intent', new=AsyncMock(return_value='new_case')), \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.recommend_form_types', new=AsyncMock(return_value=recommended_forms)):
            await handle_case_input(update, context)

        text = sim.get_last_text() or ''
        assert 'fit your case' in text.lower() or 'recommend' in text.lower() or 'matching forms' in text.lower()

    @pytest.mark.asyncio
    async def test_wait_for_pictures_holds_case_bundle(self):
        from bot import AWAIT_CASE_INPUT, handle_case_input

        sim = BotSimulator()
        update = sim._make_text_update(
            'Patient presented with worsening shortness of breath and bilateral effusions. '
            'Please wait for pictures before drafting.'
        )
        context = sim._make_context()

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.classify_intent', new=AsyncMock(return_value='case')) as classify_mock, \
             patch('bot._process_case_text', new=AsyncMock()) as process_mock:
            result = await handle_case_input(update, context)

        assert result == AWAIT_CASE_INPUT
        assert context.user_data['pending_case_bundle']['parts'][0]['source'] == 'text'
        assert 'wait for the images/files' in sim.get_last_text().lower()
        classify_mock.assert_not_called()
        process_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait_for_pictures_then_auto_releases_on_first_image(self):
        from bot import AWAIT_CASE_INPUT, handle_case_input

        sim = BotSimulator()
        context = sim._make_context()
        initial = sim._make_text_update(
            'Patient presented with unstable tachyarrhythmia. Wait for images before drafting.'
        )

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.extract_from_image', new=AsyncMock(return_value='Image one text')), \
             patch('bot.classify_intent', new=AsyncMock(return_value='case')), \
             patch('bot._process_case_text', new=AsyncMock()) as process_mock:
            result = await handle_case_input(initial, context)
            assert result == AWAIT_CASE_INPUT

            # First photo triggers auto-release: goes to _process_case_text, not back to AWAIT_CASE_INPUT
            photo_update = sim._make_text_update('')
            photo = MagicMock()
            file_obj = MagicMock()
            file_obj.download_to_drive = AsyncMock()
            photo.get_file = AsyncMock(return_value=file_obj)
            photo_update.message.text = None
            photo_update.message.photo = [photo]

            result = await handle_case_input(photo_update, context)
            process_mock.assert_called_once()
            assert 'pending_case_bundle' not in context.user_data

    @pytest.mark.asyncio
    async def test_stale_pending_bundle_does_not_capture_new_case_text(self):
        from bot import handle_case_input

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['pending_case_bundle'] = {
            'parts': [
                {'source': 'text', 'text': 'Old case text waiting for images'},
                {'source': 'photo', 'text': 'Old image text'},
            ],
            'sources': ['text', 'photo'],
            'created_at': 1,
            'updated_at': 1,
        }
        context.user_data['pending_bundle_msg_id'] = 77
        context.user_data['pending_bundle_chat_id'] = sim.user_id
        context.user_data['last_bot_msg_id'] = 77
        context.user_data['last_bot_chat_id'] = sim.user_id
        update = sim._make_text_update(
            'So this is another case that I want to file. This patient presented '
            'with hypotension, tachycardia, resus care, sedation and cardioversion.'
        )

        async def fake_process(message, ctx, user_id, case_text, input_source):
            ctx.user_data['processed_case_text'] = case_text
            ctx.user_data['processed_input_source'] = input_source
            return ConversationHandler.END

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.classify_menu_intent', new=AsyncMock(return_value='ambiguous')), \
             patch('bot.classify_intent', new=AsyncMock(return_value='case')), \
             patch('bot._process_case_text', new=AsyncMock(side_effect=fake_process)) as process_mock:
            result = await handle_case_input(update, context)

        assert result == ConversationHandler.END
        process_mock.assert_called_once()
        assert 'Old case text' not in context.user_data['processed_case_text']
        assert 'another case' in context.user_data['processed_case_text']
        assert context.user_data['processed_input_source'] == 'text'
        assert 'pending_case_bundle' not in context.user_data
        assert 'pending_bundle_msg_id' not in context.user_data

    @pytest.mark.asyncio
    async def test_pending_bundle_photo_edits_bundle_anchor_not_old_case_anchor(self):
        from bot import handle_case_input

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['pending_case_bundle'] = {
            'parts': [{'source': 'text', 'text': 'Patient with unstable AF. Wait for images.'}],
            'sources': ['text'],
            'created_at': 1,
            'updated_at': 1,
        }
        context.user_data['last_bot_msg_id'] = 10
        context.user_data['last_bot_chat_id'] = sim.user_id
        context.user_data['pending_bundle_msg_id'] = 20
        context.user_data['pending_bundle_chat_id'] = sim.user_id

        photo_update = sim._make_text_update('')
        photo = MagicMock()
        file_obj = MagicMock()
        file_obj.download_to_drive = AsyncMock()
        photo.get_file = AsyncMock(return_value=file_obj)
        photo_update.message.text = None
        photo_update.message.photo = [photo]

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.extract_from_image', new=AsyncMock(return_value='Image findings show refractory AF')), \
             patch('bot._process_case_text', new=AsyncMock(return_value=ConversationHandler.END)):
            await handle_case_input(photo_update, context)

        edited_message_ids = [
            call.kwargs.get('message_id')
            for call in context.bot.edit_message_text.await_args_list
        ]
        assert edited_message_ids
        assert set(edited_message_ids) == {20}

    @pytest.mark.asyncio
    async def test_pending_case_bundle_done_processes_combined_case(self):
        from bot import AWAIT_APPROVAL, handle_case_input

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['pending_case_bundle'] = {
            'parts': [
                {'source': 'text', 'text': 'Initial respiratory case text'},
                {'source': 'photo', 'text': 'Image findings show bilateral effusions'},
            ],
            'sources': ['text', 'photo'],
        }
        update = sim._make_text_update('done')

        async def fake_process(message, ctx, user_id, case_text, input_source):
            ctx.user_data['processed_case_text'] = case_text
            ctx.user_data['processed_input_source'] = input_source
            return AWAIT_APPROVAL

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot._process_case_text', new=AsyncMock(side_effect=fake_process)):
            result = await handle_case_input(update, context)

        assert result == AWAIT_APPROVAL
        assert 'Initial respiratory case text' in context.user_data['processed_case_text']
        assert 'Image findings show bilateral effusions' in context.user_data['processed_case_text']
        assert context.user_data['processed_input_source'] == 'mixed'
        assert 'pending_case_bundle' not in context.user_data

    @pytest.mark.asyncio
    async def test_approval_photo_regenerates_existing_draft_with_image_text(self):
        from bot import AWAIT_APPROVAL, _store_draft, handle_approval_media_feedback
        from models import CBDData

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['case_text'] = 'Original case text'
        context.user_data['case_input_source'] = 'text'
        _store_draft(context, CBDData(patient_presentation='Shortness of breath'))

        update = sim._make_text_update('')
        photo = MagicMock()
        file_obj = MagicMock()
        file_obj.download_to_drive = AsyncMock()
        photo.get_file = AsyncMock(return_value=file_obj)
        update.message.photo = [photo]
        update.message.text = None

        updated = CBDData(
            patient_presentation='Shortness of breath with bilateral effusions',
            clinical_reasoning='Image evidence added',
        )

        with patch('bot.extract_from_image', new=AsyncMock(return_value='Bilateral pleural effusions on imaging')), \
             patch('bot.extract_cbd_data', new=AsyncMock(return_value=updated)) as extract_mock, \
             patch('bot.get_voice_profile', return_value=None):
            result = await handle_approval_media_feedback(update, context)

        assert result == AWAIT_APPROVAL
        assert 'Bilateral pleural effusions on imaging' in context.user_data['case_text']
        assert context.user_data['case_input_source'] == 'mixed'
        extract_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reuse_request_routes_to_last_filed_case_not_extraction(self):
        """A typed 'use the same case for DOPS' must reuse the previously filed
        case_text and never feed the instruction to the extractor — otherwise
        the LLM fabricates clinical fields. See feedback-no-fabrication memory."""
        from bot import handle_case_input

        sim = BotSimulator()
        update = sim._make_text_update('use the same case for DOPS')
        context = sim._make_context()
        # Simulate a prior successful filing.
        context.user_data['last_filed_case_text'] = (
            '45M with chest pain, troponin positive, managed as ACS. Reflected '
            'on early ECG recognition and escalation.'
        )
        context.user_data['last_filed_form_type'] = 'CBD'

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 5, 'free'))), \
             patch('bot.recommend_form_types', new=AsyncMock(return_value=[])) as recommend, \
             patch('bot._analyse_selected_form', new=AsyncMock()) as analyse:
            await handle_case_input(update, context)

        # Recommender must NOT be called — explicit form (DOPS) was named.
        recommend.assert_not_awaited()
        # The case_text in user_data must be the previously filed case, NOT the instruction.
        assert 'chest pain' in (context.user_data.get('case_text') or '').lower()
        assert 'use the same case' not in (context.user_data.get('case_text') or '').lower()
        # Form is pre-selected to DOPS.
        assert context.user_data.get('chosen_form') == 'DOPS'

    @pytest.mark.asyncio
    async def test_thin_input_blocked_before_extraction(self):
        """A too-short non-clinical message routed into _process_case_text
        must be blocked by the anti-fabrication gate — no recommender, no
        extractor calls, and the user is asked for real clinical detail."""
        from bot import _process_case_text

        sim = BotSimulator()
        update = sim._make_text_update('please file a case')
        context = sim._make_context()
        user_id = sim.user_id

        with patch('bot.recommend_form_types', new=AsyncMock()) as recommend, \
             patch('bot._analyse_selected_form', new=AsyncMock()) as analyse:
            result = await _process_case_text(
                update.message, context, user_id, 'please file a case', 'text'
            )

        assert result == ConversationHandler.END
        # Neither the recommender nor the extractor should fire — the input is
        # below the minimum-content threshold.
        recommend.assert_not_awaited()
        analyse.assert_not_awaited()
        text = (sim.get_last_text() or '').lower()
        assert 'clinical detail' in text or 'what happened' in text


class TestRecentPortfolioFixes:
    @pytest.mark.asyncio
    async def test_setup_curriculum_completion_offers_file_first_case(self):
        from bot import setup_curriculum

        sim = BotSimulator()
        update = sim._make_callback_update('SETUP_CURRICULUM|2025')
        context = sim._make_context()

        with patch('bot.store_curriculum'):
            result = await setup_curriculum(update, context)

        assert result == ConversationHandler.END
        assert 'setup complete' in sim.get_last_text().lower()
        # Post-setup completion no longer has a "File first case" button —
        # the user is invited to send their case directly.
        assert 'send your first case' in sim.get_last_text().lower()

    def test_quick_improve_keyboard_can_be_locked_after_one_use(self):
        from bot import _build_approval_keyboard

        keyboard = _build_approval_keyboard(improved_once=True)
        buttons = [(b.text, b.callback_data) for row in keyboard.inline_keyboard for b in row]

        # After one use, the improve button is removed entirely
        assert ('Improved once ✅', 'IMPROVE|used') not in buttons
        assert ('✨ Quick improve', 'IMPROVE|reflection') not in buttons
        assert ('📤 Save as draft', 'APPROVE|draft') in buttons

    def test_dops_pre_file_guard_blocks_blank_voice_draft(self):
        from bot import _pre_file_missing_fields

        missing = _pre_file_missing_fields('DOPS', {
            'end_date': '14/5/2026',
            'stage_of_training': 'Higher/ST4-ST6',
        })

        assert 'Procedure / procedural skill' in missing
        assert 'Indication' in missing
        assert 'Trainee Performance' in missing

    def test_dops_pre_file_guard_blocks_thin_normalised_narrative(self):
        """The real dogfood bug: thin voice notes can produce a label-only
        case_observed like 'Procedure observed: DC cardioversion'. The
        existing guard fell through to that field for the Indication check
        and passed, so a near-empty Kaizen draft slipped through. The
        strengthened guard must catch it.
        """
        from bot import _pre_file_missing_fields

        missing = _pre_file_missing_fields('DOPS', {
            'date_of_encounter': '2026-05-19',
            'stage_of_training': 'Higher/ST4-ST6',
            'clinical_setting': 'Emergency Department',
            'procedure_name': 'DC cardioversion',
            # Indication and trainee performance left blank — but
            # case_observed is populated with a label-only stub the way the
            # normaliser builds it from a procedure-only DOPS draft.
            'case_observed': 'Procedure observed: DC cardioversion',
        })

        assert 'Indication' in missing
        assert 'Trainee Performance' in missing

    def test_dops_pre_file_guard_blocks_incoherent_reflection(self):
        """A two-word fragment reflection is worthless to an assessor. The
        guard surfaces it so the user adds a real reflection before save.
        """
        from bot import _pre_file_missing_fields

        missing = _pre_file_missing_fields('DOPS', {
            'date_of_encounter': '2026-05-19',
            'stage_of_training': 'Higher/ST4-ST6',
            'procedure_name': 'DC cardioversion',
            'indication': 'Unstable AF with RVR and hypotension requiring emergency cardioversion.',
            'trainee_performance': (
                'I led the synchronised cardioversion under ketamine sedation, '
                'delivered three escalating shocks, escalated to ITU.'
            ),
            'reflection': 'ok done',
        })

        assert any('Reflection' in m for m in missing), missing

    def test_post_filing_keyboard_offers_same_case_another_wpba(self):
        from bot import _build_post_filing_keyboard

        keyboard = _build_post_filing_keyboard('CBD', 'success', same_case_available=True)
        buttons = [(b.text, b.callback_data) for row in keyboard.inline_keyboard for b in row]

        assert ('🔁 Same case, another WPBA', 'ACTION|same_case_another') in buttons
        assert ('📋 File another case', 'ACTION|file') in buttons

class TestOnboardingFrictionPatch:
    @pytest.mark.asyncio
    async def test_setup_password_auto_detects_training_level(self):
        from bot import setup_password

        sim = BotSimulator()
        update = sim._make_text_update('safe-password')
        update.message.delete = AsyncMock()
        context = sim._make_context()
        context.user_data['setup_username'] = 'doctor@example.com'

        with patch('bot._test_kaizen_login', new_callable=AsyncMock, return_value='hst'), \
             patch('bot.store_credentials') as store_credentials, \
             patch('bot.get_training_level', return_value=None), \
             patch('bot.store_training_level') as store_training_level, \
             patch('bot.get_curriculum', return_value=None), \
             patch('bot.store_curriculum') as store_curriculum:
            result = await setup_password(update, context)

        assert result == ConversationHandler.END
        store_credentials.assert_called_once()
        store_training_level.assert_called_once_with(sim.user_id, 'HIGHER')
        store_curriculum.assert_called_once_with(sim.user_id, '2025')
        # Auto-detected role shown in welcome message
        assert 'higher specialist' in sim.get_last_text().lower()


class TestTrainingStageGroups:
    def test_unknown_training_level_is_displayed_as_unknown(self):
        from bot import _settings_view_components

        with patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.get_training_level', return_value=None), \
             patch('bot.get_voice_profile', return_value=None):
            text, _ = _settings_view_components(123)

        assert 'Training stage: Unknown' in text

    @pytest.mark.asyncio
    async def test_training_level_options_use_kaizen_stage_groups(self):
        from bot import handle_action_button

        sim = BotSimulator()
        update = sim._make_callback_update('ACTION|change_level')
        context = sim._make_context()

        await handle_action_button(update, context)

        buttons = sim.get_last_buttons()
        assert ('ACCS (ST1–2)', 'SETLEVEL|ACCS') in buttons
        assert ('Intermediate (ST3)', 'SETLEVEL|INTERMEDIATE') in buttons
        assert ('Higher (ST4–6)', 'SETLEVEL|HIGHER') in buttons

    def test_settings_layout_prioritises_voice_profile(self):
        from bot import _settings_view_components

        with patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.get_training_level', return_value=None), \
             patch('bot.get_voice_profile', return_value=None):
            text, keyboard = _settings_view_components(123)

        buttons = [(b.text, b.callback_data) for row in keyboard.inline_keyboard for b in row]
        assert buttons[0] == ('⭐ Set up voice profile', 'ACTION|voice')
        assert ('📚 Curriculum: 2025 Update', 'ACTION|change_curriculum') in buttons
        assert 'Set this once so drafts sound like you' in text


class TestImageOCRProgress:
    """Image OCR progress UX: one calm replacement message, never a stacked
    "Reading image…\nStill reading…" bubble.

    Background: the original implementation appended "Still reading…" to the
    initial ack on the same message, which read like the bot repeating itself.
    The contract now is: a single ack ("Reading image…"), optionally replaced
    by a single calm reassurance ("Still reading…") if OCR is slow, then
    replaced again by the success/error message.
    """

    @pytest.mark.asyncio
    async def test_progress_helper_skips_edit_when_ocr_finishes_before_delay(self):
        from bot import _run_image_progress

        ack = MagicMock()
        ack.edit_text = AsyncMock()
        ocr_done = asyncio.Event()
        ocr_done.set()

        await _run_image_progress(
            ack,
            still_text="📷 Still reading…",
            delay_seconds=0.05,
            ocr_done=ocr_done,
        )

        ack.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_progress_helper_emits_single_replacement_when_ocr_is_slow(self):
        from bot import _run_image_progress

        ack = MagicMock()
        ack.edit_text = AsyncMock()
        ocr_done = asyncio.Event()

        await _run_image_progress(
            ack,
            still_text="📷 Still reading…",
            delay_seconds=0.02,
            ocr_done=ocr_done,
        )

        ack.edit_text.assert_awaited_once()
        args, kwargs = ack.edit_text.await_args
        edited = args[0] if args else kwargs.get("text", "")
        assert edited == "📷 Still reading…"
        assert "\n" not in edited
        assert "Reading image" not in edited
        assert "Reading images" not in edited

    @pytest.mark.asyncio
    async def test_progress_helper_cancellation_is_silent(self):
        from bot import _run_image_progress

        ack = MagicMock()
        ack.edit_text = AsyncMock()
        ocr_done = asyncio.Event()

        task = asyncio.create_task(
            _run_image_progress(
                ack,
                still_text="📷 Still reading…",
                delay_seconds=10,
                ocr_done=ocr_done,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        results = await asyncio.gather(task, return_exceptions=True)

        assert results == [None]
        ack.edit_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_progress_helper_swallows_edit_failures(self):
        """A failed reassurance edit must not crash the parent OCR flow."""
        from bot import _run_image_progress

        ack = MagicMock()
        ack.edit_text = AsyncMock(side_effect=RuntimeError("telegram unreachable"))
        ocr_done = asyncio.Event()

        # Should complete without raising.
        await _run_image_progress(
            ack,
            still_text="📷 Still reading…",
            delay_seconds=0.02,
            ocr_done=ocr_done,
        )

    @pytest.mark.asyncio
    async def test_fast_photo_ocr_never_shows_still_reading_message(self):
        from bot import handle_case_input

        sim = BotSimulator()
        context = sim._make_context()

        photo_update = sim._make_text_update('')
        photo = MagicMock()
        file_obj = MagicMock()
        file_obj.download_to_drive = AsyncMock()
        photo.get_file = AsyncMock(return_value=file_obj)
        photo_update.message.text = None
        photo_update.message.photo = [photo]

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.extract_from_image', new=AsyncMock(return_value='Chest pain with ECG changes')), \
             patch('bot._process_case_text', new=AsyncMock()):
            await handle_case_input(photo_update, context)

        ack_texts = [text for _, text, _ in sim.messages_sent if text]
        assert any("Reading image" in t for t in ack_texts), (
            f"Expected initial 'Reading image…' ack, got: {ack_texts}"
        )
        for text in ack_texts:
            assert "Still reading" not in text, (
                f"Fast OCR must not emit 'Still reading…' — saw: {text!r}"
            )
            assert "Reading image…\n" not in text, (
                f"Ack must not stack with extra lines — saw: {text!r}"
            )


class TestMessageStandardCopy:
    """Locks the mode-aware error recovery rule from `WORKFLOWS.md`.

    The cause clause is identical across flows; only the recovery clause changes.
    These tests exercise the actual handlers so a regression in either the cause
    or the recovery copy fails here, not in production.

    new case        → "describe the case in text"
    template review → "Try again or send text."
    existing draft  → "Type your feedback instead."
    """

    @pytest.mark.asyncio
    async def test_new_case_voice_error_uses_case_recovery_clause(self):
        from bot import AWAIT_CASE_INPUT, handle_case_input

        sim = BotSimulator()
        context = sim._make_context()
        update = sim._make_text_update('')
        voice = MagicMock()
        file_obj = MagicMock()
        file_obj.download_to_drive = AsyncMock()
        voice.get_file = AsyncMock(return_value=file_obj)
        update.message.text = None
        update.message.voice = voice

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.transcribe_voice', new=AsyncMock(side_effect=RuntimeError('whisper down'))):
            result = await handle_case_input(update, context)

        assert result == AWAIT_CASE_INPUT
        edits = [text for kind, text, _ in sim.messages_sent if kind == 'edit' and text]
        assert edits, f"Expected an error edit, got: {sim.messages_sent}"
        final = edits[-1]
        assert "Couldn't transcribe voice note" in final, final
        assert "describe the case in text" in final, final

    @pytest.mark.asyncio
    async def test_template_review_image_error_uses_send_text_recovery(self):
        from bot import AWAIT_TEMPLATE_REVIEW, handle_template_review_media

        sim = BotSimulator()
        context = sim._make_context()
        update = sim._make_text_update('')
        photo = MagicMock()
        file_obj = MagicMock()
        file_obj.download_to_drive = AsyncMock()
        photo.get_file = AsyncMock(return_value=file_obj)
        update.message.text = None
        update.message.photo = [photo]
        update.message.voice = None
        update.message.video = None
        update.message.document = None

        with patch('bot.extract_from_image', new=AsyncMock(side_effect=RuntimeError('vision down'))):
            result = await handle_template_review_media(update, context)

        assert result == AWAIT_TEMPLATE_REVIEW
        edits = [text for kind, text, _ in sim.messages_sent if kind == 'edit' and text]
        assert edits, f"Expected an error edit, got: {sim.messages_sent}"
        final = edits[-1]
        assert "Couldn't read image" in final, final
        assert "Try again or send text" in final, final
        assert "Type your feedback" not in final, final

    @pytest.mark.asyncio
    async def test_approval_media_voice_error_uses_feedback_recovery(self):
        from bot import AWAIT_APPROVAL, _store_draft, handle_approval_media_feedback
        from models import CBDData

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['case_text'] = 'Original case text'
        context.user_data['case_input_source'] = 'text'
        _store_draft(context, CBDData(patient_presentation='Chest pain'))

        update = sim._make_text_update('')
        voice = MagicMock()
        file_obj = MagicMock()
        file_obj.download_to_drive = AsyncMock()
        voice.get_file = AsyncMock(return_value=file_obj)
        update.message.text = None
        update.message.voice = voice
        update.message.photo = []
        update.message.video = None
        update.message.document = None

        with patch('bot.transcribe_voice', new=AsyncMock(side_effect=RuntimeError('whisper down'))):
            result = await handle_approval_media_feedback(update, context)

        assert result == AWAIT_APPROVAL
        edits = [text for kind, text, _ in sim.messages_sent if kind == 'edit' and text]
        assert edits, f"Expected an error edit, got: {sim.messages_sent}"
        final = edits[-1]
        assert "Couldn't transcribe voice note" in final, final
        assert "Type your feedback instead" in final, final
        assert "describe the case in text" not in final, final

    def test_bot_source_has_no_deprecated_recovery_wording(self):
        """Static lint: deprecated copy variants must not creep back in.

        Each entry is (deprecated literal, where it used to live, replacement).
        The lint fails fast in CI so a copy-paste regression doesn't ship.
        """
        import pathlib
        bot_src = pathlib.Path(__file__).resolve().parent.parent / "bot.py"
        text = bot_src.read_text(encoding="utf-8")

        deprecated_lines = [
            "Try a clearer photo or text.",
            "Try a voice note or text.",
            "Try text instead.",
            "send it again or type the case as text.",
        ]
        offenders = [needle for needle in deprecated_lines if needle in text]
        assert not offenders, (
            "Deprecated recovery wording resurfaced in bot.py — normalise to "
            "the mode-aware recovery clauses documented in WORKFLOWS.md "
            "(User-Facing Message Standard). Offenders: " + ", ".join(offenders)
        )

    def test_voice_ack_normalised_across_clinical_flows(self):
        """Voice acks for clinical input always read 'Transcribing voice note…'.

        A short 'Transcribing…' bubble is allowed only in the voice-profile
        setup flow, but that surface was normalised too — so the bare ack
        should no longer appear anywhere in bot.py.
        """
        import pathlib
        bot_src = pathlib.Path(__file__).resolve().parent.parent / "bot.py"
        text = bot_src.read_text(encoding="utf-8")

        # The bare "🎙️ Transcribing…" literal is a regression marker — every
        # voice ack should now carry the "voice note" noun.
        assert '"🎙️ Transcribing…"' not in text, (
            'Found bare "🎙️ Transcribing…" ack — voice acks should read '
            '"🎙️ Transcribing voice note…" per WORKFLOWS.md.'
        )
