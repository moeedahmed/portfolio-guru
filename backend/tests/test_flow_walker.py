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


def _last_button_rows(sim: BotSimulator):
    for _, _, markup in reversed(sim.messages_sent):
        if markup and hasattr(markup, "inline_keyboard"):
            return [
                [(button.text, button.callback_data) for button in row if button.callback_data]
                for row in markup.inline_keyboard
            ]
    return []


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
        button_data = {data for _, data in sim.get_last_buttons()}
        assert 'FORM|PROC_LOG' in button_data
        assert 'FORM|show_all' in button_data
        recommend.assert_not_awaited()
        analyse.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_explicit_qiat_from_evidence_can_still_choose_another_form(self):
        from bot import AWAIT_FORM_CHOICE, handle_case_input

        sim = BotSimulator()
        update = sim._make_text_update(
            "Quality improvement assessment from a run chart: ED time-to-antibiotics improved after a sepsis huddle."
        )
        context = sim._make_context()

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.recommend_form_types', new_callable=AsyncMock) as recommend, \
             patch('bot.check_can_file', new_callable=AsyncMock, return_value=(True, 0, 5, 'free')):
            result = await handle_case_input(update, context)

        assert result == AWAIT_FORM_CHOICE
        assert context.user_data['chosen_form'] == 'QIAT'
        button_data = {data for _, data in sim.get_last_buttons()}
        assert 'FORM|QIAT' in button_data
        assert 'FORM|show_all' in button_data
        recommend.assert_not_awaited()

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
        # Required-but-missing fields surface inline with the missing marker
        # next to each field label; the universal gate at file-time catches
        # them too. The verbose "🧩 Missing details" block has been removed.
        assert 'Stage of Training' in text
        assert 'Missing details' not in text
        assert 'Missing required fields' not in text
        assert 'Blank fields are left blank rather than invented' not in text

    @pytest.mark.asyncio
    async def test_draft_preview_orders_body_before_reply_hint(self, thin_draft):
        """Portfolio body sits cleanly before the reply hint without any
        intermediate 'missing details' instruction block."""
        from bot import _DRAFT_DIVIDER, handle_form_choice

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']
        update = sim._make_callback_update('FORM|CBD')

        with patch(
            'bot._analyse_selected_form',
            new_callable=AsyncMock,
            return_value=thin_draft,
        ):
            await handle_form_choice(update, context)

        text = sim.get_last_text()

        first_field_pos = text.index('📅')
        assert '📋 *Draft preview*' not in text
        assert text.count(_DRAFT_DIVIDER) == 0
        assert '🧩 *Missing details*' not in text

        curriculum_pos = text.index('📚 *Curriculum:*')
        reply_hint_pos = text.index('💬 Reply to refine this draft')
        assert first_field_pos < curriculum_pos < reply_hint_pos

    @pytest.mark.asyncio
    async def test_draft_preview_keeps_why_this_form_compact(self, thin_draft):
        """The form rationale must appear after the draft body as a user-facing
        sentence, with no heavy divider sandwiching the draft."""
        from bot import _DRAFT_DIVIDER, handle_form_choice
        from extractor import FORM_UUIDS
        from models import FormTypeRecommendation

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']
        # Stash a recommendation reason so _chosen_form_reason returns it and
        # the preview renders the rationale footer.
        context.user_data['form_recommendations'] = [
            FormTypeRecommendation(
                form_type='CBD',
                rationale='The case is a reflective discussion of one patient.',
                uuid=FORM_UUIDS['CBD'],
            ),
        ]
        update = sim._make_callback_update('FORM|CBD')

        with patch(
            'bot._analyse_selected_form',
            new_callable=AsyncMock,
            return_value=thin_draft,
        ):
            await handle_form_choice(update, context)

        text = sim.get_last_text()

        assert _DRAFT_DIVIDER not in text
        first_field_pos = text.index('📅')
        curriculum_pos = text.index('📚 *Curriculum:*')
        assert first_field_pos < curriculum_pos
        assert '*Why this form:*' not in text

    @pytest.mark.asyncio
    async def test_draft_preview_sanitises_lat_management_rationale(self, thin_draft):
        """LAT/management rationales must not leak model-justification phrasing
        ('the trainee', '(EPIC equivalent)', '...framework assessed by a LAT')
        into the user-facing footer."""
        from bot import _DRAFT_DIVIDER, handle_form_choice
        from extractor import FORM_UUIDS
        from models import FormDraft, FormTypeRecommendation

        lat_draft = FormDraft(
            form_type='LAT',
            uuid=FORM_UUIDS.get('LAT', 'uuid-lat'),
            fields={
                'date_of_encounter': '2026-03-17',
                'clinical_setting': 'ED',
                'reflection': 'Led a busy shift; juniors needed prioritisation help.',
                'curriculum_links': ['SLO11'],
                'key_capabilities': ['SLO11 KC1: Lead a team safely'],
            },
        )

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']
        context.user_data['form_recommendations'] = [
            FormTypeRecommendation(
                form_type='LAT',
                rationale=(
                    "The trainee actively performed a shift leadership and "
                    "coordination role (EPIC/flow coordinator equivalent), "
                    "directing juniors and escalating to site management, "
                    "which fits the EMLeaders framework assessed by a LAT."
                ),
                uuid=FORM_UUIDS.get('LAT', 'uuid-lat'),
            ),
        ]
        update = sim._make_callback_update('FORM|LAT')

        with patch(
            'bot._analyse_selected_form',
            new_callable=AsyncMock,
            return_value=lat_draft,
        ):
            await handle_form_choice(update, context)

        text = sim.get_last_text()

        assert _DRAFT_DIVIDER not in text
        assert "I've treated this as a Leadership Assessment Tool:" not in text
        # None of the internal/model-flavoured phrasing should reach the user.
        assert 'the trainee' not in text.lower()
        assert 'EPIC' not in text
        assert 'EMLeaders' not in text
        assert 'assessed by a LAT' not in text
        assert 'assessed by LAT' not in text
        # The instruction is preserved but reads as one concise line.
        assert '💬 Reply to refine this draft, or save/cancel before sending a new case.' in text

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
    async def test_short_self_directed_learning_request_opens_sdl_choice(self):
        from bot import AWAIT_FORM_CHOICE, _process_case_text

        sim = BotSimulator()
        context = sim._make_context()
        update = sim._make_text_update('self directed learning')

        result = await _process_case_text(
            update.message,
            context,
            update.effective_user.id,
            update.message.text,
            'text',
        )

        assert result == AWAIT_FORM_CHOICE
        text = sim.get_last_text()
        assert 'Self-directed Learning' in text
        assert 'clinical detail' not in text.lower()
        assert ('✅ Draft Self-directed Learning Reflection', 'FORM|SDL') in sim.get_last_buttons()

    @pytest.mark.asyncio
    async def test_detailed_self_directed_learning_reflection_opens_sdl_choice(self):
        from bot import AWAIT_FORM_CHOICE, _process_case_text

        case_text = (
            "Self-directed learning reflection. I completed the RCEMLearning module on adult "
            "sepsis recognition and initial ED management on 6 June 2026. I reviewed the NICE "
            "sepsis guidance and local ED sepsis pathway afterwards. Key learning was earlier "
            "recognition of high-risk features, prompt senior escalation, timely antibiotics, "
            "lactate measurement, cultures, and fluid reassessment. I realised I need to be "
            "more systematic with documenting sepsis screening and safety-netting when patients "
            "are discharged after infection assessment. I will use the ED sepsis checklist during "
            "my next shifts and discuss one relevant case with my supervisor to evidence change "
            "in practice."
        )
        sim = BotSimulator()
        context = sim._make_context()
        update = sim._make_text_update(case_text)

        result = await _process_case_text(
            update.message,
            context,
            update.effective_user.id,
            update.message.text,
            'text',
        )

        assert result == AWAIT_FORM_CHOICE
        text = sim.get_last_text()
        assert 'Self-directed Learning' in text
        assert ('✅ Draft Self-directed Learning Reflection', 'FORM|SDL') in sim.get_last_buttons()

    @pytest.mark.asyncio
    async def test_explicit_sdl_see_all_forms_back_restores_same_choice(self):
        from bot import AWAIT_FORM_CHOICE, _process_case_text, handle_form_choice

        case_text = (
            "Self-directed learning reflection. I completed the RCEMLearning module on adult "
            "sepsis recognition and initial ED management. I will discuss one relevant case "
            "with my supervisor to evidence change in practice."
        )
        sim = BotSimulator()
        context = sim._make_context()
        update = sim._make_text_update(case_text)

        result = await _process_case_text(
            update.message,
            context,
            update.effective_user.id,
            case_text,
            'text',
        )
        assert result == AWAIT_FORM_CHOICE
        first_text = sim.get_last_text()
        first_buttons = sim.get_last_buttons()

        with patch('bot.get_training_level', return_value='INTERMEDIATE'), \
             patch('bot.get_curriculum', return_value='2025'):
            await handle_form_choice(sim._make_callback_update('FORM|show_all'), context)
            await handle_form_choice(sim._make_callback_update('FORM|back'), context)

        assert sim.get_last_text() == first_text
        assert sim.get_last_buttons() == first_buttons

    @pytest.mark.asyncio
    async def test_sdl_thin_draft_asks_for_learning_notes_not_patient_details(self):
        from bot import AWAIT_CASE_INPUT, handle_form_choice
        from models import FormDraft

        empty_draft = FormDraft(form_type='SDL', uuid='uuid-sdl', fields={})
        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['case_text'] = 'self directed learning'

        update = sim._make_callback_update('FORM|SDL')
        with patch('bot._analyse_selected_form', new_callable=AsyncMock, return_value=empty_draft):
            result = await handle_form_choice(update, context)

        assert result == AWAIT_CASE_INPUT
        text = sim.get_last_text()
        assert 'self-directed learning reflection' in text.lower()
        assert 'patient/presentation' not in text.lower()
        assert 'clinical detail' not in text.lower()
        assert 'e-learning module details' not in text.lower()

    @pytest.mark.asyncio
    async def test_form_choice_transient_template_failure_keeps_retry_button(self):
        from bot import AWAIT_FORM_CHOICE, handle_form_choice

        sim = BotSimulator()
        update = sim._make_callback_update('FORM|MINI_CEX')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']

        with patch('bot._analyse_selected_form', new=AsyncMock(side_effect=RuntimeError('429 resource_exhausted'))):
            result = await handle_form_choice(update, context)

        assert result == AWAIT_FORM_CHOICE
        assert 'rate-limited' in sim.get_last_text()
        assert ('🔄 Try again', 'ACTION|retry_template') in sim.get_last_buttons()
        assert context.user_data['chosen_form'] == 'MINI_CEX'

    @pytest.mark.asyncio
    async def test_form_choice_non_transient_template_failure_reports_could_not_review(self):
        from bot import handle_form_choice

        sim = BotSimulator()
        update = sim._make_callback_update('FORM|MINI_CEX')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']

        with patch('bot._analyse_selected_form', new=AsyncMock(side_effect=ValueError('Unknown form type: BROKEN'))):
            result = await handle_form_choice(update, context)

        assert result == ConversationHandler.END
        assert 'Could not review that template' in sim.get_last_text()
        assert ('❌ Cancel', 'ACTION|cancel') in sim.get_last_buttons()

    @pytest.mark.asyncio
    async def test_retry_template_reuses_selected_form_and_case_text(self):
        from bot import AWAIT_APPROVAL, handle_callback
        from models import FormDraft

        sim = BotSimulator()
        update = sim._make_callback_update('ACTION|retry_template')
        context = sim._make_context()
        context.user_data.update({
            'case_text': SAMPLE_CASES['valid'],
            'chosen_form': 'MINI_CEX',
        })
        draft = FormDraft(
            form_type='MINI_CEX',
            uuid='uuid-mini',
            fields={
                'date_of_encounter': '2026-05-21',
                'clinical_setting': 'Emergency Department',
                'patient_presentation': 'Chest pain assessment.',
                'stage_of_training': 'Higher/ST4-ST6',
                'clinical_reasoning': 'I assessed and escalated the patient.',
                'reflection': 'I reflected on early escalation.',
            },
        )

        with patch('bot._analyse_selected_form', new=AsyncMock(return_value=draft)) as analyse:
            result = await handle_callback(update, context)

        assert result == AWAIT_APPROVAL
        analyse.assert_awaited_once()
        assert analyse.await_args.args[3] == 'MINI_CEX'
        assert 'Mini-Clinical Evaluation Exercise draft ready' in sim.get_last_text()

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
    async def test_template_review_fills_stage_from_saved_training_level(self):
        from bot import _analyse_selected_form
        from models import FormDraft

        sim = BotSimulator()
        context = sim._make_context()
        draft = FormDraft(
            form_type='CBD',
            uuid='uuid-cbd',
            fields={
                'date_of_encounter': '2026-05-27',
                'clinical_setting': 'ED',
                'patient_presentation': 'Sepsis',
                'clinical_reasoning': 'Escalated septic shock promptly.',
                'reflection': 'Earlier antibiotic prompt would improve flow.',
            },
        )

        with patch('bot.get_voice_profile', return_value=''), \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.extract_cbd_data', new=AsyncMock(return_value=draft)):
            result = await _analyse_selected_form(context, sim.user_id, SAMPLE_CASES['valid'], 'CBD')

        assert result.fields['stage_of_training'] == 'Higher/ST4-ST6'
        assert context.user_data['pending_draft_data']['fields']['stage_of_training'] == 'Higher/ST4-ST6'

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
        # Optional missing fields no longer surface a "Helpful if you have it"
        # banner; the approval keyboard still appears so the user can save anyway.
        assert 'Helpful if you have it' not in sim.get_last_text()
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
    async def test_quick_improve_targets_formal_course_reflective_notes(self):
        from bot import AWAIT_APPROVAL, handle_quick_improve
        from models import FormDraft

        original = FormDraft(
            form_type='FORMAL_COURSE',
            uuid='uuid-formal-course',
            fields={
                'stage_of_training': 'Higher/ST4-ST6',
                'project_description': 'I completed the ATLS Course for Doctors.',
                'reflective_notes': '',
                'resources_used': '',
                'lessons_learned': '',
            },
        )
        improved = FormDraft(
            form_type='FORMAL_COURSE',
            uuid='uuid-formal-course',
            fields={
                **original.fields,
                'reflective_notes': (
                    'Completing ATLS helped me structure trauma assessment more reliably '
                    'and reinforced the need to use a clear primary survey under pressure.'
                ),
                'resources_used': 'ATLS course manual and simulated trauma scenarios.',
                'lessons_learned': 'I will use the ATLS structure when leading trauma assessments.',
            },
        )

        sim = BotSimulator()
        update = sim._make_callback_update('IMPROVE|reflection')
        context = sim._make_context()
        context.user_data['case_text'] = 'I completed ATLS and have a certificate.'
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': 'FORMAL_COURSE',
            'fields': original.fields,
            'uuid': original.uuid,
        }

        with patch('bot.get_voice_profile', return_value=''), \
             patch('bot.extract_form_data', new_callable=AsyncMock, return_value=improved) as extract_mock:
            result = await handle_quick_improve(update, context)

        assert result == AWAIT_APPROVAL
        updated_fields = context.user_data['draft_data']['fields']
        assert updated_fields['reflective_notes'] == improved.fields['reflective_notes']
        assert updated_fields['resources_used'] == improved.fields['resources_used']
        assert updated_fields['lessons_learned'] == improved.fields['lessons_learned']
        assert updated_fields['project_description'] == original.fields['project_description']
        assert "does not have a reflection field" not in "\n".join(text or "" for _, text, _ in sim.messages_sent)
        assert extract_mock.await_args.args[1] == 'FORMAL_COURSE'

    @pytest.mark.asyncio
    async def test_quick_improve_edits_original_draft_in_place(self, thin_draft):
        """The revised draft must replace the original draft message (edit in
        place) and never spawn a second full draft message. The chat should
        show one living draft with a Revised draft label."""
        from bot import AWAIT_APPROVAL, handle_quick_improve
        from models import FormDraft

        improved = FormDraft(
            form_type='CBD',
            uuid='uuid-cbd',
            fields={
                **thin_draft.fields,
                'reflection': 'I will escalate dynamic ECG changes earlier and document the decision-making more clearly.',
            },
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
        original_message_id = update.callback_query.message.message_id

        with patch('bot.get_voice_profile', return_value=''), \
             patch('bot.extract_form_data', new_callable=AsyncMock, return_value=improved):
            result = await handle_quick_improve(update, context)

        assert result == AWAIT_APPROVAL

        # The original draft message must have been edited in place once, with
        # the revised content + Revised draft label and the approval keyboard.
        # Use the simulator's edit log to confirm.
        edits = [m for m in sim.messages_sent if m[0] == 'edit' and m[1] is not None]
        revised_edits = [m for m in edits if 'Revised draft' in m[1]]
        assert len(revised_edits) == 1, (
            f"Expected exactly one in-place edit with the Revised draft label, got: {edits}"
        )
        revised_text = revised_edits[0][1]
        assert improved.fields['reflection'] in revised_text
        revised_markup = revised_edits[0][2]
        assert revised_markup is not None and hasattr(revised_markup, 'inline_keyboard')
        button_data = [b.callback_data for row in revised_markup.inline_keyboard for b in row]
        assert 'APPROVE|draft' in button_data
        # One-revision default: improve button must NOT come back on the
        # revised draft.
        assert 'IMPROVE|reflection' not in button_data

        # The in-place edit must target the SAME message the user tapped
        # (the original draft), not a freshly sent ack message.
        update.callback_query.message.edit_text.assert_awaited_once()

        # No second full draft message should have been sent. The only
        # outbound "reply" should be the tiny status ack.
        replies = [m for m in sim.messages_sent if m[0] == 'reply']
        assert len(replies) == 1
        assert 'Tightening' in replies[0][1]
        # That status ack must have been dismissed (deleted), so the chat
        # ends up with a single living draft instead of a status + draft.
        assert any(m[0] == 'delete' for m in sim.messages_sent)

    @pytest.mark.asyncio
    async def test_quick_improve_failure_restores_original_buttons(self, thin_draft):
        """When the LLM call fails, the original draft message must keep its
        approval keyboard so the user can retry/save — no orphaned draft and
        no second full preview message."""
        from bot import AWAIT_APPROVAL, handle_quick_improve

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

        async def _raise(*args, **kwargs):
            raise RuntimeError('boom')

        with patch('bot.get_voice_profile', return_value=''), \
             patch('bot.extract_form_data', new_callable=AsyncMock, side_effect=_raise):
            result = await handle_quick_improve(update, context)

        assert result == AWAIT_APPROVAL
        # quick_improve_used must remain unset so the user can retry.
        assert not context.user_data.get('quick_improve_used')

        # No revised-draft edit should have happened on the original message.
        revised_edits = [
            m for m in sim.messages_sent
            if m[0] == 'edit' and m[1] is not None and 'Revised draft' in m[1]
        ]
        assert revised_edits == []

        # The keyboard must be restored on the ORIGINAL draft message via a
        # markup-only edit (text untouched). The improve button must still be
        # present so the user can retry.
        markup_events = [m for m in sim.messages_sent if m[0] == 'markup' and m[2] is not None]
        assert markup_events, 'Original draft buttons were not restored after failure'
        last_markup = markup_events[-1][2]
        button_data = [b.callback_data for row in last_markup.inline_keyboard for b in row]
        assert 'APPROVE|draft' in button_data
        assert 'IMPROVE|reflection' in button_data

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
        # Should show category buttons and back. Search by name is intentionally
        # not offered from the all-forms/category picker.
        for cat_name, slug in _CAT_SLUGS.items():
            assert f'FORM|cat_{slug}' in button_data, f"Missing category button for {cat_name}"
        assert 'FORM|search' not in button_data
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
    async def test_teaching_category_names_self_directed_learning(self):
        from bot import AWAIT_FORM_CHOICE, handle_form_choice

        sim = BotSimulator()
        update = sim._make_callback_update('FORM|cat_TEACHING')
        context = sim._make_context()

        with patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'):
            result = await handle_form_choice(update, context)

        assert result == AWAIT_FORM_CHOICE
        buttons = sim.get_last_buttons()
        assert ('📖 Self-directed Learning', 'FORM|SDL') in buttons
        assert all(label != '📖 SDL' for label, _ in buttons)

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
        # Must use the standard "Portfolio Guru is ready" intake copy, not the
        # shorter FILE_CASE_PROMPT, so the UX matches a normal /start.
        assert 'Portfolio Guru is ready' in sim.get_last_text()
        # The post-filing report keyboard must NOT be stripped — the saved-draft
        # report should remain intact after clicking "File another case".
        stripped_keyboard_events = [
            m for m in sim.messages_sent if m[0] == 'markup' and m[2] is None
        ]
        assert stripped_keyboard_events == [], (
            "handle_callback ACTION|file must not edit_message_reply_markup(None) "
            "because that strips the post-filing report keyboard"
        )

    @pytest.mark.asyncio
    async def test_file_another_case_global_handler_uses_standard_intake_copy(self):
        """handle_action_button ACTION|file (global, post-ConversationHandler.END) must
        send WELCOME_MSG_CONNECTED and not FILE_CASE_PROMPT."""
        from bot import AWAIT_CASE_INPUT, WELCOME_MSG_CONNECTED, handle_action_button

        sim = BotSimulator()
        update = sim._make_callback_update('ACTION|file')
        context = sim._make_context()

        with patch('bot.has_credentials', return_value=True):
            result = await handle_action_button(update, context)

        assert result == AWAIT_CASE_INPUT
        assert sim.get_last_text() == WELCOME_MSG_CONNECTED
        assert "show buttons for what to do next" in sim.get_last_text()
        assert "draft it" not in sim.get_last_text()

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
    async def test_cancel_path_uses_standard_connected_intake_copy(self):
        from bot import WELCOME_MSG_CONNECTED, handle_callback

        sim = BotSimulator()
        update = sim._make_callback_update('CANCEL|draft')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']

        with patch('bot.has_credentials', return_value=True):
            result = await handle_callback(update, context)

        assert result == ConversationHandler.END
        assert "Cancelled" in sim.get_last_text()
        assert WELCOME_MSG_CONNECTED in sim.get_last_text()
        assert "draft it" not in sim.get_last_text()

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
        # The progress message is sent as a new message (1 reply), then the
        # final report edits that progress message (1 edit). The original
        # draft preview survives in the chat history above.
        assert sim.messages_sent[-1][0] == 'edit'
        assert 'case-based discussion saved' in sim.get_last_text().lower()
        assert 'filing finished' not in sim.get_last_text().lower()
        buttons = sim.get_last_buttons()
        # First button may be File another case or the amend button row
        assert ('📋 File another case', 'ACTION|file') in buttons
        assert ('👍 It worked', 'FEEDBACK|good|CBD|success') not in buttons
        assert ("👎 Didn't work", 'FEEDBACK|bad|CBD|success') not in buttons
        assert ('🧰 More options', 'ACTION|post_file_more|CBD|success') not in buttons
        assert not any(label in {'💬 Something missing?', '⚙️ Settings', '🏠 Main menu'} for label, _ in buttons)

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
    async def test_text_file_this_again_retries_active_draft_after_ready_message(self, thin_draft):
        """User sent 'file this again' after the bot returned to ready state but the
        draft (e.g. from a timed-out or errored filing attempt) is still loaded.
        Must approve the active draft rather than treating the text as a new case."""
        from bot import handle_case_input

        sim = BotSimulator()
        update = sim._make_text_update('file this again')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }
        context.user_data['last_filing_status'] = 'failed'
        context.user_data['last_filing_form_name'] = 'Case-Based Discussion'

        route_filing_mock = AsyncMock(return_value={
            'status': 'success',
            'filled': ['date_of_encounter'],
            'skipped': [],
            'method': 'deterministic',
        })
        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new=route_filing_mock), \
             patch('bot.recommend_form_types', new=AsyncMock(return_value=[])):
            result = await handle_case_input(update, context)

        route_filing_mock.assert_awaited()
        assert result == ConversationHandler.END
        assert context.user_data['last_filing_status'] == 'success'

    @pytest.mark.asyncio
    async def test_text_retry_restores_last_amend_draft_after_partial_failure(self, thin_draft):
        """After a partial filing the active draft is cleared but last_amend_draft
        is preserved. A natural retry phrase ('try again') in ready state must
        restore that draft and run handle_approval_approve, not regenerate."""
        from bot import handle_case_input

        sim = BotSimulator()
        update = sim._make_text_update('try again')
        context = sim._make_context()
        # Mirror the state set by handle_approval_approve after a partial save.
        context.user_data['last_amend_draft'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }
        context.user_data['last_amend_case_text'] = 'Original case text.'
        context.user_data['last_amend_chosen_form'] = thin_draft.form_type
        context.user_data['last_filing_status'] = 'partial'
        context.user_data['last_filing_form_name'] = 'Case-Based Discussion'

        route_filing_mock = AsyncMock(return_value={
            'status': 'success',
            'filled': ['date_of_encounter'],
            'skipped': [],
            'method': 'deterministic',
        })
        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new=route_filing_mock), \
             patch('bot.recommend_form_types', new=AsyncMock(return_value=[])):
            result = await handle_case_input(update, context)

        route_filing_mock.assert_awaited()
        assert result == ConversationHandler.END
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
    async def test_dops_save_with_missing_date_proceeds_without_extra_warning(self):
        """A DOPS draft missing only the date is useful enough to file — the
        bot must respect the user's explicit Save as draft and call
        route_filing so Kaizen actually receives the draft. No separate
        "heads-up" warning is sent — the user already saw the gap on the
        draft preview, and the filing flow stays a single morphing message.
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
        # No separate non-blocking warning message is sent — the only
        # outbound messages for this flow are edits on the morphing source
        # message (Saving… → final report). The draft preview already
        # surfaced the missing detail before the user approved.
        fresh_messages = [
            text for kind, text, _ in sim.messages_sent
            if kind in ('reply', 'send') and text
        ]
        gap_warnings = [
            text for text in fresh_messages
            if 'date' in text.lower()
            and ('gap' in text.lower() or 'add these in kaizen' in text.lower())
        ]
        assert not gap_warnings, (
            'expected no separate gap-warning message; '
            f'got: {gap_warnings}'
        )

    @pytest.mark.asyncio
    async def test_uncertain_save_keeps_draft_and_offers_compact_recovery(self, thin_draft):
        from bot import AWAIT_APPROVAL, handle_approval_approve

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
             patch('bot.compose_filing_recovery_copy', new=AsyncMock(return_value="")), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'partial',
                 'filled': ['date_of_encounter'],
                 'skipped': [],
                 'error': 'Save was clicked, but I could not confirm the entry in the activities list.',
                 'method': 'deterministic',
             }):
            result = await handle_approval_approve(update, context)

        assert result == AWAIT_APPROVAL
        assert context.user_data.get('draft_data')
        text = sim.get_last_text()
        assert 'may not have saved' in text.lower()
        # New step-header treatment so the uncertain-save report doesn't read
        # as another draft block, plus a divider separating the recovery copy
        # from the saved-status line.
        assert '⚠️ Filing had issues — check Kaizen' in text
        # The link in the message body must not falsely promise to open the
        # draft we just tried to save — when uncertain, we point at the
        # Kaizen drafts list so the user verifies first.
        assert 'Check your Kaizen drafts' in text
        assert '[Open ' not in text  # no "Open {form_name} manually in Kaizen" link
        buttons = sim.get_last_buttons()
        assert ('👍 It worked', 'FEEDBACK|good|CBD|partial') not in buttons
        assert ("👎 Didn't work", 'FEEDBACK|bad|CBD|partial') not in buttons
        assert ('📋 File another case', 'ACTION|file') in buttons
        assert ('❌ Cancel', 'ACTION|cancel') in buttons
        assert ('🧰 More options', 'ACTION|post_file_more|CBD|partial') not in buttons
        assert not any(label in {'💬 Something missing?', '⚙️ Settings', '🏠 Main menu'} for label, _ in buttons)
        # Button URLs must never lead to /events/new-section/ on uncertain
        # save — that opens a BLANK form and the user reported it looked
        # like the saved draft.
        markup = sim.messages_sent[-1][2]
        url_buttons = [
            button.url
            for row in markup.inline_keyboard
            for button in row
            if getattr(button, 'url', None)
        ]
        assert not any('events/new-section/' in url for url in url_buttons)
        assert any(url == 'https://kaizenep.com/activities' for url in url_buttons)

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
        # The saved-draft confirmation must lead with a clear step header so
        # users can tell the case has been filed; the review guidance must
        # then read as its own block, not as draft content.
        assert '📥 Draft saved in Kaizen' in text
        assert '⚠️ Needs your review' in text
        assert '8 fields filled' in text
        assert 'needs your review: Placement' in text
        assert '10 cases this month (Unlimited)' in text
        # The old single-line wording must NOT come back — that's the merged
        # look the user reported.
        assert 'saved as a draft, but needs manual review' not in text
        assert 'This is not complete yet' not in text
        assert '✅ Kaizen draft saved' not in text

        buttons = sim.get_last_buttons()
        callbacks = {callback for _, callback in buttons}
        assert 'FEEDBACK|good|DOPS|partial' not in callbacks
        assert 'FEEDBACK|bad|DOPS|partial' not in callbacks
        assert 'AMEND|amend' not in callbacks
        assert 'ACTION|file' in callbacks
        assert 'ACTION|post_file_more|DOPS|partial' not in callbacks
        assert not any(callback.startswith('FIELD|') for callback in callbacks)

        markup = sim.messages_sent[-1][2]
        url_buttons = [
            button.url
            for row in markup.inline_keyboard
            for button in row
            if getattr(button, 'url', None)
        ]
        # Without a saved_url from the filer, the Open Kaizen button MUST
        # land on the activities list — never the new-section URL (which
        # would open a blank form instead of the saved draft).
        assert any(url == 'https://kaizenep.com/activities' for url in url_buttons)
        assert not any('events/new-section/' in url for url in url_buttons)

    @pytest.mark.asyncio
    async def test_saved_partial_dops_2021_does_not_surface_field_edit_buttons(self):
        """Saved partial results should stay at high-level post-filing actions.

        Field-level edit buttons belong to explicit amend/edit flows or true
        failed-filing recovery, not to a Kaizen draft that was already saved.
        """
        from bot import handle_approval_approve
        from models import FormDraft

        dops_draft = FormDraft(
            form_type='DOPS_2021',
            uuid='uuid-dops-2021',
            fields={
                'date_of_encounter': '2026-06-02',
                'stage_of_training': 'SAS',
                'clinical_setting': 'Emergency Department',
                'placement': 'Emergency Department',
                'procedure_name': 'Closed ankle fracture reduction under procedural sedation',
                'procedural_skill': 'Closed ankle fracture reduction under procedural sedation',
                'indication': 'Displaced ankle fracture requiring reduction in ED.',
                'clinical_reasoning': 'I assessed suitability for ED reduction and procedural sedation.',
                'trainee_performance': 'I directly observed the sedation and reduction workflow.',
                'reflection': 'I will keep practising sedation team briefing and reduction planning.',
                'curriculum_links': ['SLO6'],
                'key_capabilities': ['Safely performs practical procedures'],
            },
        )

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['case_text'] = 'Observed procedural sedation and ankle fracture reduction.'
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': dops_draft.form_type,
            'fields': dops_draft.fields,
            'uuid': dops_draft.uuid,
        }

        with patch('bot.get_training_level', return_value='SAS'), \
             patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.record_case_filed', new=AsyncMock()), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 6, -1, 'pro_plus'))), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'partial',
                 'filled': [
                     'date_of_encounter',
                     'end_date',
                     'event_description',
                     'clinical_reasoning',
                     'reflection',
                     'key_capabilities',
                 ],
                 'skipped': ['stage_of_training', 'placement', 'procedure_name', 'procedural_skill'],
                 'method': 'deterministic',
                 'saved_url': 'https://kaizenep.com/events/fillin/dops-draft?autosave=1',
             }):
            result = await handle_approval_approve(update, context)

        assert result == ConversationHandler.END
        text = sim.get_last_text()
        assert '📥 Draft saved in Kaizen' in text
        assert 'Direct Observation of Procedural Skills' in text
        assert 'DOPS_2021' not in text

        callbacks = {callback for _, callback in sim.get_last_buttons()}
        assert 'FIELD|stage_of_training' not in callbacks
        assert 'FIELD|placement' not in callbacks
        assert 'FIELD|procedure_name' not in callbacks
        assert 'FIELD|procedural_skill' not in callbacks
        assert not any(callback.startswith('FIELD|') for callback in callbacks)
        assert 'ACTION|same_case_another' in callbacks
        assert 'ACTION|file' in callbacks
        assert 'ACTION|retry_filing' not in callbacks

    @pytest.mark.asyncio
    async def test_stale_post_filing_more_rebuilds_compact_actions(self, thin_draft):
        from bot import handle_action_button, handle_same_case_another

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
        assert ('❌ Cancel', 'ACTION|cancel') in buttons
        assert ('💬 Something missing?', 'FILING|feedback|CBD') not in buttons
        assert ('🚩 Flag a missed field', 'FILING|feedback|CBD') not in buttons
        assert ('⚙️ Settings', 'ACTION|settings') not in buttons
        assert ('🏠 Main menu', 'ACTION|back_to_menu') not in buttons
        assert ('🧰 More options', 'ACTION|post_file_more|CBD|partial') not in buttons

    def test_post_filing_keyboard_does_not_show_more_options_for_any_status(self):
        from bot import _build_post_filing_keyboard

        for status in ("success", "partial", "failed"):
            keyboard = _build_post_filing_keyboard("CBD", status)
            buttons = [
                (button.text, button.callback_data)
                for row in keyboard.inline_keyboard
                for button in row
            ]
            assert ("🧰 More options", f"ACTION|post_file_more|CBD|{status}") not in buttons
            assert not any(label in {'💬 Something missing?', '⚙️ Settings', '🏠 Main menu'} for label, _ in buttons)

    @pytest.mark.asyncio
    async def test_stale_post_filing_more_does_not_restore_clutter(self, thin_draft):
        from bot import handle_action_button, handle_filing_feedback

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.get_user_tier', new_callable=AsyncMock, return_value='free'), \
             patch('bot.get_cases_this_month', new_callable=AsyncMock, return_value=0):
            await handle_action_button(sim._make_callback_update('ACTION|post_file_more|CBD|failed'), context)
            callbacks = {callback for _, callback in sim.get_last_buttons()}
            assert {'ACTION|retry_filing', 'ACTION|file', 'ACTION|cancel'} <= callbacks
            assert 'FILING|feedback|CBD' not in callbacks
            assert 'ACTION|settings' not in callbacks
            assert 'ACTION|back_to_menu' not in callbacks

            await handle_filing_feedback(sim._make_callback_update('FILING|feedback|CBD'), context)
            pushback_callbacks = {callback for _, callback in sim.get_last_buttons()}
            assert 'PUSHBACK|CBD|date_of_encounter' in pushback_callbacks
            assert 'PUSHBACK|CBD|other' in pushback_callbacks

    @pytest.mark.asyncio
    async def test_failed_filing_uses_llm_recovery_copy(self, thin_draft):
        from bot import AWAIT_APPROVAL, handle_approval_approve

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        recovery_line = "Try again, or open the form in Kaizen and fill it manually."

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'failed', 'filled': [], 'skipped': [], 'method': 'deterministic',
                 'error': 'Save button not found or click failed',
             }):
            result = await handle_approval_approve(update, context)

        text = sim.get_last_text()
        assert result == AWAIT_APPROVAL
        assert recovery_line in text
        assert "Filing didn't complete" in text
        buttons = sim.get_last_buttons()
        assert ('📋 File another case', 'ACTION|file') in buttons
        assert ('❌ Cancel', 'ACTION|cancel') in buttons

    @pytest.mark.asyncio
    async def test_failed_login_shows_reconnect_keyboard(self, thin_draft):
        from bot import AWAIT_APPROVAL, handle_approval_approve

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
                 'error': 'Login failed',
             }), \
             patch('bot.compose_filing_recovery_copy', new=AsyncMock(return_value='ignored')):
            result = await handle_approval_approve(update, context)

        text = sim.get_last_text()
        assert result == AWAIT_APPROVAL
        assert "Kaizen session has expired" in text
        assert "Reconnect Kaizen" in text
        buttons = sim.get_last_buttons()
        assert ('🔑 Reconnect Kaizen', 'ACTION|setup') in buttons
        assert ('🔄 Try Again', 'ACTION|retry_filing') in buttons
        assert ('🆕 Start fresh', 'ACTION|reset') in buttons
        # Draft must be preserved so Try Again can pick it up
        assert context.user_data.get('draft_data') is not None
        assert context.user_data.get('force_reconnect') is True

    @pytest.mark.asyncio
    async def test_failed_filing_falls_back_to_static_when_llm_empty(self, thin_draft):
        from bot import AWAIT_APPROVAL, handle_approval_approve

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
            result = await handle_approval_approve(update, context)

        text = sim.get_last_text()
        assert result == AWAIT_APPROVAL
        assert 'Try again' in text or 'manually' in text
        assert 'Something went wrong' in text

    @pytest.mark.asyncio
    async def test_failed_filing_try_again_reuses_active_draft(self, thin_draft):
        from bot import AWAIT_APPROVAL, handle_approval_approve, handle_callback

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        route_filing = AsyncMock(side_effect=[
            {
                'status': 'failed',
                'filled': [],
                'skipped': [],
                'method': 'deterministic',
                'error': 'Save button not found or click failed',
            },
            {
                'status': 'success',
                'filled': ['date_of_encounter'],
                'skipped': [],
                'method': 'deterministic',
            },
        ])

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new=route_filing), \
             patch('bot.compose_filing_recovery_copy', new=AsyncMock(return_value='')):
            first = await handle_approval_approve(sim._make_callback_update('APPROVE|draft'), context)
            second = await handle_callback(sim._make_callback_update('ACTION|retry_filing'), context)

        assert first == AWAIT_APPROVAL
        assert route_filing.await_count == 2
        assert route_filing.await_args_list[0].kwargs['reuse_draft'] is False
        assert route_filing.await_args_list[1].kwargs['reuse_draft'] is True
        assert second == ConversationHandler.END

    @pytest.mark.asyncio
    async def test_failed_filing_new_text_shows_intent_gate(self, thin_draft):
        from bot import AWAIT_APPROVAL, handle_approval_approve, handle_mid_conversation_text

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data.update({
            'draft_data': {
                '_type': 'FORM',
                'form_type': thin_draft.form_type,
                'fields': thin_draft.fields,
                'uuid': thin_draft.uuid,
            },
            'case_text': 'Original LAT flow-management case',
            'chosen_form': thin_draft.form_type,
        })

        route_filing = AsyncMock(return_value={
            'status': 'failed',
            'filled': [],
            'skipped': [],
            'method': 'deterministic',
            'error': 'Save button not found or click failed',
        })

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new=route_filing), \
             patch('bot.compose_filing_recovery_copy', new=AsyncMock(return_value='')):
            first = await handle_approval_approve(sim._make_callback_update('APPROVE|draft'), context)

        assert first == AWAIT_APPROVAL

        new_case_text = 'New case: paediatric asthma review with escalation to HDU.'
        result = await handle_mid_conversation_text(sim._make_text_update(new_case_text), context)

        assert result == AWAIT_APPROVAL
        assert route_filing.await_count == 1
        assert context.user_data['pending_new_case_text'] == new_case_text
        text = (sim.get_last_text() or '').lower()
        assert 'kept that draft open' in text
        buttons = sim.get_last_buttons()
        assert ('🔄 Retry filing this draft', 'ACTION|retry_filing') in buttons
        assert ('✏️ Keep editing this draft', 'CASE|improve') in buttons
        assert ('📋 Start new case', 'CASE|new') in buttons
        assert ('❌ Cancel current draft', 'ACTION|cancel') in buttons

    @pytest.mark.asyncio
    async def test_failed_filing_start_new_choice_processes_pending_text(self, thin_draft):
        from bot import AWAIT_APPROVAL, AWAIT_FORM_CHOICE, handle_callback, handle_mid_conversation_text

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data.update({
            'draft_data': {
                '_type': 'FORM',
                'form_type': thin_draft.form_type,
                'fields': thin_draft.fields,
                'uuid': thin_draft.uuid,
            },
            'case_text': 'Original failed case',
            'chosen_form': thin_draft.form_type,
            'last_filing_status': 'failed',
            'last_filing_form_name': 'Leadership Assessment Tool',
        })

        new_case_text = 'New case: paediatric asthma review with escalation to HDU.'
        gate_result = await handle_mid_conversation_text(sim._make_text_update(new_case_text), context)

        assert gate_result == AWAIT_APPROVAL

        with patch('bot._process_case_text', new=AsyncMock(return_value=AWAIT_FORM_CHOICE)) as process_case:
            result = await handle_callback(sim._make_callback_update('CASE|new'), context)

        assert result == AWAIT_FORM_CHOICE
        process_case.assert_awaited_once()
        assert process_case.await_args.args[3] == new_case_text
        assert context.user_data.get('draft_data') is None

    @pytest.mark.asyncio
    async def test_store_draft_clears_stale_failed_filing_retry_state(self, thin_draft):
        """A freshly created draft (e.g. a certificate/document draft) must not
        inherit a failed-filing retry affordance from an earlier, unrelated
        filing. Storing a new draft clears the sticky last_filing_* state so the
        'Retry filing this draft' gate only appears for a draft that genuinely
        had a filing attempt."""
        from bot import _store_draft, _has_retryable_failed_filing_draft

        sim = BotSimulator()
        context = sim._make_context()
        # Stale state left behind by an earlier failed filing on a different case.
        context.user_data['last_filing_status'] = 'failed'
        context.user_data['last_filing_form_name'] = 'Case-Based Discussion'
        context.user_data['last_amend_draft'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }
        # Sticky status would otherwise make the old draft look retryable.
        assert _has_retryable_failed_filing_draft(context) is True

        # A brand-new draft is created (the document/certificate review flow).
        _store_draft(context, thin_draft)

        assert context.user_data.get('last_filing_status') not in {'failed', 'partial'}
        assert _has_retryable_failed_filing_draft(context) is False

    def test_draft_review_and_failed_filing_keyboards_are_distinct(self):
        """The normal draft-review keyboard must never offer 'Retry filing this
        draft' — that affordance belongs only to the failed-filing gate."""
        from bot import (
            _build_approval_keyboard,
            _build_failed_filing_input_gate_keyboard,
        )

        def labels(markup):
            return [
                button.text
                for row in markup.inline_keyboard
                for button in row
            ]

        review_labels = labels(_build_approval_keyboard())
        gate_labels = labels(_build_failed_filing_input_gate_keyboard())

        assert not any('Retry filing' in label for label in review_labels)
        assert any('Retry filing' in label for label in gate_labels)

    @pytest.mark.asyncio
    async def test_media_feedback_on_fresh_draft_skips_retry_gate(self, thin_draft):
        """Sending a document/photo as extra detail on a freshly stored draft
        must regenerate the draft, not surface the failed-filing retry gate,
        even if an earlier unrelated case left a stale 'failed' status."""
        from bot import _store_draft, _has_retryable_failed_filing_draft

        sim = BotSimulator()
        context = sim._make_context()
        # Earlier unrelated failure leaves sticky status before the new draft.
        context.user_data['last_filing_status'] = 'failed'
        context.user_data['last_filing_form_name'] = 'Case-Based Discussion'

        # Creating/presenting the new certificate draft stores it.
        _store_draft(context, thin_draft)

        # The new draft never had a filing attempt, so no retry gate is owed.
        assert _has_retryable_failed_filing_draft(context) is False

    @pytest.mark.asyncio
    async def test_global_try_again_button_can_retry_after_conversation_end(self, thin_draft):
        from bot import handle_action_button, handle_same_case_another

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        route_filing = AsyncMock(return_value={
            'status': 'success',
            'filled': ['date_of_encounter'],
            'skipped': [],
            'method': 'deterministic',
        })

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.route_filing', new=route_filing):
            result = await handle_action_button(sim._make_callback_update('ACTION|retry_filing'), context)

        route_filing.assert_awaited_once()
        assert route_filing.await_args.kwargs['reuse_draft'] is True
        assert result == ConversationHandler.END

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
        assert 'No Portfolio Guru cases filed this week' in text

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
    async def test_same_case_manual_mini_cex_selection_reaches_template_review(self):
        from bot import AWAIT_APPROVAL, handle_case_input, handle_form_choice
        from models import FormDraft

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['last_filed_case_text'] = (
            '58F with pleuritic chest pain in ED. I assessed for PE, reviewed ECG, '
            'discussed imaging and reflected on safety-netting.'
        )
        context.user_data['last_filed_form_type'] = 'CBD'

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 5, 'free'))), \
             patch('bot.recommend_form_types', new=AsyncMock()) as recommend:
            result = await handle_case_input(sim._make_text_update('use the same case for MINI_CEX'), context)

        assert result != ConversationHandler.END
        recommend.assert_not_awaited()
        assert context.user_data['chosen_form'] == 'MINI_CEX'
        assert 'pleuritic chest pain' in context.user_data['case_text']
        assert ('✅ Draft Mini-Clinical Evaluation Exercise', 'FORM|MINI_CEX') in sim.get_last_buttons()

        draft = FormDraft(
            form_type='MINI_CEX',
            uuid='uuid-mini',
            fields={
                'date_of_encounter': '2026-05-21',
                'clinical_setting': 'Emergency Department',
                'patient_presentation': 'Pleuritic chest pain assessment.',
                'stage_of_training': 'Higher/ST4-ST6',
                'clinical_reasoning': 'I assessed for PE and discussed imaging.',
                'reflection': 'I reflected on safety-netting.',
            },
        )
        with patch('bot._analyse_selected_form', new=AsyncMock(return_value=draft)) as analyse:
            result = await handle_form_choice(sim._make_callback_update('FORM|MINI_CEX'), context)

        assert result == AWAIT_APPROVAL
        analyse.assert_awaited_once()
        assert analyse.await_args.args[2] == context.user_data['case_text']
        assert analyse.await_args.args[3] == 'MINI_CEX'
        assert 'Mini-Clinical Evaluation Exercise draft ready' in sim.get_last_text()

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
        from bot import WELCOME_MSG_CONNECTED, setup_curriculum

        sim = BotSimulator()
        update = sim._make_callback_update('SETUP_CURRICULUM|2025')
        context = sim._make_context()

        with patch('bot.store_curriculum'):
            result = await setup_curriculum(update, context)

        assert result == ConversationHandler.END
        assert 'setup complete' in sim.get_last_text().lower()
        # Post-setup completion no longer has a "File first case" button —
        # the user is invited to send their case directly using the same
        # canonical intake message as /start, /cancel, and "File another case".
        assert WELCOME_MSG_CONNECTED in sim.get_last_text()

    def test_quick_improve_keyboard_can_be_locked_after_one_use(self):
        from bot import _build_approval_keyboard

        keyboard = _build_approval_keyboard(improved_once=True)
        buttons = [(b.text, b.callback_data) for row in keyboard.inline_keyboard for b in row]

        # After one use, the improve button is removed entirely
        assert ('Improved once ✅', 'IMPROVE|used') not in buttons
        assert ('✨ Quick improve', 'IMPROVE|reflection') not in buttons
        assert ('📤 Save as draft', 'APPROVE|draft') in buttons

    def test_dops_pre_file_guard_blocks_blank_voice_draft(self):
        from bot import _universal_pre_file_gate

        missing = _universal_pre_file_gate('DOPS', {
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
        from bot import _universal_pre_file_gate

        missing = _universal_pre_file_gate('DOPS', {
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

    def test_pre_file_guard_flags_missing_reflection_when_required(self):
        """If a field like reflection is required in the schema, the universal
        gate flags it when it is missing or empty.
        """
        from bot import _universal_pre_file_gate

        missing = _universal_pre_file_gate('MINI_CEX', {
            'date_of_encounter': '2026-05-19',
            'stage_of_training': 'Higher/ST4-ST6',
            'clinical_setting': 'Emergency Department',
            'patient_presentation': 'Chest pain',
            'clinical_reasoning': 'Worked up and managed appropriately.',
            'reflection': '',
        })

        assert 'Reflection' in missing

    def test_post_filing_keyboard_offers_same_case_another_wpba(self):
        from bot import _build_post_filing_keyboard

        keyboard = _build_post_filing_keyboard('CBD', 'success', same_case_available=True)
        buttons = [(b.text, b.callback_data) for row in keyboard.inline_keyboard for b in row]

        assert ('💾 Save as another WBA', 'ACTION|same_case_another') in buttons
        assert ('📋 File another case', 'ACTION|file') in buttons
        assert [
            ('💾 Save as another WBA', 'ACTION|same_case_another'),
            ('📋 File another case', 'ACTION|file'),
        ] in [
            [(b.text, b.callback_data) for b in row]
            for row in keyboard.inline_keyboard
        ]

    def test_post_filing_keyboard_offers_same_case_for_clean_partial(self):
        """Clean partial (no error) should also surface the 'Same case,
        another WPBA' button when same_case_available is true, so the user
        can get a different form type from the same case text."""
        from bot import _build_post_filing_keyboard

        keyboard = _build_post_filing_keyboard('CBD', 'partial', same_case_available=True)
        buttons = [(b.text, b.callback_data) for row in keyboard.inline_keyboard for b in row]

        assert ('💾 Save as another WBA', 'ACTION|same_case_another') in buttons

    def test_post_filing_keyboard_no_same_case_for_uncertain_partial(self):
        """Uncertain partial (partial + error) must NOT offer Same case
        — the save may not have landed, so we don't encourage re-filing."""
        from bot import _build_post_filing_keyboard

        keyboard = _build_post_filing_keyboard('CBD', 'partial', uncertain=True, same_case_available=True)
        buttons = [(b.text, b.callback_data) for row in keyboard.inline_keyboard for b in row]

        assert ('💾 Save as another WBA', 'ACTION|same_case_another') not in buttons
        assert ('🔄 Try again', 'ACTION|retry_filing') in buttons

    def test_post_filing_keyboard_links_to_saved_draft_url_when_present(self):
        """When the filer captures the post-save Kaizen URL, the Open button
        must link directly to that draft — not to the new-section URL (which
        would open a blank form and is the bug the user reported)."""
        from bot import _build_post_filing_keyboard

        saved_url = 'https://kaizenep.com/events/fillin/draft-doc-id?autosave=auto-1'
        for status in ('success', 'partial'):
            keyboard = _build_post_filing_keyboard(
                'CBD', status, saved_url=saved_url,
            )
            labelled_urls = [
                (button.text, button.url)
                for row in keyboard.inline_keyboard
                for button in row
                if getattr(button, 'url', None)
            ]

            assert ('🔗 Open saved draft', saved_url) in labelled_urls
            # No 'Open in Kaizen' label that falsely promises to open the draft.
            assert not any(text == '🔗 Open in Kaizen' for text, _ in labelled_urls)
            # The new-section URL (blank form) must not appear when we have a
            # real saved-draft URL.
            assert not any('events/new-section/' in url for _, url in labelled_urls)

    def test_post_filing_keyboard_falls_back_to_activities_without_saved_url(self):
        """Without a saved-draft URL, the button must NOT open a new blank
        form. It opens the Kaizen activities list (where the saved draft can
        be found) and the label is plain 'Open Kaizen' — no false promise."""
        from bot import _build_post_filing_keyboard

        keyboard = _build_post_filing_keyboard('CBD', 'partial', saved_url=None)
        labelled_urls = [
            (button.text, button.url)
            for row in keyboard.inline_keyboard
            for button in row
            if getattr(button, 'url', None)
        ]

        assert ('🔗 Open Kaizen', 'https://kaizenep.com/activities') in labelled_urls
        # No label claiming the button opens the saved draft, since it
        # actually opens the activities list.
        assert not any(text == '🔗 Open saved draft' for text, _ in labelled_urls)
        # Critically: never link to the new-section URL on a saved draft —
        # that would open a blank form, which is the user-reported bug.
        assert not any('events/new-section/' in url for _, url in labelled_urls)

        success_keyboard = _build_post_filing_keyboard('CBD', 'success', saved_url=None)
        success_urls = [
            (button.text, button.url)
            for row in success_keyboard.inline_keyboard
            for button in row
            if getattr(button, 'url', None)
        ]
        assert ('🔗 Open Kaizen', 'https://kaizenep.com/activities') in success_urls
        assert not any('events/new-section/' in url for _, url in success_urls)

    def test_post_filing_keyboard_uncertain_uses_saved_url_or_activities_fallback(self):
        """The uncertain-save path (partial + error) currently can't trust the
        captured URL, so it must use the honest activities-list fallback."""
        from bot import _build_post_filing_keyboard

        keyboard = _build_post_filing_keyboard('CBD', 'partial', uncertain=True)
        url_buttons = [
            button.url
            for row in keyboard.inline_keyboard
            for button in row
            if getattr(button, 'url', None)
        ]

        assert 'https://kaizenep.com/activities' in url_buttons
        assert not any('events/new-section/' in url for url in url_buttons)

    @pytest.mark.asyncio
    async def test_saved_confirmation_partial_uses_clear_step_header(self, thin_draft):
        """The partial-no-error confirmation must lead with a 'Draft saved in
        Kaizen' step header and visually separate the field-review guidance
        from the saved-status line so users can tell what phase they're in."""
        from bot import handle_approval_approve

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.record_case_filed', new=AsyncMock()), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 1, -1, 'pro_plus'))), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'partial',
                 'filled': ['date_of_encounter', 'clinical_setting', 'reflection'],
                 'skipped': ['clinical_reasoning'],
                 'method': 'deterministic',
                 'saved_url': 'https://kaizenep.com/events/fillin/draft-doc-id?autosave=auto-1',
             }):
            await handle_approval_approve(update, context)

        text = sim.get_last_text()

        # Top step header is clear and distinct from the draft above.
        first_line = text.split('\n', 1)[0]
        assert '📥' in first_line and 'Draft saved in Kaizen' in first_line, (
            f"First line should be the step header. Got: {first_line!r}"
        )

        # Field-review guidance lives in its own block, after the saved-status
        # block, and is itself led by a clear "Needs your review" sub-header.
        assert '⚠️ Needs your review' in text
        header_pos = text.index('Draft saved in Kaizen')
        review_pos = text.index('Needs your review')
        assert review_pos > header_pos, (
            "Field-review guidance must come AFTER the saved-status header"
        )

        # When saved_url is available, the action line points to "the saved
        # draft" — matching what the keyboard button actually does.
        assert 'Open the saved draft' in text

        # Keyboard must link to the actual saved draft URL.
        url_buttons = [
            button.url
            for row in sim.messages_sent[-1][2].inline_keyboard
            for button in row
            if getattr(button, 'url', None)
        ]
        assert 'https://kaizenep.com/events/fillin/draft-doc-id?autosave=auto-1' in url_buttons

    @pytest.mark.asyncio
    async def test_saved_confirmation_partial_falls_back_when_no_saved_url(self, thin_draft):
        """Without a saved_url, the action wording and the button must both
        be the honest fallback: 'Open Kaizen and find your saved draft' (text)
        and the activities list (URL)."""
        from bot import handle_approval_approve

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.record_case_filed', new=AsyncMock()), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 1, -1, 'pro_plus'))), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'partial',
                 'filled': ['date_of_encounter', 'clinical_setting'],
                 'skipped': ['reflection'],
                 'method': 'browser-use',
                 # No saved_url — browser-use path doesn't return it.
             }):
            await handle_approval_approve(update, context)

        text = sim.get_last_text()
        assert '📥 Draft saved in Kaizen' in text
        # Honest fallback wording — does not promise to open the draft.
        assert 'Open Kaizen and find your saved draft' in text
        assert 'Open the saved draft' not in text

        url_buttons = [
            button.url
            for row in sim.messages_sent[-1][2].inline_keyboard
            for button in row
            if getattr(button, 'url', None)
        ]
        assert 'https://kaizenep.com/activities' in url_buttons
        assert not any('events/new-section/' in url for url in url_buttons)

    @pytest.mark.asyncio
    async def test_approval_partial_includes_same_case_another(self, thin_draft):
        """When handle_approval_approve processes a clean partial (no error),
        the post-filing keyboard must include the same-case another-WBA action
        so the user can get a different WPBA from the same case."""
        from bot import handle_approval_approve

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin_draft.form_type,
            'fields': thin_draft.fields,
            'uuid': thin_draft.uuid,
        }

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.record_case_filed', new=AsyncMock()), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 1, -1, 'pro_plus'))), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'partial',
                 'filled': ['date_of_encounter', 'clinical_setting', 'reflection'],
                 'skipped': ['clinical_reasoning'],
                 'method': 'deterministic',
                 # No error — clean partial, save landed
             }):
            await handle_approval_approve(update, context)

        buttons = [
            (row[0].text, row[0].callback_data)
            for row in sim.messages_sent[-1][2].inline_keyboard
            if row
        ]
        assert ('💾 Save as another WBA', 'ACTION|same_case_another') in buttons, (
            f"Clean partial should surface 'Same case' button. Got: {buttons!r}"
        )

    @pytest.mark.asyncio
    async def test_approval_success_includes_same_case_another(self):
        """When handle_approval_approve processes a success, the post-filing
        keyboard must also include the same-case another-WBA action."""
        from bot import _DRAFT_DIVIDER, handle_approval_approve
        from models import FormDraft

        thin = FormDraft(
            form_type='CBD',
            uuid='uuid-cbd',
            fields={
                'date_of_encounter': '2026-03-17',
                'clinical_setting': 'ED',
                'patient_presentation': 'Chest pain',
                'clinical_reasoning': 'Managed as ACS, escalated appropriately.',
                'reflection': 'Need faster ECG review.',
                'curriculum_links': ['SLO1'],
                'key_capabilities': ['SLO1 KC1: Assess and stabilise the patient'],
            },
        )

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin.form_type,
            'fields': thin.fields,
            'uuid': thin.uuid,
        }

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.record_case_filed', new=AsyncMock()), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 1, -1, 'pro_plus'))), \
             patch('bot.get_case_history', new=AsyncMock(return_value=[])), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'success',
                 'filled': ['date_of_encounter', 'clinical_setting', 'reflection'],
                 'skipped': [],
                 'method': 'deterministic',
             }):
            await handle_approval_approve(update, context)

        buttons = [
            (row[0].text, (row[0].callback_data or ''))
            for row in sim.messages_sent[-1][2].inline_keyboard
            if row
        ]
        assert ('💾 Save as another WBA', 'ACTION|same_case_another') in buttons, (
            f"Success should surface 'Same case' button. Got: {buttons!r}"
        )
        """Successful save also reads as a new step: 'Kaizen draft saved' on top, then
        the form-name subhead, then summary lines — distinct from the draft."""
        from bot import handle_approval_approve
        from models import FormDraft

        thin = FormDraft(
            form_type='CBD',
            uuid='uuid-cbd',
            fields={
                'date_of_encounter': '2026-03-17',
                'clinical_setting': 'ED',
                'patient_presentation': 'Chest pain',
                'clinical_reasoning': 'Managed as ACS, escalated appropriately.',
                'reflection': 'Need faster ECG review.',
                'curriculum_links': ['SLO1'],
                'key_capabilities': ['SLO1 KC1: Assess and stabilise the patient'],
            },
        )

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin.form_type,
            'fields': thin.fields,
            'uuid': thin.uuid,
        }

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.record_case_filed', new=AsyncMock()), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 1, -1, 'pro_plus'))), \
             patch('bot.get_case_history', new=AsyncMock(return_value=[])), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'success',
                 'filled': ['date_of_encounter', 'clinical_setting', 'reflection'],
                 'skipped': [],
                 'method': 'deterministic',
             }):
            await handle_approval_approve(update, context)

        text = sim.get_last_text()
        first_line = text.split('\n', 1)[0]
        assert '✅ Kaizen draft saved' in first_line, (
            f"First line should be the 'Kaizen draft saved' header. Got: {first_line!r}"
        )
        # The subhead mentions the form was saved as a Kaizen draft so the
        # user knows nothing was submitted/signed.
        assert 'saved as a Kaizen draft' in text
        assert _DRAFT_DIVIDER not in text

    @pytest.mark.asyncio
    async def test_saved_confirmation_tells_user_when_today_was_used_for_missing_date(self):
        from bot import handle_approval_approve
        from models import FormDraft

        thin = FormDraft(
            form_type='CBD',
            uuid='uuid-cbd',
            fields={
                'date_of_encounter': '',
                'clinical_setting': 'ED',
                'patient_presentation': 'Chest pain',
                'clinical_reasoning': 'Managed as ACS.',
                'reflection': 'Need faster ECG review.',
                'curriculum_links': ['SLO1'],
                'key_capabilities': ['SLO1 KC1: Assess and stabilise the patient'],
            },
        )

        sim = BotSimulator()
        update = sim._make_callback_update('APPROVE|draft')
        context = sim._make_context()
        context.user_data['case_text'] = SAMPLE_CASES['valid']
        context.user_data['draft_data'] = {
            '_type': 'FORM',
            'form_type': thin.form_type,
            'fields': thin.fields,
            'uuid': thin.uuid,
        }

        with patch('bot.get_credentials', return_value=('user', 'pass')), \
             patch('bot.record_case_filed', new=AsyncMock()), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 1, -1, 'pro_plus'))), \
             patch('bot.get_case_history', new=AsyncMock(return_value=[])), \
             patch('bot.route_filing', new_callable=AsyncMock, return_value={
                 'status': 'success',
                 'filled': ['date_of_encounter', 'end_date', 'event_description', 'clinical_reasoning', 'reflection'],
                 'skipped': [],
                 'method': 'deterministic',
                 'activity_date': '2026-05-26',
                 'defaulted_fields': ['date_of_encounter', 'end_date'],
             }):
            await handle_approval_approve(update, context)

        text = sim.get_last_text()
        assert "I used today's date (2026-05-26) because no case date was given." in text

    def test_post_filing_keyboard_flag_missed_field_button_removed_from_primary(self):
        """The '🚩 Flag a missed field' button is removed from the primary
        success and clean partial post-filing keyboards — filing pushback
        telemetry can still be recorded via other channels but doesn't
        clutter the immediate post-filing confirmation."""
        from bot import _build_post_filing_keyboard

        for status in ('success', 'partial'):
            keyboard = _build_post_filing_keyboard('CBD', status)
            buttons = [
                (b.text, b.callback_data)
                for row in keyboard.inline_keyboard
                for b in row
            ]
            assert ('🚩 Flag a missed field', 'FILING|feedback|CBD') not in buttons, (
                f"status={status!r} keyboard should NOT expose the flag button "
                f"on the primary post-filing keyboard. Got: {buttons!r}"
            )

    def test_post_filing_keyboard_omits_flag_button_when_filing_failed(self):
        """A hard failure means nothing saved — there's no draft, so a
        "missed field" pushback isn't meaningful. The flag button stays off."""
        from bot import _build_post_filing_keyboard

        keyboard = _build_post_filing_keyboard('CBD', 'failed')
        callbacks = {b.callback_data for row in keyboard.inline_keyboard for b in row}
        assert 'FILING|feedback|CBD' not in callbacks

    def test_post_filing_keyboard_has_no_duplicate_file_another_case(self):
        """'File another case' must appear at most once in any post-filed
        keyboard — the user reported it was showing up in both primary and
        the old More-options drawer."""
        from bot import _build_post_filing_keyboard

        for status, kwargs in (
            ('success', {'same_case_available': True}),
            ('success', {'same_case_available': False}),
            ('partial', {}),
            ('partial', {'uncertain': True}),
            ('failed', {}),
        ):
            keyboard = _build_post_filing_keyboard('CBD', status, **kwargs)
            file_again_count = sum(
                1
                for row in keyboard.inline_keyboard
                for b in row
                if (b.callback_data or '') == 'ACTION|file'
            )
            assert file_again_count <= 1, (
                f"status={status!r} kwargs={kwargs!r} should not duplicate "
                f"'File another case'. Got count={file_again_count}"
            )

    def test_post_filing_keyboard_no_feedback_buttons_on_success(self):
        """Feedback buttons ('It worked' / 'Didn't work') must not appear on
        the primary post-filing keyboard. Open/amend/same-case/new-case
        actions remain. Stale FEEDBACK| callbacks on older messages are still
        handled by handle_feedback's self-strip logic, so removing them from
        the keyboard doesn't break existing messages."""
        from bot import _build_post_filing_keyboard

        for kwargs in (
            {'same_case_available': True, 'saved_url': 'https://kaizenep.com/events/fillin/123'},
            {'same_case_available': False, 'saved_url': None},
        ):
            keyboard = _build_post_filing_keyboard('CBD', 'success', **kwargs)
            callbacks = {
                b.callback_data
                for row in keyboard.inline_keyboard
                for b in row
                if b.callback_data
            }
            assert not any(cb.startswith('FEEDBACK|') for cb in callbacks), (
                f"Success keyboard must not contain FEEDBACK buttons. Got: {callbacks!r}"
            )
            # Core follow-up actions remain
            assert 'ACTION|file' in callbacks, "File another case must remain"

    def test_post_filing_keyboard_partial_never_had_feedback_buttons(self):
        """Partial-save keyboard never included feedback buttons — verify
        unchanged after the success-path cleanup."""
        from bot import _build_post_filing_keyboard

        keyboard = _build_post_filing_keyboard('CBD', 'partial')
        callbacks = {
            b.callback_data
            for row in keyboard.inline_keyboard
            for b in row
            if b.callback_data
        }
        assert not any(cb.startswith('FEEDBACK|') for cb in callbacks)

    @pytest.mark.asyncio
    async def test_same_case_another_reuses_original_case_text_not_draft(
        self, recommended_forms
    ):
        """Acceptance #5: the Same-case-another button must reuse the user's
        ORIGINAL submitted case text — never the saved draft text or a
        bot-generated draft — and route back to the assessment-type
        recommendation step so the user doesn't re-send the case."""
        from bot import AWAIT_FORM_CHOICE, handle_same_case_another

        original_case_text = (
            "45M with epigastric pain radiating to back, raised lipase, "
            "started IV fluids and analgesia, admitted under surgery."
        )

        sim = BotSimulator()
        context = sim._make_context()
        # Post-filed state: original case + the form just filed sit on
        # last_filed_*; the user_data also carries the bot-generated draft
        # text so we can confirm we're NOT reusing that.
        context.user_data['last_filed_case_text'] = original_case_text
        context.user_data['last_filed_form_type'] = 'CBD'
        context.user_data['last_draft_preview'] = (
            'Bot-generated draft body — must NOT be reused as case text.'
        )

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'), \
             patch(
                 'bot.recommend_form_types',
                 new_callable=AsyncMock,
                 return_value=recommended_forms,
             ) as recommend_mock:
            result = await handle_same_case_another(
                sim._make_callback_update('ACTION|same_case_another'), context
            )

        # Routes back to the recommendation/assessment-type selection step.
        assert result == AWAIT_FORM_CHOICE
        # The recommender saw the ORIGINAL case text, not the bot draft.
        recommend_mock.assert_awaited()
        called_with = recommend_mock.await_args.args[0]
        assert called_with == original_case_text, (
            f"Expected recommender to receive the original case text, "
            f"got: {called_with!r}"
        )
        # The previously filed form type is excluded so the user is offered a
        # different WPBA type.
        assert context.user_data.get('excluded_form_type') == 'CBD'
        # case_text was restored from last_filed_case_text — not the draft body.
        assert context.user_data.get('case_text') == original_case_text

    @pytest.mark.asyncio
    async def test_same_case_transition_message_is_replaced_by_form_list(
        self, recommended_forms
    ):
        from bot import AWAIT_FORM_CHOICE, handle_same_case_another

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['last_filed_case_text'] = SAMPLE_CASES['valid']
        context.user_data['last_filed_form_type'] = 'REFLECT_LOG'

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.recommend_form_types', new_callable=AsyncMock, return_value=recommended_forms):
            result = await handle_same_case_another(
                sim._make_callback_update('ACTION|same_case_another'), context
            )

        assert result == AWAIT_FORM_CHOICE
        assert sim.messages_sent[-1][0] == 'bot_edit'
        assert 'Forms that fit your case' in sim.messages_sent[-1][1]
        assert 'Reusing the same case' not in sim.messages_sent[-1][1]

    @pytest.mark.asyncio
    async def test_same_case_stale_button_explains_expiry_without_dead_end(self):
        from bot import handle_action_button, handle_same_case_another

        sim = BotSimulator()
        context = sim._make_context()

        with patch('bot._setup_needs_finishing', return_value=False):
            result = await handle_same_case_another(
                sim._make_callback_update('ACTION|same_case_another'), context
            )

        assert result == ConversationHandler.END
        text = sim.get_last_text()
        assert 'same-case shortcut has expired' in text
        assert 'Send the case again' in text
        assert 'no longer available here' not in text

    @pytest.mark.asyncio
    async def test_stale_see_all_forms_restores_last_filed_case_context(self):
        from bot import AWAIT_FORM_CHOICE, handle_form_choice

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['last_filed_case_text'] = SAMPLE_CASES['valid']
        context.user_data['last_filed_form_type'] = 'REFLECT_LOG'

        with patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'):
            result = await handle_form_choice(
                sim._make_callback_update('FORM|show_all'), context
            )

        assert result == AWAIT_FORM_CHOICE
        assert context.user_data['case_text'] == SAMPLE_CASES['valid']
        assert context.user_data['excluded_form_type'] == 'REFLECT_LOG'
        assert 'Pick a category' in sim.get_last_text()

    @pytest.mark.asyncio
    async def test_stale_form_selection_without_case_gives_restart_path(self):
        from bot import handle_form_choice

        sim = BotSimulator()
        context = sim._make_context()

        with patch('bot._setup_needs_finishing', return_value=False):
            result = await handle_form_choice(
                sim._make_callback_update('FORM|CBD'), context
            )

        assert result == ConversationHandler.END
        text = sim.get_last_text()
        assert 'form list has expired' in text
        assert 'Start a new case' in text

    @pytest.mark.asyncio
    async def test_paused_flow_restores_last_filed_case_before_recommending(
        self, recommended_forms
    ):
        from bot import AWAIT_FORM_CHOICE, _resume_paused_flow

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data['last_filed_case_text'] = SAMPLE_CASES['valid']
        context.user_data['last_filed_form_type'] = 'REFLECT_LOG'

        with patch('bot._setup_needs_finishing', return_value=False), \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.recommend_form_types', new=AsyncMock(return_value=recommended_forms)):
            result = await _resume_paused_flow(
                sim._make_callback_update('FORM|CBD'),
                context,
                'That earlier button is no longer active.',
            )

        assert result == AWAIT_FORM_CHOICE
        assert context.user_data['case_text'] == SAMPLE_CASES['valid']
        assert context.user_data['case_input_source'] == 'same case'
        assert context.user_data['excluded_form_type'] == 'REFLECT_LOG'
        assert 'Your case is still in progress' in sim.messages_sent[-2][1]
        assert 'Pick one' in sim.messages_sent[-1][1]


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
        # Auto-detected portfolio shown in welcome message
        assert 'hst portfolio profile' in sim.get_last_text().lower()


