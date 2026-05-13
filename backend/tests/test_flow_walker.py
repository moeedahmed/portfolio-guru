"""Flow walker tests for the Portfolio Guru bot."""

from __future__ import annotations

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
        assert any(data == 'ACTION|file' for _, data in sim.get_last_buttons())

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
        assert 'FORM|CBD' in button_data
        assert 'FORM|show_all' in button_data
        assert context.user_data['case_text'] == SAMPLE_CASES['valid']

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
    async def test_template_review_buttons_progress(self, thin_draft):
        from bot import AWAIT_APPROVAL, AWAIT_TEMPLATE_REVIEW, handle_callback, handle_form_choice

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']

        update = sim._make_callback_update('FORM|CBD')
        with patch('bot._analyse_selected_form', new_callable=AsyncMock, return_value=thin_draft),              patch('bot._missing_template_fields', return_value=([{'label': 'Supervisor'}], [], [])):
            result = await handle_form_choice(update, context)

        context.user_data['pending_draft_data'] = {
            '_type': 'FORM',
            'form_type': 'CBD',
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        assert result == AWAIT_TEMPLATE_REVIEW
        assert {'ACTION|continue_thin'} <= {data for _, data in sim.get_last_buttons()}

        sim.clear_messages()
        update = sim._make_callback_update('ACTION|continue_thin')
        result = await handle_callback(update, context)
        assert result == AWAIT_APPROVAL
        button_data = {data for _, data in sim.get_last_buttons()}
        assert {'APPROVE|draft', 'IMPROVE|reflection', 'EDIT|draft', 'CANCEL|draft'} <= button_data
        assert 'APPROVE|submit' not in button_data

    @pytest.mark.asyncio
    async def test_optional_missing_fields_do_not_block_draft_preview(self, thin_draft):
        from bot import AWAIT_APPROVAL, handle_form_choice

        sim = BotSimulator()
        update = sim._make_callback_update('FORM|CBD')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']

        optional_field = {'label': 'Supervisor', 'key': 'supervisor_name'}
        with patch('bot._analyse_selected_form', new_callable=AsyncMock, return_value=thin_draft), \
             patch('bot._missing_template_fields', return_value=([], [optional_field], [])):
            result = await handle_form_choice(update, context)

        assert result == AWAIT_APPROVAL
        assert 'draft ready' in sim.get_last_text().lower()
        assert any(data == 'APPROVE|draft' for _, data in sim.get_last_buttons())

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
            'INFO|what', 'FORM|show_all', 'FORM|disabled', 'FORM|switch_curriculum', 'FORM|back',
            'CANCEL|form', 'CANCEL|draft', 'APPROVE|draft', 'EDIT|draft',
            'FIELD|date_of_encounter', 'SET_CURRICULUM|2025', 'LEVEL|ST5',
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
                if callback.startswith('ACTION|') and callback not in {'ACTION|file', 'ACTION|reset', 'ACTION|cancel', 'ACTION|add_detail', 'ACTION|continue_thin', 'ACTION|retry_filing'}:
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
        assert any(data == 'ACTION|file' for _, data in sim.get_last_buttons())

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
        assert any(data == 'ACTION|file' for _, data in sim.get_last_buttons())

    @pytest.mark.asyncio
    async def test_expired_draft_recovery_updates_latest_template_message(self, thin_draft):
        from bot import AWAIT_TEMPLATE_REVIEW, handle_approval_edit

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

        with patch('bot.has_credentials', return_value=True),              patch('bot.get_training_level', return_value='ST5'),              patch('bot._missing_template_fields', return_value=([{'label': 'Supervisor'}], [], [])):
            result = await handle_approval_edit(update, context)

        assert result == AWAIT_TEMPLATE_REVIEW
        assert sim.messages_sent[-1][0] == 'bot_edit'
        assert 'still in progress' in sim.messages_sent[-2][1].lower()
        assert {'ACTION|continue_thin'} <= {data for _, data in sim.get_last_buttons()}

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

        with patch('bot.get_credentials', return_value=('user', 'pass')),              patch('bot.has_credentials', return_value=True),              patch('bot.get_training_level', return_value='ST5'),              patch('bot._missing_template_fields', return_value=([], [], [])):
            result = await handle_approval_approve(update, context)

        assert result == AWAIT_APPROVAL
        assert sim.messages_sent[-1][0] == 'bot_edit'
        assert 'still in progress' in sim.messages_sent[-2][1].lower()
        assert {'APPROVE|draft', 'EDIT|draft'} <= {data for _, data in sim.get_last_buttons()}

    @pytest.mark.asyncio
    async def test_filing_completion_updates_current_message(self, thin_draft):
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
        assert sim.messages_sent[-1][0] == 'edit'
        assert 'draft saved' in sim.get_last_text().lower()
        buttons = sim.get_last_buttons()
        assert buttons[0] == ('File another case', 'ACTION|file')
        assert ('Worked', 'FEEDBACK|good|CBD|success') in buttons
        assert ("Didn't work", 'FEEDBACK|bad|CBD|success') in buttons
        assert ('More', 'ACTION|post_file_more|CBD|success') in buttons

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
        assert ('Worked', 'FEEDBACK|good|CBD|partial') in buttons
        assert ("Didn't work", 'FEEDBACK|bad|CBD|partial') in buttons
        assert ('More', 'ACTION|post_file_more|CBD|partial') in buttons

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
        assert ('Try again', 'ACTION|retry_filing') in buttons
        assert ('File another case', 'ACTION|file') in buttons
        assert ('Something missing?', 'FILING|feedback|CBD') in buttons
        assert ('Status', 'ACTION|status') in buttons
