"""Shared reply contracts for side questions across channels.

Telegram and WhatsApp should not maintain separate workflow heuristics for
ordinary setup/capability/portfolio/account questions. This module returns a
channel-neutral :class:`channel_actions.ChannelReply`; callers render it as
Telegram inline buttons or WhatsApp-safe text.

The contract is deliberately stricter than free generation and looser than
"identical message every time":

* workflow decisions, safety boundaries and action IDs are deterministic;
* high-risk copy remains static;
* lower-risk portfolio guidance can later flex inside the same required-facts
  contract without changing channel controls.
"""

from __future__ import annotations

from channel_actions import ChannelAction, ChannelReply
from conversational_router import ConversationalIntent, route_message
from message_policy import render_message, style_grounded_answer
from portfolio_first_contact import classify_first_contact, first_contact_reply


CONNECT_KAIZEN_ACTION = ChannelAction(
    action_id="ACTION|setup",
    label="🔗 Connect Kaizen",
)
SETTINGS_ACTION = ChannelAction(
    action_id="ACTION|settings",
    label="⚙️ Settings",
)

STATIC_COPY_INTENTS = frozenset(
    {
        ConversationalIntent.SETUP_OR_CREDENTIALS,
        ConversationalIntent.SAFETY_OR_MEDICAL_ADVICE,
        ConversationalIntent.ACCOUNT_OR_BILLING,
        ConversationalIntent.OUT_OF_SCOPE,
    }
)

FLEXIBLE_COPY_INTENTS = frozenset(
    {
        ConversationalIntent.PORTFOLIO_QUESTION,
        ConversationalIntent.HELP_OR_CAPABILITY,
        ConversationalIntent.EDIT_DRAFT,
        ConversationalIntent.FILE_TO_KAIZEN,
        ConversationalIntent.UNKNOWN,
    }
)


def copy_mode_for_intent(intent: ConversationalIntent) -> str:
    """Return whether copy for an intent should be static or controlled-flexible."""

    if intent in STATIC_COPY_INTENTS:
        return "static"
    if intent in FLEXIBLE_COPY_INTENTS:
        return "controlled-flexible"
    return "workflow-only"


def select_deterministic_reply(
    text: str | None,
    *,
    include_first_contact: bool = True,
) -> ChannelReply | None:
    """Return a deterministic side-question reply, or ``None`` for case flow.

    ``None`` deliberately means "let the normal clinical case/drafting path
    continue". It does not mean "ask the user for another screenshot".
    """

    if include_first_contact:
        onboarding = first_contact_reply(classify_first_contact(text))
        if onboarding is not None:
            return onboarding

    routed = route_message(text or "")
    intent = routed.intent

    if intent is ConversationalIntent.SETUP_OR_CREDENTIALS:
        return ChannelReply(
            body=render_message("kaizen_setup_guide"),
            actions=(CONNECT_KAIZEN_ACTION, SETTINGS_ACTION),
        )

    if intent is ConversationalIntent.PORTFOLIO_QUESTION:
        form_type = routed.signals.get("form_type")
        form_line = (
            f"\n\nYou mentioned {form_type}; send the case details and I'll check "
            "whether that is the best fit."
            if form_type
            else ""
        )
        return ChannelReply(
            body=style_grounded_answer(
                "I can help with RCEM portfolio evidence and WPBA drafts, including "
                "CBD, Mini-CEX, DOPS, reflective logs, teaching, QIP and related "
                "portfolio forms.\n\n"
                "Send rough anonymised case notes and I'll recommend the best-fit "
                f"form before drafting.{form_line}"
            )
        )

    if intent is ConversationalIntent.HELP_OR_CAPABILITY:
        return ChannelReply(body=render_message("capability_overview"))

    if intent is ConversationalIntent.SAFETY_OR_MEDICAL_ADVICE:
        return ChannelReply(body=render_message("medical_advice_refusal"))

    if intent is ConversationalIntent.ACCOUNT_OR_BILLING:
        return ChannelReply(
            body=style_grounded_answer(
                "The free plan includes 5 cases a month. Portfolio Guru Unlimited "
                "is £9.99/month for unlimited filing and premium features.\n\n"
                "For account, access, billing or subscription changes, use the main "
                "Portfolio Guru account/support flow rather than chat. I will not ask "
                "for payment details or Kaizen credentials here."
            )
        )

    if intent in {ConversationalIntent.EDIT_DRAFT, ConversationalIntent.FILE_TO_KAIZEN}:
        return ChannelReply(
            body=style_grounded_answer(
                "I don't have an active draft in this chat yet.\n\n"
                "Send the anonymised case details first. I'll prepare the draft "
                "for review, and nothing is saved or filed to Kaizen until you "
                "approve it."
            )
        )

    if intent is ConversationalIntent.OUT_OF_SCOPE:
        return ChannelReply(body=render_message("prompt_injection_refusal"))

    if intent is ConversationalIntent.UNKNOWN:
        return ChannelReply(
            body=style_grounded_answer(
                routed.clarification
                or (
                    "I can help draft portfolio evidence, answer portfolio questions, "
                    "edit a draft, or prepare a Kaizen draft. Which would you like "
                    "to do?"
                )
            )
        )

    return None