class TestTrainingStageGroups:
    def test_unknown_training_level_is_displayed_as_unknown(self):
        from bot import _settings_view_components

        with patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.get_training_level', return_value=None), \
             patch('bot.get_voice_profile', return_value=None):
            text, _ = _settings_view_components(123)

        assert 'Portfolio: Unknown' in text

    def test_unlimited_settings_labels_cases_as_filed_not_usage(self):
        from bot import _settings_view_components

        with patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_voice_profile', return_value=None):
            text, _ = _settings_view_components(123, tier='pro_plus', used=10)

        assert 'Plan: Unlimited' in text
        assert 'Cases filed: 10 this month' in text
        assert 'Usage: 10 cases this month' not in text

    @pytest.mark.asyncio
    async def test_training_level_options_use_kaizen_stage_groups(self):
        from bot import handle_action_button, handle_same_case_another

        sim = BotSimulator()
        update = sim._make_callback_update('ACTION|change_level')
        context = sim._make_context()

        await handle_action_button(update, context)

        buttons = sim.get_last_buttons()
        assert ('ACCS Profile', 'SETLEVEL|ACCS') in buttons
        assert ('Intermediate Profile', 'SETLEVEL|INTERMEDIATE') in buttons
        assert ('HST Profile', 'SETLEVEL|HIGHER') in buttons

    def test_settings_layout_prioritises_voice_profile(self):
        from bot import _settings_view_components

        with patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.get_training_level', return_value=None), \
             patch('bot.get_voice_profile', return_value=None):
            text, keyboard = _settings_view_components(123)

        buttons = [(b.text, b.callback_data) for row in keyboard.inline_keyboard for b in row]
        assert buttons[0] == ('✍️ Writing style: Not set', 'ACTION|voice')
        assert ('📚 Curriculum: 2025 Update', 'ACTION|change_curriculum') in buttons
        assert 'Helps drafts sound like you' in text


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
    async def test_forwarded_voice_audio_enters_new_case_flow(self, recommended_forms):
        from bot import AWAIT_FORM_CHOICE, handle_case_input

        sim = BotSimulator()
        context = sim._make_context()
        update = sim._make_text_update('')
        audio = MagicMock()
        audio.file_name = "forwarded-voice.oga"
        audio.mime_type = "audio/ogg"
        file_obj = MagicMock()
        file_obj.download_to_drive = AsyncMock()
        audio.get_file = AsyncMock(return_value=file_obj)
        update.message.text = None
        update.message.voice = None
        update.message.audio = audio
        update.message.photo = []
        update.message.document = None

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.transcribe_voice', new=AsyncMock(return_value=SAMPLE_CASES['valid'])) as transcribe_mock, \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.recommend_form_types', new=AsyncMock(return_value=recommended_forms)):
            result = await handle_case_input(update, context)

        assert result == AWAIT_FORM_CHOICE
        transcribe_mock.assert_awaited_once()
        assert context.user_data['case_input_source'] == 'voice'
        assert 'fit your case' in (sim.get_last_text() or '').lower()
        assert 'send a text message' not in (sim.get_last_text() or '').lower()

    @pytest.mark.asyncio
    async def test_voice_capture_reuses_voice_status_message(self, recommended_forms):
        from bot import AWAIT_FORM_CHOICE, CAPTURED_ACK, handle_case_input

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
             patch('bot.transcribe_voice', new=AsyncMock(return_value=SAMPLE_CASES['valid'])), \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.recommend_form_types', new=AsyncMock(return_value=recommended_forms)):
            result = await handle_case_input(update, context)

        assert result == AWAIT_FORM_CHOICE
        captured_replies = [
            text for kind, text, _ in sim.messages_sent
            if kind == 'reply' and text == CAPTURED_ACK
        ]
        assert captured_replies == []
        bot_edits = [text for kind, text, _ in sim.messages_sent if kind == 'bot_edit' and text]
        assert CAPTURED_ACK in bot_edits
        assert 'fit your case' in (sim.get_last_text() or '').lower()

    @pytest.mark.asyncio
    async def test_forwarded_voice_document_enters_new_case_flow(self, recommended_forms):
        from bot import AWAIT_FORM_CHOICE, handle_case_input

        sim = BotSimulator()
        context = sim._make_context()
        update = sim._make_text_update('')
        document = MagicMock()
        document.file_name = "forwarded-voice.oga"
        document.mime_type = "audio/ogg"
        file_obj = MagicMock()
        file_obj.download_to_drive = AsyncMock()
        document.get_file = AsyncMock(return_value=file_obj)
        update.message.text = None
        update.message.voice = None
        update.message.audio = None
        update.message.photo = []
        update.message.document = document

        with patch('bot.has_credentials', return_value=True), \
             patch('bot.check_can_file', new=AsyncMock(return_value=(True, 0, 10, 'free'))), \
             patch('bot.transcribe_voice', new=AsyncMock(return_value=SAMPLE_CASES['valid'])) as transcribe_mock, \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.recommend_form_types', new=AsyncMock(return_value=recommended_forms)):
            result = await handle_case_input(update, context)

        assert result == AWAIT_FORM_CHOICE
        transcribe_mock.assert_awaited_once()
        assert context.user_data['case_input_source'] == 'voice'
        assert 'fit your case' in (sim.get_last_text() or '').lower()
        assert 'file type' not in (sim.get_last_text() or '').lower()

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


