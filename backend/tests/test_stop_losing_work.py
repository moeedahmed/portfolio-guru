"""Batch A, "stop losing work": nothing a doctor sent may vanish silently.

Offline and deterministic: the Kaizen filer and the Telegram bot are stubbed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

import bot
from kaizen_form_filer import FORM_FIELD_MAP, unfiled_schema_fields
from tests.bot_simulator import BotSimulator


@pytest.fixture(autouse=True)
def _isolated_flow_storage(monkeypatch, tmp_path):
    # Other modules (test_smoke) re-import `bot`; patch("bot.x") targets the
    # live module, so use that one rather than the import from collection time.
    global bot
    import importlib
    import sys
    bot = sys.modules.get("bot") or importlib.import_module("bot")
    from tests.helpers import isolate_bot_storage
    isolate_bot_storage(monkeypatch, tmp_path)


# --- 1. Fields with no Kaizen target are reported, never "Saved" ------------


@pytest.mark.parametrize(
    "form_type, fields, expected",
    [
        ("AUDIT", {"reflection": "What I learned"}, ["reflection"]),
        ("RESEARCH", {"reflection": "What I learned"}, ["reflection"]),
        ("QIAT", {"qi_journey_aspects": ["Measurement"]}, ["qi_journey_aspects"]),
        ("MSF", {"context": "Team feedback", "reflection": "Themes"}, ["context", "reflection"]),
        ("MINI_CEX", {"complexity": "Moderate"}, ["complexity"]),
    ],
)
def test_unmapped_non_empty_schema_fields_are_listed_as_gaps(form_type, fields, expected):
    assert unfiled_schema_fields(form_type, fields, FORM_FIELD_MAP[form_type]) == expected


def test_empty_values_and_fields_handled_elsewhere_are_not_gaps():
    gaps = unfiled_schema_fields(
        "AUDIT",
        {
            "reflection": "",
            "curriculum_links": ["SLO1"],
            "key_capabilities": ["SLO1 KC1"],
            "stage_of_training": "Higher",
        },
        FORM_FIELD_MAP["AUDIT"],
    )
    assert gaps == []


def test_mapped_fields_are_never_gaps():
    mapped = next(iter(FORM_FIELD_MAP["AUDIT"]))
    assert unfiled_schema_fields("AUDIT", {mapped: "text"}, FORM_FIELD_MAP["AUDIT"]) == []


@pytest.mark.asyncio
async def test_audit_reflection_makes_the_filing_partial_not_clean():
    """End to end through file_to_kaizen: the gap reaches `skipped`, so the bot
    cannot call the save complete."""
    import kaizen_form_filer as kff
    from tests.test_kaizen_save_confirmation import FakePage, _playwright_with

    page = FakePage()
    fields = {"reflection": "What I learned from the audit"}

    with patch("kaizen_form_filer.async_playwright", return_value=_playwright_with(page)), \
         patch("kaizen_form_filer.KAIZEN_USE_CDP", False), \
         patch("kaizen_form_filer._login", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._fill_field_legacy", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._save_form", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._resolve_procedural_skill_selects", new=AsyncMock(return_value=([], []))), \
         patch("kaizen_form_filer._verify_entry_saved", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._verify_filing_qa", new=AsyncMock(return_value={"gaps": []})), \
         patch("kaizen_form_filer._required_field_gaps", new=AsyncMock(return_value=[])), \
         patch("kaizen_form_filer.asyncio.sleep", new=AsyncMock()):
        result = await kff.file_to_kaizen("AUDIT", fields, "user", "pass")

    assert "reflection" in result["skipped"]
    assert result["status"] != "success"


# --- 3. A dead draft address is forgotten, so Retry starts fresh -------------


@pytest.mark.asyncio
async def test_retry_after_failed_reopen_starts_a_fresh_form():
    from models import FormDraft

    draft = FormDraft(form_type="CBD", uuid="uuid-cbd", fields={"clinical_reasoning": "Managed as ACS."})
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["draft_data"] = {
        "_type": "FORM", "form_type": draft.form_type, "fields": draft.fields, "uuid": draft.uuid,
    }
    context.user_data["kaizen_draft_url"] = "https://kaizenep.com/events/fillin/gone"

    route_filing = AsyncMock(side_effect=[
        {"status": "failed", "filled": [], "skipped": [], "method": "deterministic",
         "error": "Couldn't reopen the earlier draft", "reopen_failed": True},
        {"status": "success", "filled": ["clinical_reasoning"], "skipped": [], "method": "deterministic"},
    ])

    with patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot.route_filing", new=route_filing):
        await bot.handle_approval_approve(sim._make_callback_update("APPROVE|draft"), context)
        assert "kaizen_draft_url" not in context.user_data
        await bot.handle_callback(sim._make_callback_update("ACTION|retry_filing"), context)

    assert route_filing.await_args_list[1].kwargs["reuse_draft_url"] is None


# --- 5. The previous case never hijacks the next ------------------------------


def _text_update(sim, text):
    return sim._make_text_update(text)


@pytest.mark.asyncio
async def test_did_it_save_keeps_an_open_case_open():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update({
        "last_filing_status": "success",
        "last_filing_form_name": "CBD",
        "case_text": "New case: 60M with sepsis, I started antibiotics and fluids early.",
    })

    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 5, "free"))):
        result = await bot.handle_case_input(_text_update(sim, "Did it save?"), context)

    assert result == bot.AWAIT_FORM_CHOICE
    assert context.user_data["case_text"].startswith("New case")


@pytest.mark.asyncio
async def test_did_it_save_with_no_case_open_still_ends_cleanly():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["last_filing_status"] = "success"
    context.user_data["last_filing_form_name"] = "CBD"

    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 5, "free"))):
        result = await bot.handle_case_input(_text_update(sim, "Did it save?"), context)

    assert result == ConversationHandler.END
    assert any("saved to Kaizen as a draft" in t for _, t, _ in sim.messages_sent if t)


@pytest.mark.asyncio
async def test_reuse_phrase_mid_case_does_not_swap_in_the_last_case():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update({
        "last_filed_case_text": "Old filed case about chest pain.",
        "case_text": "Current open case about sepsis.",
    })

    with patch("bot._handle_reuse_request", new=AsyncMock()) as reuse, \
         patch("bot._kaizen_connected", return_value=False):
        await bot.handle_case_input(_text_update(sim, "also file as a DOPS"), context)

    reuse.assert_not_awaited()


@pytest.mark.asyncio
async def test_reuse_phrase_with_its_own_case_is_a_new_case_not_a_reuse():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["last_filed_case_text"] = "Old filed case about chest pain."
    text = (
        "Please file as a CBD: 72 year old female, sepsis from a urinary source, "
        "I reviewed her in resus, gave fluids and antibiotics, discussed with ICU, "
        "and reflected on early escalation and the lactate result."
    )

    with patch("bot._handle_reuse_request", new=AsyncMock()) as reuse, \
         patch("bot._kaizen_connected", return_value=False):
        await bot.handle_case_input(_text_update(sim, text), context)

    reuse.assert_not_awaited()


def test_starting_a_new_case_forgets_the_last_filing():
    context = MagicMock()
    context.user_data = {
        "last_filing_status": "success",
        "last_filing_form_name": "CBD",
        "last_filing_report": "r",
        "last_filed_case_text": "old",
        "last_filed_form_type": "CBD",
    }

    bot._forget_last_filing(context)

    assert context.user_data == {"last_filed_form_type": "CBD"}


@pytest.mark.asyncio
async def test_process_case_text_clears_last_filing_but_same_case_reuse_keeps_it():
    fresh = BotSimulator()._make_context()
    fresh.user_data.update({"last_filing_status": "success", "last_filed_case_text": "old"})
    reuse = BotSimulator()._make_context()
    reuse.user_data.update({"last_filing_status": "success", "last_filed_case_text": "old"})

    message = MagicMock()
    message.reply_text = AsyncMock()
    with patch("bot.extract_explicit_form_type", return_value="DOPS"), \
         patch("bot._store_explicit_form_choice_state"), \
         patch("bot._send_latest_message", new=AsyncMock()):
        await bot._process_case_text(message, fresh, 1, "A brand new case", "text")
        await bot._process_case_text(message, reuse, 1, "old", "same case")

    assert "last_filing_status" not in fresh.user_data
    assert "last_filed_case_text" not in fresh.user_data
    assert reuse.user_data["last_filed_case_text"] == "old"


@pytest.mark.asyncio
async def test_old_same_case_button_keeps_a_newer_open_case_after_last_filing_cleared():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["case_text"] = "newer case in progress"
    update = sim._make_callback_update("ACTION|same_case_another")

    with patch("bot._retire_clicked_keyboard", new=AsyncMock()), \
         patch("bot._resume_paused_flow", new=AsyncMock(return_value=bot.AWAIT_FORM_CHOICE)) as resume, \
         patch("bot._process_case_text", new=AsyncMock()) as process:
        await bot.handle_same_case_another(update, context)

    resume.assert_awaited_once()
    process.assert_not_called()
    assert context.user_data["case_text"] == "newer case in progress"


def test_rejected_upload_returns_to_a_pending_choice():
    context = MagicMock()
    context.user_data = {"_pending_doc": {"path": "/tmp/a.pdf"}, "case_text": "x"}
    assert bot._state_after_media_rejection(context) == bot.AWAIT_DOC_INTENT
    context.user_data = {"case_text": "x"}
    assert bot._state_after_media_rejection(context) == bot.AWAIT_FORM_CHOICE


@pytest.mark.asyncio
async def test_failed_reopen_forgets_amend_pointers_too():
    from models import FormDraft

    draft = FormDraft(form_type="CBD", uuid="uuid-cbd", fields={"clinical_reasoning": "Managed as ACS."})
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data["draft_data"] = {
        "_type": "FORM", "form_type": draft.form_type, "fields": draft.fields, "uuid": draft.uuid,
    }
    context.user_data.update({
        "amend_mode": True,
        "amend_draft_url": "https://kaizenep.com/events/fillin/gone",
        "last_amend_draft_url": "https://kaizenep.com/events/fillin/gone",
    })
    route_filing = AsyncMock(return_value={
        "status": "failed", "filled": [], "skipped": [], "error": "x", "reopen_failed": True,
    })

    with patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot.route_filing", new=route_filing):
        await bot.handle_approval_approve(sim._make_callback_update("APPROVE|draft"), context)

    for key in ("kaizen_draft_url", "amend_draft_url", "last_amend_draft_url"):
        assert key not in context.user_data
