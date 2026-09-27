"""'Another form' must never re-offer a form already filed from the same case.

Regression cover for the live defect where a case sent as "Please file this as
a case-based discussion…" was saved as a CBD, and tapping "Another form"
re-read that sentence from the reused case text and offered CBD again.
"""

from unittest.mock import AsyncMock, patch

import pytest

from bot import AWAIT_FORM_CHOICE
from tests.bot_simulator import BotSimulator


EXPLICIT_CBD_CASE = (
    "Please file this as a case-based discussion. I reviewed a 68-year-old man "
    "with acute chest pain and an inferior STEMI in resus, activated the cath lab "
    "pathway, gave loading antiplatelets and discussed the ECG with my consultant."
)


@pytest.fixture(autouse=True)
def _isolated_storage(monkeypatch, tmp_path):
    from tests.helpers import isolate_bot_storage
    isolate_bot_storage(monkeypatch, tmp_path)


def _rec(form_type):
    from extractor import FORM_UUIDS
    from models import FormTypeRecommendation

    return FormTypeRecommendation(
        form_type=form_type, rationale=f"{form_type} fits.", uuid=FORM_UUIDS.get(form_type)
    )


def _form_callbacks(sim):
    return {cb for _, cb in sim.get_last_buttons() if cb and cb.startswith("FORM|")}


async def _tap_another_form(sim, context, recommend):
    from bot import handle_same_case_another

    update = sim._make_callback_update("ACTION|same_case_another")
    with patch("bot.recommend_form_types", new=recommend), \
         patch("bot.get_training_level", return_value="ST5"), \
         patch("bot.get_curriculum", return_value="2025"):
        return await handle_same_case_another(update, context)


@pytest.mark.asyncio
async def test_another_form_ignores_explicit_request_for_the_form_just_filed():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["last_filed_case_text"] = EXPLICIT_CBD_CASE
    context.user_data["last_filed_form_type"] = "CBD"

    recommend = AsyncMock(return_value=[_rec("CBD"), _rec("ACAT"), _rec("MINI_CEX")])
    result = await _tap_another_form(sim, context, recommend)

    assert result == AWAIT_FORM_CHOICE
    recommend.assert_awaited_once()
    assert context.user_data.get("chosen_form") != "CBD"
    callbacks = _form_callbacks(sim)
    assert "FORM|CBD" not in callbacks
    assert "FORM|ACAT" in callbacks or "FORM|MINI_CEX" in callbacks


@pytest.mark.asyncio
async def test_another_form_skips_every_form_already_filed_from_the_case():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["last_filed_case_text"] = EXPLICIT_CBD_CASE
    context.user_data["last_filed_form_type"] = "MINI_CEX"
    context.user_data["last_filed_form_types"] = ["CBD", "MINI_CEX"]

    recommend = AsyncMock(return_value=[_rec("CBD"), _rec("MINI_CEX"), _rec("ACAT")])
    result = await _tap_another_form(sim, context, recommend)

    assert result == AWAIT_FORM_CHOICE
    offered = [r.form_type for r in context.user_data["form_recommendations"]]
    assert offered == ["ACAT"]
    callbacks = _form_callbacks(sim)
    assert "FORM|CBD" not in callbacks
    assert "FORM|MINI_CEX" not in callbacks


@pytest.mark.asyncio
async def test_choosing_a_filed_form_from_a_stale_list_is_refused():
    from bot import handle_form_choice

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = EXPLICIT_CBD_CASE
    context.user_data["excluded_form_type"] = "MINI_CEX"
    context.user_data["excluded_form_types"] = ["CBD", "MINI_CEX"]

    result = await handle_form_choice(sim._make_callback_update("FORM|CBD"), context)

    assert result == AWAIT_FORM_CHOICE
    assert context.user_data.get("chosen_form") != "CBD"


@pytest.mark.asyncio
async def test_saving_from_another_form_remembers_all_forms_filed_for_the_case():
    from bot import handle_approval_approve

    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update({
        "case_text": EXPLICIT_CBD_CASE,
        "excluded_form_type": "CBD",
        "excluded_form_types": ["CBD"],
        "draft_data": {
            "_type": "FORM",
            "form_type": "ACAT",
            "fields": {"date_of_encounter": "2026-09-26", "clinical_setting": "Resus"},
            "uuid": "uuid-acat",
        },
    })

    with patch("bot.get_training_level", return_value="ST5"), \
         patch("bot.get_curriculum", return_value="2025"), \
         patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot.record_case_filed", new=AsyncMock()), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, -1, "pro_plus"))), \
         patch("bot.get_case_history", new=AsyncMock(return_value=[])), \
         patch("bot.route_filing", new_callable=AsyncMock, return_value={
             "status": "success",
             "filled": ["date_of_encounter"],
             "skipped": [],
             "method": "deterministic",
             "saved_url": "https://kaizenep.com/events/fillin/acat?autosave=1",
         }):
        await handle_approval_approve(sim._make_callback_update("APPROVE|draft"), context)

    assert context.user_data["last_filed_form_types"] == ["CBD", "ACAT"]
    assert "ACTION|same_case_another" in {cb for _, cb in sim.get_last_buttons()}


def test_filter_accepts_several_excluded_forms():
    from bot import _filter_recommendations_for_allowed_forms

    recs = [_rec("CBD"), _rec("MINI_CEX"), _rec("ACAT")]
    kept = _filter_recommendations_for_allowed_forms(
        recs, {"CBD", "MINI_CEX", "ACAT", "REFLECT_LOG"}, excluded_form={"CBD", "MINI_CEX_2021"}
    )
    assert [r.form_type for r in kept] == ["ACAT"]