class TestVoiceProfileTwoPathFlow:
    """Settings → Set up voice profile must offer two clear paths:
    Learn from Kaizen entries (read-only, action-gated) or Add examples
    manually (the existing 3-5 examples flow).
    """

    @pytest.mark.asyncio
    async def test_voice_command_shows_two_path_choice_for_fresh_user(self):
        from bot import AWAIT_VOICE_EXAMPLES, voice_start

        sim = BotSimulator()
        update = sim._make_text_update('/voice')
        context = sim._make_context()

        with patch('bot.get_voice_profile', return_value=None):
            result = await voice_start(update, context)

        assert result == AWAIT_VOICE_EXAMPLES
        buttons = sim.get_last_buttons()
        assert ('🤖 Learn from Kaizen entries', 'VOICE|path_kaizen') in buttons
        assert ('✍️ Add examples manually', 'VOICE|path_manual') in buttons
        assert ('🔙 Back to settings', 'VOICE|back_to_settings') in buttons
        assert ('❌ Cancel', 'VOICE|cancel') not in buttons
        text = sim.get_last_text() or ''
        assert 'Voice Profile Setup' in text
        assert 'read-only' in text.lower()
        assert "won't create" in text.lower()
        assert 'submit' in text.lower()
        # Fresh choice copy must NOT drop the user straight into the 3-5
        # examples brief — that path is now opt-in via VOICE|path_manual.
        assert 'Send 3-5 examples' not in text

    @pytest.mark.asyncio
    async def test_action_voice_routes_to_choice_screen(self):
        from bot import AWAIT_VOICE_EXAMPLES, voice_start

        sim = BotSimulator()
        update = sim._make_callback_update('ACTION|voice')
        context = sim._make_context()

        with patch('bot.get_voice_profile', return_value=None):
            result = await voice_start(update, context)

        assert result == AWAIT_VOICE_EXAMPLES
        update.callback_query.answer.assert_awaited_once()
        assert sim.messages_sent[-1][0] == 'bot_edit'
        buttons = sim.get_last_buttons()
        assert ('🤖 Learn from Kaizen entries', 'VOICE|path_kaizen') in buttons
        assert ('✍️ Add examples manually', 'VOICE|path_manual') in buttons
        assert ('🔙 Back to settings', 'VOICE|back_to_settings') in buttons

    @pytest.mark.asyncio
    async def test_voice_command_for_existing_profile_offers_paths_and_remove(self):
        from bot import AWAIT_VOICE_EXAMPLES, voice_start

        sim = BotSimulator()
        update = sim._make_text_update('/voice')
        context = sim._make_context()

        with patch('bot.get_voice_profile', return_value='{"voice_summary": "x"}'):
            result = await voice_start(update, context)

        assert result == AWAIT_VOICE_EXAMPLES
        buttons = sim.get_last_buttons()
        assert ('🤖 Learn from Kaizen entries', 'VOICE|path_kaizen') in buttons
        assert ('✍️ Add examples manually', 'VOICE|path_manual') in buttons
        assert ('🗑️ Remove Profile', 'VOICE|remove') in buttons
        assert ('🔙 Back to settings', 'VOICE|back_to_settings') in buttons
        assert ('❌ Cancel', 'VOICE|cancel') not in buttons

    @pytest.mark.asyncio
    async def test_manual_path_preserves_existing_3_to_5_examples_flow(self):
        from bot import AWAIT_VOICE_EXAMPLES, voice_collect_example

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|path_manual')
        context = sim._make_context()

        result = await voice_collect_example(update, context)

        assert result == AWAIT_VOICE_EXAMPLES
        assert context.user_data.get('voice_examples') == []
        text = sim.get_last_text() or ''
        assert 'Add examples manually' in text
        assert 'Send 3-5 examples' in text
        assert [
            ('🔙 Back', 'VOICE|back_to_choice'),
        ] in _last_button_rows(sim)
        # The Kaizen path gate must NOT be set from the manual path — those
        # are independent contracts.
        assert context.user_data.get('voice_kaizen_path_started') is None

    @pytest.mark.asyncio
    async def test_voice_choice_back_returns_to_settings(self):
        from bot import ConversationHandler, voice_collect_example

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|back_to_settings')
        context = sim._make_context()
        context.user_data['voice_examples'] = ['draft example']
        context.user_data['voice_kaizen_path_started'] = True

        with patch('bot.get_user_tier', new_callable=AsyncMock, return_value='free'), \
             patch('bot.get_cases_this_month', new_callable=AsyncMock, return_value=1), \
             patch('bot.has_credentials', return_value=True), \
             patch('bot.get_curriculum', return_value='2025'), \
             patch('bot.get_training_level', return_value='ST5'), \
             patch('bot.get_voice_profile', return_value=None):
            result = await voice_collect_example(update, context)

        assert result == ConversationHandler.END
        assert context.user_data.get('voice_examples') is None
        assert context.user_data.get('voice_kaizen_path_started') is None
        assert 'Your settings' in (sim.get_last_text() or '')
        assert ('🔙 Back', 'ACTION|back_to_menu') in sim.get_last_buttons()

    @pytest.mark.asyncio
    async def test_kaizen_path_opens_sample_size_choice_with_read_only_copy(self):
        from bot import AWAIT_VOICE_EXAMPLES, voice_collect_example

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|path_kaizen')
        context = sim._make_context()

        result = await voice_collect_example(update, context)

        assert result == AWAIT_VOICE_EXAMPLES
        text = sim.get_last_text() or ''
        # The sample-size screen must carry the lightweight safety wording, so
        # the Kaizen button itself acts as the user's choice/action.
        assert 'Pick a sample size' in text
        assert 'read-only' in text.lower()
        assert 'no creating' in text.lower()
        assert 'submitting' in text.lower()

        buttons = sim.get_last_buttons()
        assert ('✅ I consent — pick sample', 'VOICE|kaizen_consent') not in buttons
        assert ('📋 Recent 10 entries', 'VOICE|kaizen_sample|recent_10') in buttons
        assert ('📅 Last 6 months', 'VOICE|kaizen_sample|last_6m') in buttons
        assert ('📅 Last 12 months', 'VOICE|kaizen_sample|last_12m') in buttons
        assert [
            ('🔙 Back', 'VOICE|back_to_choice'),
        ] in _last_button_rows(sim)
        assert context.user_data.get('voice_kaizen_path_started') is True

    @pytest.mark.asyncio
    async def test_kaizen_sample_requires_path_choice_first(self):
        """Stale sample-pick buttons must not bypass the Kaizen path choice."""
        from bot import AWAIT_VOICE_EXAMPLES, voice_collect_example

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|kaizen_sample|recent_10')
        context = sim._make_context()
        # No Kaizen path state set — simulate a stale callback.

        with patch('voice_sampler.sample_kaizen_entries',
                   new_callable=AsyncMock) as sampler:
            result = await voice_collect_example(update, context)
            sampler.assert_not_called()

        assert result == AWAIT_VOICE_EXAMPLES
        text = (sim.get_last_text() or '').lower()
        assert 'sample option is no longer active' in text

    @pytest.mark.asyncio
    async def test_kaizen_sample_invokes_sampler_without_live_browser(self):
        """The flow must call the sampler boundary, not touch Kaizen directly."""
        from bot import AWAIT_VOICE_EXAMPLES, voice_collect_example
        from voice_sampler import SampleWindow, SamplerResult, SamplerStatus

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|kaizen_sample|recent_10')
        context = sim._make_context()
        context.user_data['voice_kaizen_path_started'] = True

        fake_result = SamplerResult(
            status=SamplerStatus.NOT_AVAILABLE,
            window=SampleWindow.RECENT_10,
            message='Kaizen learning isn\'t switched on yet.',
        )
        with patch('voice_sampler.sample_kaizen_entries',
                   new_callable=AsyncMock, return_value=fake_result) as sampler:
            result = await voice_collect_example(update, context)
            sampler.assert_awaited_once()

        assert result == AWAIT_VOICE_EXAMPLES
        text = sim.get_last_text() or ''
        assert "isn't switched on yet" in text or 'manually' in text
        buttons = sim.get_last_buttons()
        assert ('✍️ Add examples manually', 'VOICE|path_manual') in buttons

    @pytest.mark.asyncio
    async def test_kaizen_login_required_offers_inline_reconnect(self):
        from bot import AWAIT_VOICE_EXAMPLES, voice_collect_example
        from voice_sampler import SampleWindow, SamplerResult, SamplerStatus

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|kaizen_sample|recent_10')
        context = sim._make_context()
        context.user_data['voice_kaizen_path_started'] = True

        fake_result = SamplerResult(
            status=SamplerStatus.NOT_AVAILABLE,
            window=SampleWindow.RECENT_10,
            message='Kaizen needs reconnecting before I can learn from previous entries.',
            reason='login_required',
        )
        with patch(
            'voice_sampler.sample_kaizen_entries',
            new_callable=AsyncMock,
            return_value=fake_result,
        ):
            result = await voice_collect_example(update, context)

        assert result == AWAIT_VOICE_EXAMPLES
        buttons = sim.get_last_buttons()
        assert ('🔗 Reconnect Kaizen', 'ACTION|setup') in buttons
        assert ('✍️ Add examples manually', 'VOICE|path_manual') in buttons
        assert [
            ('🔙 Back', 'VOICE|back_to_choice'),
        ] in _last_button_rows(sim)

    @pytest.mark.asyncio
    async def test_build_voice_profile_activates_immediately_without_approval_gate(self):
        """The approval gate is gone: a successful profile build must save the
        profile straight away and not stash anything as pending."""
        from bot import _build_voice_profile

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|done')
        context = sim._make_context()
        context.user_data['voice_examples'] = ['one', 'two', 'three']

        with patch(
            'voice_profile.generate_voice_profile',
            new_callable=AsyncMock,
            return_value='{"voice_summary": "x"}',
        ), patch(
            'bot._generate_voice_preview',
            new_callable=AsyncMock,
            return_value='Sample draft text.',
        ), patch('bot.store_voice_profile') as store:
            result = await _build_voice_profile(update, context)

        store.assert_called_once()
        args, _ = store.call_args
        assert args[0] == sim.user_id
        assert args[1] == '{"voice_summary": "x"}'
        assert args[2] == 3
        assert result == ConversationHandler.END
        assert context.user_data.get('pending_voice_profile') is None
        assert context.user_data.get('voice_examples') is None

        buttons = sim.get_last_buttons()
        button_data = {data for _, data in buttons}
        assert 'VOICE|preview_accept' not in button_data
        assert 'VOICE|preview_reject' not in button_data
        text = sim.get_last_text() or ''
        assert 'does this sound like you' not in text.lower()
        assert 'looks like me' not in text.lower()
        assert 'not quite' not in text.lower()

    @pytest.mark.asyncio
    async def test_build_voice_profile_preview_frames_sample_as_demo_from_combined_samples(self):
        """Preview copy must explain the sample is a demo and the profile was
        built from combined writing patterns, not that one example/case."""
        from bot import _build_voice_profile

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|done')
        context = sim._make_context()
        context.user_data['voice_examples'] = ['one', 'two', 'three', 'four']

        with patch(
            'voice_profile.generate_voice_profile',
            new_callable=AsyncMock,
            return_value='{"voice_summary": "x"}',
        ), patch(
            'bot._generate_voice_preview',
            new_callable=AsyncMock,
            return_value='Reflective sample draft text.',
        ), patch('bot.store_voice_profile'):
            await _build_voice_profile(update, context)

        text = (sim.get_last_text() or '').lower()
        # Demo framing — the rendered sample is not the profile itself
        assert 'demo' in text or 'example' in text or 'sample' in text
        # Combined samples / writing-patterns framing
        assert (
            'combined' in text
            or 'across' in text
            or 'patterns' in text
            or 'all your' in text
        ), f"preview should explain combined-samples framing: {text!r}"
        # Activation confirmation up front
        assert 'activated' in text or 'active' in text

    @pytest.mark.asyncio
    async def test_build_voice_profile_offers_post_activation_recovery_buttons(self):
        """After activation the user must have clear next moves: drafting and
        a way back into voice setup (improve / rebuild)."""
        from bot import _build_voice_profile

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|done')
        context = sim._make_context()
        context.user_data['voice_examples'] = ['one', 'two', 'three']

        with patch(
            'voice_profile.generate_voice_profile',
            new_callable=AsyncMock,
            return_value='{"voice_summary": "x"}',
        ), patch(
            'bot._generate_voice_preview',
            new_callable=AsyncMock,
            return_value='Sample draft text.',
        ), patch('bot.store_voice_profile'):
            await _build_voice_profile(update, context)

        buttons = sim.get_last_buttons()
        button_data = {data for _, data in buttons}
        # Start drafting → reuse the existing "file a case" action
        assert 'ACTION|file' in button_data
        # Improve / rebuild → reuse the two-path voice setup entry point
        assert 'ACTION|voice' in button_data

    @pytest.mark.asyncio
    async def test_stale_preview_accept_without_pending_shows_clean_recovery(self):
        """A user tapping the old 'Activate' button after this change should
        not crash and should land on a useful next-step screen."""
        from bot import voice_collect_example

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|preview_accept')
        context = sim._make_context()
        # New flow never stashes pending_voice_profile.

        with patch('bot.store_voice_profile') as store:
            result = await voice_collect_example(update, context)

        store.assert_not_called()
        assert result == ConversationHandler.END
        text = (sim.get_last_text() or '').lower()
        assert 'voice profile' in text
        # No raw error/crash language
        assert 'expired' not in text or 'already' in text
        buttons = sim.get_last_buttons()
        button_data = {data for _, data in buttons}
        # Recovery must offer a way forward without dead ends
        assert 'ACTION|voice' in button_data or 'ACTION|file' in button_data

    @pytest.mark.asyncio
    async def test_stale_preview_accept_with_pending_still_activates_for_backwards_compat(self):
        """If an old in-flight preview still has pending_voice_profile in
        user_data, tapping Activate must still save the profile cleanly."""
        from bot import voice_collect_example

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|preview_accept')
        context = sim._make_context()
        context.user_data['pending_voice_profile'] = '{"voice_summary": "x"}'
        context.user_data['voice_examples'] = ['one', 'two', 'three']

        with patch('bot.store_voice_profile') as store:
            result = await voice_collect_example(update, context)

        store.assert_called_once()
        assert result == ConversationHandler.END
        assert context.user_data.get('pending_voice_profile') is None

    @pytest.mark.asyncio
    async def test_stale_preview_reject_shows_clean_recovery_not_retry(self):
        """The 'Not quite — try again' button is gone. Old taps must show a
        clean recovery message instead of dropping into the old retry path."""
        from bot import voice_collect_example

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|preview_reject')
        context = sim._make_context()
        # Simulate a stale tap — no pending profile, no examples buffered.

        result = await voice_collect_example(update, context)

        assert result == ConversationHandler.END
        text = sim.get_last_text() or ''
        buttons = sim.get_last_buttons()
        button_data = {data for _, data in buttons}
        # Old retry path must not reappear
        assert ('🔄 Try Again', 'VOICE|path_manual') not in buttons
        # Must offer a path back into voice setup without crashing
        assert 'ACTION|voice' in button_data
        # Must not echo the old "Does this sound like you?" framing
        assert 'does this sound like you' not in text.lower()

    @pytest.mark.asyncio
    async def test_voice_preview_uses_plain_text_without_markdown_or_fence(self):
        from bot import _build_voice_profile

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|done')
        context = sim._make_context()
        context.user_data['voice_examples'] = ['one', 'two', 'three']

        with patch(
            'voice_profile.generate_voice_profile',
            new_callable=AsyncMock,
            return_value='{"voice_summary": "x"}',
        ), patch(
            'bot._generate_voice_preview',
            new_callable=AsyncMock,
            return_value='**Reflection**\nSample preview text.\n---',
        ), patch('bot.store_voice_profile'):
            result = await _build_voice_profile(update, context)

        assert result == ConversationHandler.END
        text = sim.get_last_text() or ''
        assert 'Reflection' in text
        assert '**Reflection**' not in text
        assert 'Preview draft' not in text
        assert '────────────' not in text
        assert '---' not in text

    @pytest.mark.asyncio
    async def test_voice_sampler_uses_mocked_read_only_runner(self):
        """Normal tests must not touch live Kaizen; the runner is mockable."""
        from voice_sampler import SampleWindow, SamplerStatus, sample_kaizen_entries

        with patch(
            'voice_sampler._run_browser_harness',
            return_value={'status': 'ok', 'samples': ['Reflective example text']},
        ) as runner:
            result = await sample_kaizen_entries(123, SampleWindow.RECENT_10)

        runner.assert_called_once()
        assert result.status == SamplerStatus.OK
        assert result.samples == ['Reflective example text']

    @pytest.mark.asyncio
    async def test_voice_sampler_auto_reconnects_when_session_expired(self):
        from voice_sampler import SampleWindow, SamplerStatus, sample_kaizen_entries

        with patch(
            'voice_sampler._run_browser_harness',
            side_effect=[
                {'status': 'not_available', 'reason': 'login_required', 'samples': []},
                {'status': 'ok', 'samples': ['Recovered reflective example']},
            ],
        ) as runner, patch(
            'voice_sampler._restore_kaizen_session',
            return_value={'ok': True},
        ) as restore:
            result = await sample_kaizen_entries(123, SampleWindow.RECENT_10)

        assert runner.call_count == 2
        restore.assert_called_once_with(123)
        assert result.status == SamplerStatus.OK
        assert result.samples == ['Recovered reflective example']

    @pytest.mark.asyncio
    async def test_voice_sampler_only_shows_reconnect_after_auto_reconnect_fails(self):
        from voice_sampler import SampleWindow, SamplerStatus, sample_kaizen_entries

        with patch(
            'voice_sampler._run_browser_harness',
            return_value={'status': 'not_available', 'reason': 'login_required', 'samples': []},
        ) as runner, patch(
            'voice_sampler._restore_kaizen_session',
            return_value={'ok': False, 'reason': 'credentials_rejected'},
        ):
            result = await sample_kaizen_entries(123, SampleWindow.RECENT_10)

        runner.assert_called_once()
        assert result.status == SamplerStatus.NOT_AVAILABLE
        assert result.reason == 'credentials_rejected'
        assert 'Reconnect Kaizen' in result.message

    def test_voice_sampler_browser_script_is_read_only(self):
        from voice_sampler import _browser_script

        script = _browser_script(10).lower()
        forbidden = ['submit(', '.click(', 'delete', 'save as draft', 'send to assessor', 'set_react_value', 'fill_input']
        offenders = [needle for needle in forbidden if needle in script]
        assert not offenders, "voice sampler script must stay read-only: " + ", ".join(offenders)

    @pytest.mark.asyncio
    async def test_long_text_in_voice_profile_exits_to_filing(self):
        """Sending a case-length text while in voice setup should exit the flow."""
        from bot import AWAIT_VOICE_EXAMPLES, voice_collect_example, ConversationHandler

        sim = BotSimulator()
        # Simulate user already in voice profile setup
        update = sim._make_text_update("A 45-year-old man presents with chest pain radiating to the left arm for 2 hours. He is diaphoretic and hypotensive. ECG shows ST elevation in anterior leads. I diagnosed anterior STEMI and activated the cath lab team. Thrombolysis was contraindicated due to recent surgery. Bloods showed elevated troponin. He was started on dual antiplatelets and referred to cardiology for urgent angiography. The patient had a history of hypertension and type 2 diabetes.")
        context = sim._make_context()
        context.user_data["voice_examples"] = ["I like concise summaries"]

        result = await voice_collect_example(update, context)

        assert result == ConversationHandler.END
        # Voice state should be cleaned up
        assert context.user_data.get("voice_examples") is None
        text = sim.get_last_text() or ""
        assert "exited" in text.lower()

    @pytest.mark.asyncio
    async def test_back_to_choice_drops_kaizen_path_state(self):
        from bot import AWAIT_VOICE_EXAMPLES, voice_collect_example

        sim = BotSimulator()
        update = sim._make_callback_update('VOICE|back_to_choice')
        context = sim._make_context()
        context.user_data['voice_kaizen_path_started'] = True

        with patch('bot.get_voice_profile', return_value=None):
            result = await voice_collect_example(update, context)

        assert result == AWAIT_VOICE_EXAMPLES
        assert context.user_data.get('voice_kaizen_path_started') is None
        buttons = sim.get_last_buttons()
        assert ('🤖 Learn from Kaizen entries', 'VOICE|path_kaizen') in buttons
        assert ('✍️ Add examples manually', 'VOICE|path_manual') in buttons
        assert ('🔙 Back to settings', 'VOICE|back_to_settings') in buttons
