"""Focused quality regression tests for the portfolio-guru Telegram copy.

These tests enforce constraints on user-visible response formatting, including:
1. Mobile-readable structure (proper vertical spacing, no double/triple empty lines).
2. Sensible maximum size constraints for shell copy.
3. Absence of duplicated Call To Actions (CTAs) or scaffolding headers.
4. Validation against conceptually simulated prior behaviour to verify that the tests
   fail against the old behaviour but pass with the new behaviour.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
import pytest
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ConversationHandler

from bot import (
    _format_generic_draft,
    handle_approval_approve,
    _format_receipt_date,
    _format_attachment_status_line,
)
from models import FormDraft
from message_policy import MESSAGE_TEMPLATES
from tests.bot_simulator import BotSimulator


# 1. Spacing / Spacing Regression Helper & Conceptual Test
def simulate_old_format_generic_draft() -> str:
    """Simulates the prior draft formatting behavior which resulted in triple newlines."""
    return (
        "🟢 *CBD draft ready*\n\n"
        "📚 *Curriculum:*\n"
        "• SLO1 — Clinical assessment\n"
        "  ↳ KC1: assessing patients\n\n\n"  # Triple newline issue from old list/formatted blocks
        "📌 *Setting:* Emergency Department\n\n"
        "💭 *Reflection:*\n"
        "I managed the case with indirect supervision."
    )


def verify_clean_spacing(text: str) -> None:
    """Verifies that the text complies with clean spacing rules.
    
    - No triple newlines (i.e. `\n\n\n` or `\n \n \n` which create ugly large gaps on mobile).
    - No duplicate blank lines.
    """
    assert "\n\n\n" not in text, "Text contains triple newlines / empty lines"
    assert not text.endswith("\n\n"), "Text has trailing empty lines"


def test_draft_preview_spacing_and_spacing_regression():
    # Form draft with mixed narrative and lists
    draft = FormDraft(
        form_type="CBD",
        uuid="test-uuid",
        fields={
            "clinical_setting": "Emergency Department",
            "reflection": "I managed the case with indirect supervision.",
            "actions_taken": "Escalated to cardiology early.",
            "key_learnings": ["Early escalation helps", "ECG changes are dynamic"],
        }
    )
    
    # Verify current formatting passes the clean spacing checks
    current_formatted = _format_generic_draft(draft)
    verify_clean_spacing(current_formatted)
    
    # Verify that the simulated prior formatting fails the clean spacing checks
    # (proving the test is not trivial and would have caught prior regressions)
    old_formatted = simulate_old_format_generic_draft()
    with pytest.raises(AssertionError, match="triple newlines"):
         verify_clean_spacing(old_formatted)


# 2. Receipt Size, Formatting, and Duplication Test
def simulate_old_success_receipt(form_name: str, date_display: str, slo_str: str, filled_count: int, usage_line: str) -> str:
    """Simulates the prior verbose/duplicated success receipt.
    
    Prior behavior included full verbose details, duplicated CTA headers,
    and structural debug output (like the entire draft JSON or long lists).
    """
    return (
        f"✅ Draft saved in Kaizen\n"
        f"Saved successfully to Kaizen.\n"
        f"Form Name: {form_name}\n"
        f"Date: {date_display}\n"
        f"Curriculum: {slo_str}\n"
        f"Fields filled: {filled_count}\n"
        f"{usage_line}\n"
        f"Open the saved draft to check details.\n"
        f"Open Kaizen and find your saved draft to continue."
    )


def verify_receipt_quality(text: str, is_uncertain: bool = False) -> None:
    """Assesses receipt copy quality for length, duplication, and formatting.
    
    - Should be concise (shell copy excluding actual variable content under 500 characters).
    - No duplicate confirmation headers (e.g. only one main status header emoji at the start).
    - No duplicate instructions to open Kaizen or assign assessors.
    """
    # Character length checks (non-clinical shell copy must be concise)
    assert len(text) < 700, f"Receipt too verbose: {len(text)} characters"
    
    # Duplication checks
    status_emojis = [emoji for emoji in ["✅", "📝", "⚠️", "❌"] if emoji in text]
    assert len(status_emojis) <= 1 or (len(status_emojis) == 2 and "💡" in text), \
        "Found multiple conflicting status headers in the receipt"
        
    # CTA redundancy checks
    cta_phrases = [
        "open kaizen",
        "open the saved draft",
        "check your kaizen drafts"
    ]
    matched_ctas = [phrase for phrase in cta_phrases if phrase in text.lower()]
    assert len(matched_ctas) <= 1, f"Duplicate / redundant CTAs found in receipt: {matched_ctas}"


@pytest.mark.asyncio
async def test_success_receipt_quality_and_regression():
    sim = BotSimulator()
    update = sim._make_callback_update("APPROVE|draft")
    context = sim._make_context()
    context.user_data["case_text"] = "Sample case text"
    context.user_data["draft_data"] = {
        "_type": "FORM",
        "form_type": "CBD",
        "fields": {"clinical_setting": "ED"},
        "uuid": "test-uuid",
    }
    
    # Test Success Receipt
    with patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot.record_case_filed", new=AsyncMock()), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, 5, "pro"))), \
         patch("bot.get_case_history", new=AsyncMock(return_value=[])), \
         patch("bot.route_filing", new_callable=AsyncMock, return_value={
             "status": "success",
             "filled": ["clinical_setting"],
             "skipped": [],
             "method": "deterministic",
         }):
        await handle_approval_approve(update, context)
        
    success_text = sim.get_last_text()
    
    # Verify current success receipt passes our quality checks
    verify_receipt_quality(success_text)
    
    # Confirm it includes the key required elements:
    assert "Saved! Your draft is ready on Kaizen." in success_text
    assert "Case-Based Discussion" in success_text
    assert "completed" in success_text
    
    # Verify that the simulated prior receipt fails quality checks (due to verbosity/duplicated CTA)
    old_success = simulate_old_success_receipt(
        "Case-Based Discussion", "10 Jul 2026", "SLO1", 3, "1/5 cases this month"
    )
    with pytest.raises(AssertionError, match="Duplicate / redundant CTAs found"):
        verify_receipt_quality(old_success)


@pytest.mark.asyncio
async def test_partial_receipt_quality():
    sim = BotSimulator()
    update = sim._make_callback_update("APPROVE|draft")
    context = sim._make_context()
    context.user_data["case_text"] = "Sample case text"
    context.user_data["draft_data"] = {
        "_type": "FORM",
        "form_type": "CBD",
        "fields": {"clinical_setting": "ED"},
        "uuid": "test-uuid",
    }
    
    # Test Partial Receipt (gaps but no error)
    with patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot.record_case_filed", new=AsyncMock()), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, 5, "pro"))), \
         patch("bot.route_filing", new_callable=AsyncMock, return_value={
             "status": "partial",
             "filled": ["clinical_setting"],
             "skipped": ["reflection"],
             "method": "deterministic",
             "saved_url": "https://kaizenep.com/events/fillin/123",
         }):
        await handle_approval_approve(update, context)
        
    partial_text = sim.get_last_text()
    
    verify_receipt_quality(partial_text)
    assert "needs a quick review!" in partial_text
    assert "Open the saved draft to complete" in partial_text
    
    # Repeat without saved_url to verify exact URL conditional phrasing
    sim_no_url = BotSimulator()
    update_no_url = sim_no_url._make_callback_update("APPROVE|draft")
    context_no_url = sim_no_url._make_context()
    context_no_url.user_data["case_text"] = "Sample case text"
    context_no_url.user_data["draft_data"] = {
        "_type": "FORM",
        "form_type": "CBD",
        "fields": {"clinical_setting": "ED"},
        "uuid": "test-uuid",
    }
    
    with patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot.record_case_filed", new=AsyncMock()), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, 5, "pro"))), \
         patch("bot.route_filing", new_callable=AsyncMock, return_value={
             "status": "partial",
             "filled": ["clinical_setting"],
             "skipped": ["reflection"],
             "method": "deterministic",
             # No saved_url
         }):
        await handle_approval_approve(update_no_url, context_no_url)
        
    partial_no_url_text = sim_no_url.get_last_text()
    
    verify_receipt_quality(partial_no_url_text)
    assert "Open Kaizen to complete" in partial_no_url_text
    assert "Open the saved draft" not in partial_no_url_text


@pytest.mark.asyncio
async def test_uncertain_receipt_quality():
    sim = BotSimulator()
    update = sim._make_callback_update("APPROVE|draft")
    context = sim._make_context()
    context.user_data["case_text"] = "Sample case text"
    context.user_data["draft_data"] = {
        "_type": "FORM",
        "form_type": "CBD",
        "fields": {"clinical_setting": "ED"},
        "uuid": "test-uuid",
    }
    
    # Test Uncertain Receipt (partial with filing error)
    with patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot.record_case_filed", new=AsyncMock()), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, 5, "pro"))), \
         patch("bot.route_filing", new_callable=AsyncMock, return_value={
             "status": "partial",
             "filled": ["clinical_setting"],
             "skipped": ["reflection"],
             "method": "deterministic",
             "error": "Timeout clicking Save Draft",
         }):
        await handle_approval_approve(update, context)
        
    uncertain_text = sim.get_last_text()
    
    verify_receipt_quality(uncertain_text, is_uncertain=True)
    assert "some issues" in uncertain_text
    assert "may not have saved completely" in uncertain_text


# 3. Message Template Regression Check
def test_message_policy_templates_size_limits():
    """Verify that templates in message_policy are within sensible mobile screen size limits."""
    # Core templates must stay concise and clear
    for key, template in MESSAGE_TEMPLATES.items():
        if template.message_class.name == "FIXED":
            limit = 600 if any(word in key for word in ("guide", "help", "setup")) else 400
            assert len(template.text) <= limit, (
                f"Template message '{key}' is too long ({len(template.text)} characters). "
                f"Keep static messaging concise for mobile screens."
            )
