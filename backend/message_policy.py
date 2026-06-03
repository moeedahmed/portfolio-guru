"""User-facing message policy for Portfolio Guru.

The bot keeps workflow decisions deterministic, but the copy should still be
consistent: short, mobile-first, one action per message, and explicit about
privacy/source limits. This module is intentionally small; it is a policy and
template layer, not a free-form message generator.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from string import Formatter


class MessageClass(str, Enum):
    FIXED = "fixed"
    TEMPLATED = "templated"
    LLM_ASSISTED = "llm_assisted"


@dataclass(frozen=True)
class MessageTemplate:
    key: str
    text: str
    message_class: MessageClass
    safety_critical: bool = False
    parse_mode: str | None = None


MESSAGE_TEMPLATES: dict[str, MessageTemplate] = {
    "welcome_disconnected": MessageTemplate(
        key="welcome_disconnected",
        message_class=MessageClass.FIXED,
        text=(
            "🩺 Portfolio Guru — RCEM portfolio drafts from rough notes.\n\n"
            "Send a case by text, voice, photo, or document. I’ll match it to the right form "
            "(CBD, DOPS, Mini-CEX, ACAT, reflections, teaching, procedurals, and more) "
            "and draft only after you choose.\n\n"
            "I won’t invent clinical detail. Missing fields stay blank, and nothing is filed "
            "until you approve it.\n\n"
            "Your Kaizen login is encrypted and used only to save drafts — never shared.\n\n"
            "Tap 🔗 Connect to start."
        ),
        safety_critical=True,
    ),
    "welcome_connected": MessageTemplate(
        key="welcome_connected",
        message_class=MessageClass.FIXED,
        text=(
            "🩺 Portfolio Guru is ready.\n\n"
            "Send the case details in whatever format is easiest: text, voice, photo, or document.\n\n"
            "I’ll read what you send, suggest the best-fit portfolio options, then show buttons for what to do next. "
            "Send extra messages only if you want to add or correct case detail.\n\n"
            "I won’t invent clinical detail, and nothing goes to Kaizen until you approve it."
        ),
        safety_critical=True,
    ),
    "bot_profile_description": MessageTemplate(
        key="bot_profile_description",
        message_class=MessageClass.FIXED,
        text=(
            "Portfolio Guru turns rough case notes into RCEM WPBA drafts.\n\n"
            "Send text, voice, photo, or documents. It picks the right form, fills only "
            "supported details, and shows the draft before saving.\n\n"
            "45 RCEM forms. Draft-only until approval. Kaizen login encrypted, never shared."
        ),
        safety_critical=True,
    ),
    "bot_profile_short_description": MessageTemplate(
        key="bot_profile_short_description",
        message_class=MessageClass.FIXED,
        text="RCEM WPBA drafts from text, voice, photo, or document — draft-only until you approve.",
        safety_critical=True,
    ),
    "what_is_this": MessageTemplate(
        key="what_is_this",
        message_class=MessageClass.FIXED,
        text=(
            "🩺 Portfolio Guru turns clinical notes into RCEM portfolio drafts.\n\n"
            "Flow: send case → pick form → review draft → save to Kaizen.\n\n"
            "I won’t invent clinical detail — missing fields stay blank for you to complete.\n\n"
            "Nothing is filed until you approve it. Supervisor submission is always manual."
        ),
        safety_critical=True,
    ),
    "file_case_prompt": MessageTemplate(
        key="file_case_prompt",
        message_class=MessageClass.FIXED,
        text=(
            "📥 Send what happened — text, voice note, photo, or document.\n\n"
            "Include the patient’s presentation, what you did, outcome, and learning point if you have them."
        ),
    ),
    "captured_ack": MessageTemplate(
        key="captured_ack",
        message_class=MessageClass.FIXED,
        text=(
            "📥 *Captured.* I’ll turn this into portfolio evidence and flag missing details. "
            "Nothing goes to Kaizen until you approve it."
        ),
        safety_critical=True,
        parse_mode="Markdown",
    ),
    "thin_case_detail_request": MessageTemplate(
        key="thin_case_detail_request",
        message_class=MessageClass.FIXED,
        text=(
            "📋 I need a bit more clinical detail before drafting.\n\n"
            "Send rough notes with: patient/presentation, what you did, outcome, and what you learned."
        ),
        safety_critical=True,
    ),
    "thin_sdl_detail_request": MessageTemplate(
        key="thin_sdl_detail_request",
        message_class=MessageClass.FIXED,
        text=(
            "📖 Send rough notes for the self-directed learning reflection.\n\n"
            "Include what you read/watched/listened to, the main learning points, and how it will change your practice."
        ),
        safety_critical=True,
    ),
    "ai_temporarily_unavailable": MessageTemplate(
        key="ai_temporarily_unavailable",
        message_class=MessageClass.FIXED,
        text="⚠️ AI is temporarily unavailable. Try again, or pick a form manually.",
        safety_critical=True,
    ),
    "form_recommendation": MessageTemplate(
        key="form_recommendation",
        message_class=MessageClass.TEMPLATED,
        text="{opening}\n\n{recommendations}\n\n{closing}{privacy_nudge}",
        safety_critical=True,
    ),
    "photo_privacy_nudge": MessageTemplate(
        key="photo_privacy_nudge",
        message_class=MessageClass.FIXED,
        text=(
            "\n\nPrivacy check: I extracted this from a photo. Remove names, NHS numbers, "
            "DOBs or addresses before drafting."
        ),
        safety_critical=True,
    ),
    "draft_reply_hint": MessageTemplate(
        key="draft_reply_hint",
        message_class=MessageClass.FIXED,
        text="\n\n💬 Reply to refine this draft, or save/cancel before sending a new case.",
    ),
}


def render_message(key: str, **kwargs) -> str:
    template = MESSAGE_TEMPLATES[key]
    allowed = {field for _, field, _, _ in Formatter().parse(template.text) if field}
    safe_kwargs = {field: kwargs.get(field, "") for field in allowed}
    return template.text.format(**safe_kwargs)


def message_audit_summary() -> dict[str, int]:
    counts = {message_class.value: 0 for message_class in MessageClass}
    for template in MESSAGE_TEMPLATES.values():
        counts[template.message_class.value] += 1
    return counts


def plain_text_policy_violations() -> list[str]:
    """Return template keys where plain text contains raw Markdown markers."""
    violations: list[str] = []
    for key, template in MESSAGE_TEMPLATES.items():
        if template.parse_mode:
            continue
        if any(marker in template.text for marker in ("*", "`", "[")):
            violations.append(key)
    return violations
