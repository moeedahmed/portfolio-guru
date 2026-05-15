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
        # Post-cancel for a connected user shows NO inline buttons — the next
        # action is to type/send a fresh case, and Settings etc. live in the
        # Telegram Menu (☰).
        assert sim.get_last_buttons() == []

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
        assert buttons[0] == ('📋 File another case', 'ACTION|file')
        assert ('👍 It worked', 'FEEDBACK|good|CBD|success') in buttons
        assert ("👎 Didn't work", 'FEEDBACK|bad|CBD|success') in buttons
        assert ('⋯ More options', 'ACTION|post_file_more|CBD|success') in buttons

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
        assert ('👍 It worked', 'FEEDBACK|good|CBD|partial') in buttons
        assert ("👎 Didn't work", 'FEEDBACK|bad|CBD|partial') in buttons
        assert ('⋯ More options', 'ACTION|post_file_more|CBD|partial') in buttons

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
    async def test_natural_language_edit_with_no_match_asks_to_rephrase(self, thin_draft):
        from bot import handle_mid_conversation_text, AWAIT_APPROVAL

        sim = BotSimulator()
        update = sim._make_text_update('change the doodad to flibble')
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
             patch('bot.extract_field_updates', new=AsyncMock(return_value={})):
            result = await handle_mid_conversation_text(update, context)

        text = (sim.get_last_text() or '').lower()
        assert result == AWAIT_APPROVAL
        assert "couldn't tell" in text or "rephrase" in text
        assert "new case" not in text

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
        assert '2/5 cases' in text.lower()

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

        assert ('Improved once ✅', 'IMPROVE|used') in buttons
        assert ('✨ Quick improve', 'IMPROVE|reflection') not in buttons

    def test_dops_pre_file_guard_blocks_blank_voice_draft(self):
        from bot import _pre_file_missing_fields

        missing = _pre_file_missing_fields('DOPS', {
            'end_date': '14/5/2026',
            'stage_of_training': 'Higher/ST4-ST6',
        })

        assert 'Procedure / procedural skill' in missing
        assert 'Indication' in missing
        assert 'Trainee Performance' in missing

    def test_post_filing_keyboard_offers_same_case_another_wpba(self):
        from bot import _build_post_filing_keyboard

        keyboard = _build_post_filing_keyboard('CBD', 'success', same_case_available=True)
        buttons = [(b.text, b.callback_data) for row in keyboard.inline_keyboard for b in row]

        assert ('🔁 Same case, another WPBA', 'ACTION|same_case_another') in buttons
        assert ('📋 File new case', 'ACTION|file') in buttons

class TestOnboardingFrictionPatch:
    @pytest.mark.asyncio
    async def test_setup_password_skips_training_level_and_goes_to_file_first_case(self):
        from bot import setup_password

        sim = BotSimulator()
        update = sim._make_text_update('safe-password')
        update.message.delete = AsyncMock()
        context = sim._make_context()
        context.user_data['setup_username'] = 'doctor@example.com'

        with patch('bot._test_kaizen_login', new_callable=AsyncMock, return_value=True), \
             patch('bot.store_credentials') as store_credentials, \
             patch('bot.get_training_level', return_value=None), \
             patch('bot.store_training_level') as store_training_level, \
             patch('bot.get_curriculum', return_value=None), \
             patch('bot.store_curriculum') as store_curriculum:
            result = await setup_password(update, context)

        assert result == ConversationHandler.END
        store_credentials.assert_called_once()
        store_training_level.assert_not_called()
        store_curriculum.assert_called_once_with(sim.user_id, '2025')
        # Post-setup completion invites the user to send their first case directly
        # rather than tapping a re-prompt button.
        assert 'send your first case' in sim.get_last_text().lower()


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
