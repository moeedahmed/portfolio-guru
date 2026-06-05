"""Conversational gathering helpers over the pure case engine.

This layer keeps the deterministic case engine underneath but stops the
bot from behaving like a parser harness. The bot gathers case details
across turns, asks for the next useful fact, and previews only when the
user says they are done or asks to draft/file.

Side-chat / capability replies are no longer produced here — that copy
now lives in :mod:`message_policy` and is routed through
:mod:`conversation_supervisor` so it stays auditable and channel-agnostic.
"""

from __future__ import annotations

import re

from conversational_case_engine import CaseFact, CaseWorkspace
from message_policy import render_message

_COMPLETION_RE = re.compile(
    r"\b("
    r"done|that's all|that is all|all done|finished|finish|"
    r"draft it|preview|show me the draft|show draft|"
    r"file this|file it|save this|save it|send this|send it|"
    r"create draft|save draft"
    r")\b",
    re.IGNORECASE,
)

_DETAIL_LABELS: dict[str, str] = {
    "age": "age",
    "sex": "sex",
    "setting": "setting",
    "presenting_complaint": "presenting complaint",
    "diagnosis": "diagnosis",
    "procedure": "procedure/intervention",
    "supervision": "supervision",
    "learning_point": "learning point",
}

_PROMPT_BY_KEY: dict[str, str] = {
    "setting": "Where did this happen - ED, resus, ICU, clinic, or somewhere else?",
    "presenting_complaint": "What did the patient present with?",
    "diagnosis": "What was the working diagnosis or final diagnosis?",
    "supervision": "Was anyone supervising or directly observing you?",
    "learning_point": "What was your main learning point or reflection?",
}

_PRIORITY_MISSING_KEYS: tuple[str, ...] = (
    "setting",
    "presenting_complaint",
    "diagnosis",
    "supervision",
    "learning_point",
)

def is_completion_request(text: str | None) -> bool:
    """True when the user is asking to move from collecting to preview."""
    if not text:
        return False
    return bool(_COMPLETION_RE.search(text))


def captured_fact_summary(workspace: CaseWorkspace) -> str:
    """Return a compact human summary of captured fact labels."""
    facts = workspace.draft_eligible_facts()
    if not facts:
        return "I have not captured enough case detail yet."
    labels = [_DETAIL_LABELS.get(fact.key, fact.key.replace("_", " ")) for fact in facts]
    return "I have captured " + ", ".join(labels) + "."


def next_missing_prompt(workspace: CaseWorkspace) -> str:
    """Ask for the highest-value missing detail for the current case."""
    present = {fact.key for fact in workspace.draft_eligible_facts()}
    for key in _PRIORITY_MISSING_KEYS:
        if key not in present:
            return _PROMPT_BY_KEY[key]
    return "Anything else worth adding? If not, say 'done' and I will preview it."


def collecting_reply(workspace: CaseWorkspace) -> str:
    """Brief acknowledgement while the user is still sharing information."""
    return render_message("gathering_captured")


def not_ready_reply(workspace: CaseWorkspace) -> str:
    """Explain what is missing without exposing engine state names."""
    return (
        f"Not enough to preview yet. {captured_fact_summary(workspace)}\n\n"
        f"{next_missing_prompt(workspace)}"
    )


