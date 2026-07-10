"""Pure free-text policy around Portfolio Guru's deterministic workflow.

The conversation may stay flexible, but a message must not silently move the
filing state unless its intent is explicit and the current phase permits that
action.  This module owns no Telegram or persistence I/O; handlers decide how
to render and execute the returned decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from conversational_router import ConversationalIntent, route_message


class WorkflowPhase(str, Enum):
    IDLE = "idle"
    CASE_OPEN = "case_open"
    DRAFT_OPEN = "draft_open"
    COMPLETED = "completed"


class WorkflowTurnKind(str, Enum):
    CHAT = "chat"
    SIDE_QUESTION = "side_question"
    ENRICH = "enrich"
    ENRICH_AND_ANSWER = "enrich_and_answer"
    EXPLICIT_EDIT = "explicit_edit"
    EXPLICIT_FILE = "explicit_file"
    NEW_CASE = "new_case"
    CONFIRM_STATE_CHANGE = "confirm_state_change"
    CLARIFY = "clarify"


@dataclass(frozen=True)
class WorkflowTurnDecision:
    kind: WorkflowTurnKind
    state_action: str | None = None
    canonical_intent: ConversationalIntent = ConversationalIntent.UNKNOWN
    case_detail: str | None = None


_SIDE_QUESTION_INTENTS = frozenset(
    {
        ConversationalIntent.PORTFOLIO_QUESTION,
        ConversationalIntent.HELP_OR_CAPABILITY,
        ConversationalIntent.SAFETY_OR_MEDICAL_ADVICE,
        ConversationalIntent.ACCOUNT_OR_BILLING,
        ConversationalIntent.SETUP_OR_CREDENTIALS,
        ConversationalIntent.OUT_OF_SCOPE,
    }
)

_AMBIGUOUS_DESTRUCTIVE_RE = re.compile(
    r"^\s*(?:forget\s+it|start\s+over|never\s*mind|scrap\s+(?:it|this)|"
    r"cancel\s+(?:it|this|the\s+draft))\s*[.!?]*\s*$",
    re.IGNORECASE,
)

_EXPLICIT_NEW_CASE_RE = re.compile(
    r"\b(?:start|file|create|open)?\s*(?:a\s+)?(?:new|another|different|separate)\s+"
    r"(?:case|wpba|ticket|patient)\b",
    re.IGNORECASE,
)

_EXPLICIT_EDIT_RE = re.compile(
    r"^\s*(?:(?:can|could|would)\s+you\s+)?(?:actually\s+)?"
    r"(?:change|set|replace|rewrite|revise|make|add|remove|tweak|update)\b",
    re.IGNORECASE,
)

_CLINICAL_DETAIL_RE = re.compile(
    r"\b(?:patient|presented|diagnosed|managed|treated|assessed|reviewed|resus|"
    r"hypotension|tachycardia|procedure|ultrasound|pocus|escalated|consultant|"
    r"adrenaline|oxygen|blood\s+pressure|\bBP\b)\b",
    re.IGNORECASE,
)

_PORTFOLIO_EVIDENCE_DETAIL_RE = re.compile(
    r"\b(?:completed|attended|course|certificate|certification|module|"
    r"rcemlearning|atls|als|apls|teaching|audit|qip|quality\s+improvement|"
    r"reflection|reflected|supervised|observed|assessment)\b",
    re.IGNORECASE,
)

_PORTFOLIO_QUESTION_RE = re.compile(
    r"\b(?:cbd|mini[- ]?cex|dops|acat|wpba|wba|kaizen|portfolio|form|slo\d*|"
    r"curriculum|key\s+capabilit(?:y|ies))\b",
    re.IGNORECASE,
)


def decide_workflow_turn(
    text: str,
    *,
    phase: WorkflowPhase,
    legacy_intent: str | None,
    classifier_failed: bool = False,
) -> WorkflowTurnDecision:
    """Classify a workflow turn without mutating workflow or content state."""
    raw = (text or "").strip()
    routed = route_message(raw)
    canonical = routed.intent

    if not raw:
        return WorkflowTurnDecision(WorkflowTurnKind.CLARIFY, canonical_intent=canonical)

    if phase in {WorkflowPhase.CASE_OPEN, WorkflowPhase.DRAFT_OPEN} and _AMBIGUOUS_DESTRUCTIVE_RE.match(raw):
        return WorkflowTurnDecision(
            WorkflowTurnKind.CONFIRM_STATE_CHANGE,
            state_action="cancel_current",
            canonical_intent=canonical,
        )

    explicit_new_case = bool(_EXPLICIT_NEW_CASE_RE.search(raw))
    if explicit_new_case:
        if phase in {WorkflowPhase.CASE_OPEN, WorkflowPhase.DRAFT_OPEN}:
            return WorkflowTurnDecision(
                WorkflowTurnKind.CONFIRM_STATE_CHANGE,
                state_action="start_new_case",
                canonical_intent=canonical,
            )
        return WorkflowTurnDecision(
            WorkflowTurnKind.NEW_CASE,
            state_action="start_new_case",
            canonical_intent=canonical,
        )

    case_detail = _looks_like_case_detail(raw)
    questionish = _looks_like_question(raw)

    if questionish and _PORTFOLIO_QUESTION_RE.search(raw) and not case_detail:
        return WorkflowTurnDecision(
            WorkflowTurnKind.SIDE_QUESTION,
            canonical_intent=canonical,
        )

    if canonical in _SIDE_QUESTION_INTENTS:
        if case_detail and questionish and phase is WorkflowPhase.CASE_OPEN and not classifier_failed:
            return WorkflowTurnDecision(
                WorkflowTurnKind.ENRICH_AND_ANSWER,
                canonical_intent=canonical,
                case_detail=_mixed_case_detail(raw),
            )
        return WorkflowTurnDecision(
            WorkflowTurnKind.SIDE_QUESTION,
            canonical_intent=canonical,
        )

    if legacy_intent in {"question_general", "question_about_case"}:
        if case_detail and questionish and phase is WorkflowPhase.CASE_OPEN and not classifier_failed:
            return WorkflowTurnDecision(
                WorkflowTurnKind.ENRICH_AND_ANSWER,
                canonical_intent=canonical,
                case_detail=_mixed_case_detail(raw),
            )
        return WorkflowTurnDecision(
            WorkflowTurnKind.SIDE_QUESTION,
            canonical_intent=canonical,
        )

    if legacy_intent == "chitchat":
        return WorkflowTurnDecision(WorkflowTurnKind.CHAT, canonical_intent=canonical)

    if canonical is ConversationalIntent.FILE_TO_KAIZEN:
        if phase is WorkflowPhase.DRAFT_OPEN:
            return WorkflowTurnDecision(
                WorkflowTurnKind.EXPLICIT_FILE,
                state_action="file_draft",
                canonical_intent=canonical,
        )
        return WorkflowTurnDecision(WorkflowTurnKind.CLARIFY, canonical_intent=canonical)

    if canonical is ConversationalIntent.EDIT_DRAFT and phase is WorkflowPhase.DRAFT_OPEN:
        return WorkflowTurnDecision(
            WorkflowTurnKind.EXPLICIT_EDIT,
            state_action="edit_draft",
            canonical_intent=canonical,
        )

    if classifier_failed:
        return WorkflowTurnDecision(WorkflowTurnKind.CLARIFY, canonical_intent=canonical)

    if case_detail and phase is WorkflowPhase.CASE_OPEN:
        return WorkflowTurnDecision(WorkflowTurnKind.ENRICH, canonical_intent=canonical)

    if canonical is ConversationalIntent.EDIT_DRAFT or (
        legacy_intent == "edit_detail" and _EXPLICIT_EDIT_RE.search(raw)
    ):
        if phase is WorkflowPhase.DRAFT_OPEN:
            return WorkflowTurnDecision(
                WorkflowTurnKind.EXPLICIT_EDIT,
                state_action="edit_draft",
                canonical_intent=canonical,
            )
        return WorkflowTurnDecision(WorkflowTurnKind.CLARIFY, canonical_intent=canonical)

    if case_detail:
        if phase in {WorkflowPhase.IDLE, WorkflowPhase.COMPLETED}:
            return WorkflowTurnDecision(
                WorkflowTurnKind.NEW_CASE,
                state_action="start_new_case",
                canonical_intent=canonical,
            )
        return WorkflowTurnDecision(WorkflowTurnKind.ENRICH, canonical_intent=canonical)

    if legacy_intent == "edit_detail" and phase is WorkflowPhase.DRAFT_OPEN:
        return WorkflowTurnDecision(WorkflowTurnKind.ENRICH, canonical_intent=canonical)

    # An LLM-only "new_case" verdict is not explicit enough to move state.
    if legacy_intent == "new_case":
        return WorkflowTurnDecision(WorkflowTurnKind.CLARIFY, canonical_intent=canonical)

    return WorkflowTurnDecision(WorkflowTurnKind.CLARIFY, canonical_intent=canonical)


def _looks_like_case_detail(text: str) -> bool:
    words = len(text.split())
    return (
        (words >= 8 and bool(_CLINICAL_DETAIL_RE.search(text)))
        or (words >= 6 and bool(_PORTFOLIO_EVIDENCE_DETAIL_RE.search(text)))
    )


def _looks_like_question(text: str) -> bool:
    return "?" in text or bool(
        re.match(
            r"^\s*(?:what|which|how|why|when|where|who|can|could|do|does|is|are|will|should)\b",
            text,
            re.IGNORECASE,
        )
    )


def _mixed_case_detail(text: str) -> str:
    """Keep the clinical clause while excluding the attached side question."""
    match = re.match(
        r"^(?P<detail>.+?)(?:\s+[—–-]\s+|\.\s+)"
        r"(?=(?:what|which|how|why|when|where|who|can|could|do|does|is|are|will|should)\b)",
        text.strip(),
        re.IGNORECASE,
    )
    return (match.group("detail") if match else text).strip()
