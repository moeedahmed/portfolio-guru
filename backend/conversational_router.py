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
    HELP_OR_CAPABILITY = "help_or_capability"
    SAFETY_OR_MEDICAL_ADVICE = "safety_or_medical_advice"
    EDIT_DRAFT = "edit_draft"
    FILE_TO_KAIZEN = "file_to_kaizen"
    ACCOUNT_OR_BILLING = "account_or_billing"
    SETUP_OR_CREDENTIALS = "setup_or_credentials"
    OUT_OF_SCOPE = "out_of_scope"
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

HELP_CAPABILITY_TERMS = (
    "help",
    "how does this work",
    "what can you do",
    "what do you do",
    "features",
    "upload",
    "voice",
    "voice note",
    "photo",
    "document",
    "pdf",
    "word document",
    "style",
    "writing style",
    "tone",
    "voice profile",
)

PROMPT_INJECTION_TERMS = (
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "developer message",
    "reveal your prompt",
    "jailbreak",
    "pretend you are",
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
    "account",
    "tier",
    "limit",
    "access",
    "blocked",
    "trial",
    "usage",
)

BILLING_TERMS = (
    "billing",
    "payment",
    "pay",
    "paid",
    "cost",
    "how much",
    "subscribe",
    "subscription",
    "price",
    "pricing",
    "card",
    "refund",
    "upgrade",
    "invoice",
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

SECURITY_TERMS = (
    "secure",
    "security",
    "privacy",
    "private",
    "data",
    "stored",
    "store",
    "encrypted",
    "encryption",
    "credential safety",
    "safe with my login",
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

    if _contains_any(text, PROMPT_INJECTION_TERMS):
        return RouterResult(
            intent=ConversationalIntent.OUT_OF_SCOPE,
            confidence=0.94,
            signals=_compact_signals(action="safe_redirect"),
        )

    if _contains_safety_medical_request(text):
        return RouterResult(
            intent=ConversationalIntent.SAFETY_OR_MEDICAL_ADVICE,
            confidence=0.9,
            signals=_compact_signals(action="medical_safety_redirect"),
        )

    # Classify a product question by its own clause, so a login delay in the
    # preceding activity does not turn an account or billing question into setup.
    product_question = _product_question(text)
    routing_text = product_question or text

    # Case evidence owns the turn unless a question asks about the product.
    if has_case_narrative(text):
        return RouterResult(
            intent=ConversationalIntent.NEW_CASE,
            confidence=0.9,
            signals=_compact_signals(action="start_case", form_type=form_type),
        )

    if _contains_any(routing_text, SETUP_TERMS):
        return RouterResult(
            intent=ConversationalIntent.SETUP_OR_CREDENTIALS,
            confidence=0.88,
            signals=_compact_signals(action="setup_credentials"),
        )

    if _looks_like_question(routing_text) and _contains_any(routing_text, SECURITY_TERMS):
        return RouterResult(
            intent=ConversationalIntent.SETUP_OR_CREDENTIALS,
            confidence=0.86,
            signals=_compact_signals(action="security_credentials"),
        )

    if _contains_any(routing_text, BILLING_TERMS + ACCOUNT_TERMS):
        return RouterResult(
            intent=ConversationalIntent.ACCOUNT_OR_BILLING,
            confidence=0.86,
            signals=_compact_signals(action="account_or_billing"),
        )

    if product_question:
        return RouterResult(
            intent=ConversationalIntent.HELP_OR_CAPABILITY,
            confidence=0.86,
            signals=_compact_signals(action="answer_capability"),
        )

    if _looks_like_form_help_request(text, form_type):
        return RouterResult(
            intent=ConversationalIntent.PORTFOLIO_QUESTION,
            confidence=0.82,
            signals=_compact_signals(action="answer_question", form_type=form_type),
        )

    if _looks_like_help_or_capability_question(text):
        return RouterResult(
            intent=ConversationalIntent.HELP_OR_CAPABILITY,
            confidence=0.86,
            signals=_compact_signals(action="answer_capability"),
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
    return any(_contains_term(text, term) for term in terms)


def _contains_term(text: str, term: str) -> bool:
    """Match intent terms without catching substrings inside clinical words.

    This keeps words like "planning" out of the billing "plan" route and
    "escalated" out of LAT/form-code routing.
    """
    if not term:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))


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


def _looks_like_form_help_request(text: str, form_type: str | None) -> bool:
    return bool(form_type) and _contains_any(
        text,
        (
            "help with",
            "need help",
            "support with",
            "can you help",
        ),
    )


def _looks_like_help_or_capability_question(text: str) -> bool:
    if text in {"help", "features"}:
        return True
    return _looks_like_question(text) and _contains_any(text, HELP_CAPABILITY_TERMS)


def _contains_safety_medical_request(text: str) -> bool:
    if _contains_any(
        text,
        (
            "clinical advice",
            "medical advice",
            "should i treat",
            "should i prescribe",
            "what dose",
            "is it safe",
            "treatment advice",
        ),
    ):
        return True
    return _looks_like_question(text) and bool(
        re.search(r"\b(diagnose|prescribe|treat|dose|safe)\b", text)
    )


def _looks_like_question(text: str) -> bool:
    return "?" in text or bool(_question_clauses(text))


def _question_clauses(text: str) -> tuple[str, ...]:
    """Find direct questions anywhere; 'how to' and 'why login was' narrate activity."""
    question_start = (
        r"^(?:(?:what|which|who)\b|"
        r"how\s+(?:do|does|did|can|could|should|will|is|are|much|many)\b|"
        r"(?:why|when|where)\s+(?:do|does|did|can|could|should|will|is|are|was|were)\b|"
        r"(?:can|could|do|does|is|are|will|should)\s+"
        r"(?:i|we|you|my|our|this|that|the|portfolio guru|kaizen)\b)"
    )
    # A question can follow a sentence or a joined clause, while an embedded
    # 'I taught which account to use' describes the activity's subject.
    clauses = (clause.strip() for clause in re.split(r"[?!.;:,]|\b(?:and|but)\b", text))
    return tuple(clause for clause in clauses if re.match(question_start, clause))


def _product_question(text: str) -> str | None:
    """Return the product question, excluding requests for evidence-writing help."""
    evidence_subjects = "|".join(re.escape(subject) for subject in (
        "case", *(alias for aliases in FORM_ALIASES.values() for alias in aliases),
    ))
    for question in _question_clauses(text):
        # The requested action's object decides the subject: reconnecting an
        # account to save a reflection asks for setup; helping with the
        # reflection about a login delay asks for evidence-writing help.
        if re.search(
            r"\b(?:(?:help|support)\s+(?:me\s+)?(?:with\s+)?(?:(?:write|draft)\s+)?|"
            r"(?:write|draft|rewrite)\s+)"
            r"(?:(?:a|my|this|the|our|handover|clinical|teaching)\s+)*"
            rf"(?:{evidence_subjects})\b",
            question,
        ):
            continue
        if _contains_any(question, SETUP_TERMS + ACCOUNT_TERMS + BILLING_TERMS + SECURITY_TERMS + (
            "upload", "save", "saving", "file", "filing", "submit", "bot", "app",
        )):
            return question
    return None


def _looks_like_case_description(text: str) -> bool:
    clinical_hits = sum(1 for term in CLINICAL_TERMS if _contains_term(text, term))
    has_patient_demographic = bool(re.search(r"\b\d{1,3}\s*([mf]|male|female)\b", text))
    return clinical_hits >= 2 or has_patient_demographic


def _compact_signals(**signals: str | None) -> dict[str, str]:
    return {key: value for key, value in signals.items() if value}


def has_case_narrative(message: str) -> bool:
    """Recognise narrated evidence before incidental product/navigation words.

    Reflection and team/teaching evidence need no patient demographic or
    minimum clinical-keyword count. Require narrated activity so requests like
    'Can you help with a reflection?' remain questions.
    """
    text = _normalise(message)
    # Filing commands may include demographics, but are instructions about a
    # draft rather than new evidence. Leave them to the existing filing route.
    if _contains_any(text, FILE_TERMS):
        return False
    if _product_question(text):
        return False
    narrated_activity = bool(re.search(
        r"\b(?:i|we)\s+(?:had|saw|assessed|managed|treated|reviewed|reflected|taught|"
        r"learnt|learned|clarified|delivered|attended|performed|led|observed)\b",
        text,
    ))
    evidence_context = _contains_any(text, CLINICAL_TERMS + (
        "reflection", "reflected", "handover", "handovers", "teaching",
        "simulation", "simulated", "learning",
    ))
    reflection_notes = not _looks_like_question(text) and bool(re.search(
        r"(?:^reflection\b|^reflective log\b|\breflection\s*:\s*\S|\bmy learning\b)",
        text,
    ))
    return reflection_notes or (narrated_activity and evidence_context) or (
        not _looks_like_question(text) and _looks_like_case_description(text)
    )


def _unknown() -> RouterResult:
    return RouterResult(
        intent=ConversationalIntent.UNKNOWN,
        confidence=0.2,
        signals={},
        clarification=UNKNOWN_CLARIFICATION,
    )
