"""User-facing message policy for Portfolio Guru.

The bot keeps workflow decisions deterministic, but the copy should still be
consistent: short, mobile-first, one action per message, and explicit about
privacy/source limits. This module is intentionally small; it is a policy and
template layer, not a free-form message generator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from string import Formatter

# Every templated Portfolio Guru message leads with an emoji marker. A
# grounded free-form answer (e.g. an LLM reply routed through the
# conversation supervisor) must inherit the same standard rather than
# arriving as bare prose.
HOUSE_EMOJI = "🩺"

_LEADING_EMOJI = re.compile(
    "^\\s*["
    "\U0001F300-\U0001FAFF"  # pictographs / emoji (📋 📥 👋 💬 …)
    "\U00002600-\U000027BF"  # misc symbols + dingbats (✅ ✨ ⚠ …)
    "\U00002100-\U000021FF"  # letterlike + arrows (ℹ ↩ …)
    "\U00002B00-\U00002BFF"  # arrows + stars (⬅ ⭐ …)
    "\U0001F1E6-\U0001F1FF"  # regional indicators
    "]"
)

_DECORATIVE_EMOJI_RE = re.compile("[✨🤖🎉⭐]")


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
            "👋 Welcome to Portfolio Guru\n\n"
            "Send rough case notes as text, voice, photo or document. I’ll turn them into "
            "RCEM portfolio drafts across 45 forms, then ask you to approve before anything "
            "is saved to Kaizen."
        ),
        safety_critical=True,
    ),
    "welcome_connected": MessageTemplate(
        key="welcome_connected",
        message_class=MessageClass.FIXED,
        text=(
            "🩺 Ready.\n\n"
            "Send an anonymised case as text, voice, photo, or document."
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
    "source_grounding_detail_request": MessageTemplate(
        key="source_grounding_detail_request",
        message_class=MessageClass.TEMPLATED,
        text=(
            "📋 More clinical context needed\n\n"
            "Send rough notes with: patient/presentation, what you did, outcome, and learning."
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
            "\n\n🔒 Privacy check\nI extracted this from a photo. Remove names, NHS numbers, "
            "DOBs or addresses before drafting."
        ),
        safety_critical=True,
    ),
    "draft_reply_hint": MessageTemplate(
        key="draft_reply_hint",
        message_class=MessageClass.FIXED,
        text="\n\n💬 Reply to refine this draft, or save/cancel before sending a new case.",
    ),
    "capability_overview": MessageTemplate(
        key="capability_overview",
        message_class=MessageClass.FIXED,
        text=(
            "🩺 Portfolio Guru turns your case notes into RCEM portfolio drafts. I can:\n"
            "• collect a case across several messages\n"
            "• keep what you send separate from chat, so nothing is invented\n"
            "• recommend the best-fit WPBA form\n"
            "• show the draft for review before anything is saved\n\n"
            "Nothing goes to Kaizen until you approve it."
        ),
        safety_critical=True,
    ),
    "kaizen_setup_guide": MessageTemplate(
        key="kaizen_setup_guide",
        message_class=MessageClass.FIXED,
        text=(
            "🔗 Connect Kaizen\n\n"
            "Use the secure Connect Kaizen flow before you ask me to save drafts.\n\n"
            "1. Open Connect Kaizen.\n"
            "2. Enter your Kaizen username and password in the secure setup screen.\n"
            "3. Wait for the connection check to pass, then send an anonymised case.\n\n"
            "Safety notes:\n"
            "• Kaizen credentials are encrypted and not shown back in chat.\n"
            "• I only save Kaizen drafts after you review and approve them.\n"
            "• I never submit anything to a supervisor."
        ),
        safety_critical=True,
    ),
    "greeting_reply": MessageTemplate(
        key="greeting_reply",
        message_class=MessageClass.FIXED,
        text="👋 Hi — tell me what happened in the case, or ask what I can do.",
    ),
    "gathering_captured": MessageTemplate(
        key="gathering_captured",
        message_class=MessageClass.FIXED,
        text="📥 Captured. Add anything else before I draft this?",
    ),
    "attachment_captured": MessageTemplate(
        key="attachment_captured",
        message_class=MessageClass.TEMPLATED,
        text=(
            "📎 {attachment_label} attached.\n\n"
            "Add anonymised case details before I draft this.{context_note}"
        ),
    ),
    "gathering_continuation": MessageTemplate(
        key="gathering_continuation",
        message_class=MessageClass.FIXED,
        text="💬 Back to your case — add more detail when you’re ready.",
    ),
    "prompt_injection_refusal": MessageTemplate(
        key="prompt_injection_refusal",
        message_class=MessageClass.FIXED,
        text=(
            "🩺 I can’t share internal instructions. "
            "Send a case or choose a form to start a Kaizen draft."
        ),
        safety_critical=True,
    ),
    "medical_advice_refusal": MessageTemplate(
        key="medical_advice_refusal",
        message_class=MessageClass.FIXED,
        text=(
            "🩺 I can’t advise on medication doses, prescribing, diagnosis, or treatment. "
            "Use your local ED prescribing guidance and senior/pharmacy support. "
            "I can help turn anonymised case notes into a portfolio draft, with clinical decisions documented as your own."
        ),
        safety_critical=True,
    ),
    "scope_redirect": MessageTemplate(
        key="scope_redirect",
        message_class=MessageClass.FIXED,
        text=(
            "🩺 I can help with portfolio/admin work: send an anonymised case, "
            "ask about supported forms, or ask about Kaizen setup."
        ),
        safety_critical=True,
    ),
}


def render_message(key: str, **kwargs) -> str:
    template = MESSAGE_TEMPLATES[key]
    allowed = {field for _, field, _, _ in Formatter().parse(template.text) if field}
    safe_kwargs = {field: kwargs.get(field, "") for field in allowed}
    return template.text.format(**safe_kwargs)


def style_grounded_answer(body: str) -> str:
    """Make a free-form grounded answer follow the house emoji standard.

    Templated copy in this module already leads with an emoji; a grounded
    answer about forms, billing, or setup may arrive as bare prose. Prefix
    the house marker so a supervisor side-question answer is visually
    consistent with every other Portfolio Guru message, on every channel.
    Answers that already lead with an emoji are returned unchanged.
    """
    stripped = body.strip()
    if not stripped or _LEADING_EMOJI.match(stripped):
        return stripped
    return f"{HOUSE_EMOJI} {stripped}"


_PROMPT_INJECTION_RE = re.compile(
    r"\b(ignore previous|ignore all previous|system prompt|developer message|"
    r"reveal your prompt|jailbreak|pretend you are)\b"
)


def safety_redirect_text(text: str | None = None, *, intent: str | None = None) -> str:
    """Return deterministic copy for unsafe or out-of-scope prompts.

    Principle-based, not prompt-specific:
    - Prompt injection (by intent or text) → concise internal-instruction refusal.
    - Clinical dosing/advice → explicit refusal + ED/pharmacy redirect.
    - Off-topic/unknown → short scope redirect.
    Neither path invites case gathering or drafting.
    """
    lowered = (text or "").lower()
    if intent == "out_of_scope" or _PROMPT_INJECTION_RE.search(lowered):
        return render_message("prompt_injection_refusal")
    if intent == "safety_or_medical_advice" or re.search(
        r"\b(clinical advice|medical advice|what dose|dose of|prescribe|prescribing|treat|treatment|diagnose|diagnosis|safe for|is it safe|medication|drug)\b",
        lowered,
    ):
        return render_message("medical_advice_refusal")
    return render_message("scope_redirect")


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


def decorative_emoji_policy_violations() -> list[str]:
    """Return template keys that use decorative emoji instead of functional markers."""
    return [
        key
        for key, template in MESSAGE_TEMPLATES.items()
        if _DECORATIVE_EMOJI_RE.search(template.text)
    ]
