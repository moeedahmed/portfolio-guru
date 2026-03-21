from unittest.mock import AsyncMock, patch

import pytest
from telegram.ext import ConversationHandler

from tests.bot_simulator import BotSimulator


@pytest.fixture
def fixed_recommendations():
    from extractor import FORM_UUIDS
    from models import FormTypeRecommendation

    return [
        FormTypeRecommendation(
            form_type="CBD",
            rationale="Best fit for a reflective clinical case.",
            uuid=FORM_UUIDS["CBD"],
        ),
        FormTypeRecommendation(
            form_type="ACAT",
            rationale="The case spans multiple patients across a shift.",
            uuid=FORM_UUIDS["ACAT"],
        ),
    ]


@pytest.fixture
def fixed_cbd_form_draft():
    from extractor import FORM_UUIDS
    from models import FormDraft

    return FormDraft(
        form_type="CBD",
        uuid=FORM_UUIDS["CBD"],
        fields={
            "date_of_encounter": "2026-03-17",
            "clinical_setting": "Emergency Department",
            "patient_presentation": "Central chest pain with diaphoresis",
            "stage_of_training": "Higher/ST4-ST6",
            "trainee_role": "I led the assessment with indirect supervision.",
            "clinical_reasoning": "I treated the presentation as acute coronary syndrome and escalated early to cardiology.",
            "reflection": "I would involve the cath lab team earlier if the ECG remains dynamic.",
            "level_of_supervision": "Indirect",
            "curriculum_links": ["SLO1", "SLO3"],
            "key_capabilities": [
                "SLO1 KC1: to be expert in assessing and managing all adult patients attending the ED. These capabilities will apply to patients attending with both physical and psychological ill health (2025 Update)",
                "SLO3 KC1: able to support the pre-hospital, medical, nursing and administrative team in answering clinical questions and in making safe decisions for patients with appropriate levels of risk in the ED (2025 Update)",
            ],
        },
    )


@pytest.mark.asyncio
async def test_start_message_snapshot(snapshot):
    from bot import start

    sim = BotSimulator()
    update = sim._make_text_update("/start")
    context = sim._make_context()

    with patch("bot.has_credentials", return_value=False):
        result = await start(update, context)

    assert result == ConversationHandler.END
    assert {
        "text": sim.get_last_text(),
        "buttons": sim.get_last_buttons(),
    } == snapshot


@pytest.mark.asyncio
async def test_form_recommendation_snapshot(snapshot, fixed_recommendations):
    from bot import handle_case_input

    sim = BotSimulator()
    update = sim._make_text_update(
        "I managed a full emergency department shift with multiple chest pain patients and reflected on escalation."
    )
    context = sim._make_context()

    with patch("bot.has_credentials", return_value=True), patch(
        "bot.classify_intent", new_callable=AsyncMock, return_value="case"
    ), patch(
        "bot.recommend_form_types",
        new_callable=AsyncMock,
        return_value=fixed_recommendations,
    ), patch("bot.get_training_level", return_value="ST5"), patch(
        "bot.get_curriculum", return_value="2025"
    ), patch(
        "bot.check_can_file", new_callable=AsyncMock, return_value=(True, 0, 5, "free")
    ):
        await handle_case_input(update, context)

    assert {
        "text": sim.get_last_text(),
        "buttons": sim.get_last_buttons(),
    } == snapshot


@pytest.mark.asyncio
async def test_draft_preview_snapshot(snapshot, fixed_cbd_form_draft):
    from bot import handle_form_choice

    sim = BotSimulator()
    update = sim._make_callback_update("FORM|CBD")
    context = sim._make_context()
    context.user_data["case_text"] = "Clinical case text"

    with patch(
        "bot._analyse_selected_form",
        new_callable=AsyncMock,
        return_value=fixed_cbd_form_draft,
    ), patch("bot._missing_template_fields", return_value=([], [], [])):
        await handle_form_choice(update, context)

    assert {
        "text": sim.get_last_text(),
        "buttons": sim.get_last_buttons(),
    } == snapshot
