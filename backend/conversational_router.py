"""Pure intent routing shared by capture and open-draft workflows."""

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
    "log in",
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

    # Workflow precedence is deliberate: an explicit filing instruction owns
    # the turn; a direct product question/complaint owns its own sentence;
    # otherwise activity evidence takes priority over incidental product words.
    if _explicit_filing_request(text):
        return RouterResult(
            intent=ConversationalIntent.FILE_TO_KAIZEN,
            confidence=0.9,
            signals=_compact_signals(
                action="file_to_kaizen", form_type=form_type, target_draft="current",
            ),
        )

    product_help = _product_help(text)
    if product_help:
        return product_help

    if _has_activity_evidence(text):
        return RouterResult(
            intent=ConversationalIntent.NEW_CASE,
            confidence=0.9,
            signals=_compact_signals(action="start_case", form_type=form_type),
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


_QUESTION_START = re.compile(
    r"^(?:(?:what|which|who)\b|"
    r"how\s+(?:do|does|did|can|could|should|will|is|are|much|many)\b|"
    r"(?:why|when|where)\s+(?:do|does|did|can|could|should|will|is|are|was|were|has|have)\b|"
    r"(?:can|could|would|do|does|did|is|are|was|were|will|should|has|have)(?:n?['’]t)?\s+"
    r"(?:i|we|you|my|our|this|that|the|portfolio guru|kaizen)\b)"
)


def _sentences(text: str) -> tuple[str, ...]:
    # Keep narrated clauses intact; split a conjunction, comma or colon only
    # before a direct question ("I taught handover, why is my account blocked?").
    return tuple(part.strip() for part in re.split(
        r"[?!.;]|\b(?:and|but)\s+(?=(?:how|why|what|when|where|can|could)\b)"
        # A comma or colon is narrative punctuation unless a question follows:
        # one ending in "?", or one about the doctor or their account
        # ("..., what was difficult was IV access" stays one clause).
        r"|[,:]\s*(?=(?:how|why|what|when|where|which|who|is|are|do|does|did|can|could|would|should|will|has|have)\b[^.;!?]*\?)"
        r"|[,:]\s*(?=(?:(?:how|why|what|when|where)\s+)?"
        r"(?:do|does|did|can|could|would|should|will|is|are|was|were|has|have)(?:n?['’]t)?\s+"
        r"(?:i|you|my|we|our)\b)", text,
    ) if part.strip())


def _question_clauses(text: str) -> tuple[str, ...]:
    return tuple(part for part in _sentences(text) if _QUESTION_START.match(part))


def _evidence_writing_request(sentence: str) -> bool:
    """The requested writing object matters, not incidental account/plan words."""
    action = re.search(
        r"\b(?:(?:help|support)\s+(?:me\s+)?(?:with\s+|(?:to\s+)?(?:write|draft|reflect)\s+)"
        r"|(?:write|draft|rewrite)\s+|reflect\s+on\s+)", sentence,
    )
    if not action:
        return False
    if re.search(r"\breflect\s+(?:on\s+)?$", action.group()):
        return True
    # Descriptive adjectives are unrestricted. Stop at the object's topic or
    # purpose so "help with my account to save a reflection" stays account help.
    subject = re.split(r"\b(?:about|on|to|because|for|so)\b", sentence[action.end():], maxsplit=1)[0]
    return _contains_any(subject, (
        "case", "activity", "experience", "draft", "reflection",
        *(alias for aliases in FORM_ALIASES.values() for alias in aliases),
    ))


def _explicit_filing_request(text: str) -> bool:
    prefix = r"^(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"
    filing = "|".join(re.escape(term) for term in FILE_TERMS + ("save to kaizen",))
    return any(re.match(prefix + rf"(?:{filing})\b", sentence) for sentence in _sentences(text))


def _billing_context(text: str) -> bool:
    return _contains_any(text, BILLING_TERMS) or bool(re.search(
        r"\b(?:what|which)\s+plan\s+(?:am i|is my account)\s+on\b|"
        r"\b(?:change|upgrade|cancel)\s+my\s+plan\b|"
        r"\bplan\s+price\b|\b(?:paid|free)\s+plan\b", text,
    ))


def _product_help(text: str) -> RouterResult | None:
    """Resolve direct questions first, then complaints/requests about the product.

    Narrated events with incidental product vocabulary never enter this pass.
    The result is shared with has_case_narrative so capture cannot undo it.
    """
    sentences = _sentences(text)
    questions = tuple(part for part in sentences if _QUESTION_START.match(part))
    requests = tuple(part for part in sentences if part not in questions and (
        re.match(r"^(?:please\s+)?(?:tell me|help|connect|reconnect|set up|cancel|change|upgrade)\b", part)
        or re.match(r"^i\s+(?:need|want)\b", part)
        or _own_product_complaint(part)
        or part in SETUP_TERMS + ACCOUNT_TERMS + BILLING_TERMS
    ))
    for sentence in questions + requests:
        if _evidence_writing_request(sentence):
            continue
        topic = None
        if _billing_context(sentence):
            intent, action = ConversationalIntent.ACCOUNT_OR_BILLING, "account_or_billing"
            topic = "billing"
        elif _contains_any(sentence, SETUP_TERMS):
            action = "setup_credentials"
            # A reconnect or login failure concerns an existing account. New
            # connection/setup and credential-security questions retain setup.
            if _contains_term(sentence, "reconnect") or _own_product_complaint(sentence) or (
                _contains_any(sentence, ("log in", "login"))
                and re.search(r"\b(?:can(?:not|'t)|unable|fail\w*|blocked)\b", sentence)
            ):
                intent, topic = ConversationalIntent.ACCOUNT_OR_BILLING, "account"
            else:
                intent = ConversationalIntent.SETUP_OR_CREDENTIALS
        elif _contains_any(sentence, SECURITY_TERMS):
            intent, action = ConversationalIntent.SETUP_OR_CREDENTIALS, "security_credentials"
        elif _contains_any(sentence, ACCOUNT_TERMS) or _own_product_complaint(sentence):
            intent, action = ConversationalIntent.ACCOUNT_OR_BILLING, "account_or_billing"
            topic = "account"
        elif _contains_any(sentence, (
            "upload", "save", "saving", "file", "filing", "submit", "bot", "app",
        )):
            intent, action = ConversationalIntent.HELP_OR_CAPABILITY, "answer_capability"
        else:
            continue
        return RouterResult(intent=intent, confidence=0.88, signals=_compact_signals(action=action, topic=topic))
    return None


def _own_product_complaint(sentence: str) -> bool:
    """A current account/save failure, not an account event inside an activity."""
    own_subject = bool(re.match(
        r"^(?:i\s+(?:can(?:not|'t)|could(?:n't| not)|am unable|have|had)|"
        r"my\s+(?:account|login|access|subscription|payment|plan|draft)|"
        r"(?:saving|filing|logging in)\b)", sentence,
    ))
    failure = bool(re.search(
        r"\b(?:can(?:not|'t)|couldn't|unable|problem|blocked|blocking|failed|failing|"
        r"fails|failure|error|won't|not working)\b", sentence,
    ))
    product = _contains_any(sentence, ACCOUNT_TERMS + SETUP_TERMS + BILLING_TERMS) or bool(re.search(
        r"\b(?:saving|save|filing|file)\b.*\b(?:reflection|draft|kaizen|portfolio|evidence)\b", sentence,
    ))
    return own_subject and failure and product


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
    return not _explicit_filing_request(text) and not _product_help(text) and _has_activity_evidence(text)


def _has_activity_evidence(text: str) -> bool:
    """Recognise activity only after the higher-priority routes have been checked."""
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
