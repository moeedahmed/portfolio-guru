"""Non-invasive conversational intent router contract.

Phase 1 keeps this module deliberately standalone: no Telegram handlers import
or call it yet. Later phases can route ordinary text through this contract
without changing the existing deterministic workflows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class ConversationalIntent(str, Enum):
    NEW_CASE = "new_case"
    PORTFOLIO_QUESTION = "portfolio_question"
    EDIT_DRAFT = "edit_draft"
    FILE_TO_KAIZEN = "file_to_kaizen"
    ACCOUNT_OR_BILLING = "account_or_billing"
    SETUP_OR_CREDENTIALS = "setup_or_credentials"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RouterResult:
    intent: ConversationalIntent
    confidence: float
    signals: dict[str, str] = field(default_factory=dict)
    clarification: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")


FORM_ALIASES: dict[str, tuple[str, ...]] = {
    "CBD": ("cbd", "case based discussion", "case-based discussion"),
    "MINI_CEX": ("mini-cex", "mini cex", "minicex"),
    "DOPS": ("dops", "procedure"),
    "REFLECT_LOG": ("reflection", "reflective log", "reflect log"),
    "TEACHING": ("teaching", "teach"),
    "QIP": ("qip", "quality improvement"),
    "MCR": ("mcr", "multi-source", "multisource"),
}

CLINICAL_TERMS = (
    "patient",
    "year old",
    "y/o",
    "yo",
    "presented",
    "diagnosed",
    "managed",
    "treated",
    "resus",
    "ed",
    "icu",
    "airway",
    "sepsis",
    "trauma",
    "chest pain",
    "shortness of breath",
    "abdominal pain",
    "fracture",
    "sedation",
    "central line",
    "cvc",
    "fascia iliaca",
    "ultrasound",
    "pocus",
    "procedure",
    "consultant",
    "supervisor",
)

PORTFOLIO_QUESTION_TERMS = (
    "what form",
    "which form",
    "forms would",
    "form would",
    "support",
    "map to",
    "curriculum",
    "slo",
    "key capability",
    "kc",
    "arcp",
    "portfolio",
    "kaizen",
    "wpba",
    "eportfolio",
)

EDIT_TERMS = (
    "make it",
    "rewrite",
    "revise",
    "edit",
    "shorter",
    "concise",
    "clearer",
    "more detailed",
    "professional",
    "change",
    "actually",
)

FILE_TERMS = (
    "file this",
    "file it",
    "save this",
    "save it",
    "send this",
    "send it",
    "submit this",
    "submit it",
    "put this in kaizen",
    "add this to kaizen",
    "log this",
    "create draft",
    "save draft",
)

ACCOUNT_TERMS = (
    "billing",
    "payment",
    "pay",
    "paid",
    "subscribe",
    "subscription",
    "plan",
    "price",
    "pricing",
    "tier",
    "limit",
    "access",
    "blocked",
    "trial",
    "usage",
)

SETUP_TERMS = (
    "setup",
    "set up",
    "connect",
    "credential",
    "credentials",
    "login",
    "password",
    "username",
    "kaizen login",
    "reconnect",
)

UNKNOWN_CLARIFICATION = (
    "I can help draft portfolio evidence, answer portfolio questions, edit a draft, "
    "or prepare a Kaizen draft. Which would you like to do?"
)


def route_message(message: str) -> RouterResult:
    """Classify an ordinary user message without side effects."""

    text = _normalise(message)
    if not text:
        return _unknown()

    form_type = _extract_form_type(text)

    if _contains_any(text, SETUP_TERMS):
        return RouterResult(
            intent=ConversationalIntent.SETUP_OR_CREDENTIALS,
            confidence=0.88,
            signals=_compact_signals(action="setup_credentials"),
        )

    if _contains_any(text, ACCOUNT_TERMS):
        return RouterResult(
            intent=ConversationalIntent.ACCOUNT_OR_BILLING,
            confidence=0.86,
            signals=_compact_signals(action="account_or_billing"),
        )

    if _contains_any(text, FILE_TERMS):
        return RouterResult(
            intent=ConversationalIntent.FILE_TO_KAIZEN,
            confidence=0.9,
            signals=_compact_signals(
                action="file_to_kaizen",
                form_type=form_type,
                target_draft="current",
            ),
        )

    if _looks_like_edit_request(text):
        return RouterResult(
            intent=ConversationalIntent.EDIT_DRAFT,
            confidence=0.84,
            signals=_compact_signals(
                action=_extract_edit_action(text),
                target_draft="current",
            ),
        )

    if _looks_like_portfolio_question(text):
        return RouterResult(
            intent=ConversationalIntent.PORTFOLIO_QUESTION,
            confidence=0.82,
            signals=_compact_signals(action="answer_question", form_type=form_type),
        )

    if _looks_like_case_description(text):
        return RouterResult(
            intent=ConversationalIntent.NEW_CASE,
            confidence=0.78,
            signals=_compact_signals(action="start_case", form_type=form_type),
        )

    return _unknown()


def _normalise(message: str) -> str:
    return re.sub(r"\s+", " ", message.strip().lower())


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _extract_form_type(text: str) -> str | None:
    for form_type, aliases in FORM_ALIASES.items():
        if any(alias in text for alias in aliases):
            return form_type
    return None


def _looks_like_edit_request(text: str) -> bool:
    return _contains_any(text, EDIT_TERMS) and (
        "it" in text.split() or "draft" in text or "this" in text
    )


def _extract_edit_action(text: str) -> str:
    if "shorter" in text or "concise" in text:
        return "make_concise"
    if "more detailed" in text:
        return "expand_detail"
    if "clearer" in text or "professional" in text:
        return "improve_wording"
    return "edit_draft"


def _looks_like_portfolio_question(text: str) -> bool:
    return "?" in text and _contains_any(text, PORTFOLIO_QUESTION_TERMS)


def _looks_like_case_description(text: str) -> bool:
    clinical_hits = sum(1 for term in CLINICAL_TERMS if term in text)
    has_patient_demographic = bool(re.search(r"\b\d{1,3}\s*([mf]|male|female)\b", text))
    enough_words = len(text.split()) >= 8
    return enough_words and (clinical_hits >= 2 or has_patient_demographic)


def _compact_signals(**signals: str | None) -> dict[str, str]:
    return {key: value for key, value in signals.items() if value}


def _unknown() -> RouterResult:
    return RouterResult(
        intent=ConversationalIntent.UNKNOWN,
        confidence=0.2,
        signals={},
        clarification=UNKNOWN_CLARIFICATION,
    )
