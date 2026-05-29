"""Conversational reply policy for the private vNext bot.

This layer keeps the pure case engine underneath, but stops the Telegram
dogfood bot from behaving like a parser harness. The bot should gather case
details across turns, ask for the next useful fact, and show the local preview
only when the user says they are done or asks to draft/file.
"""

from __future__ import annotations

import re

from conversational_case_engine import CaseFact, CaseWorkspace

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

_FEATURE_TERMS: tuple[str, ...] = (
    "feature",
    "features",
    "what can you do",
    "what do you do",
    "help",
    "how does this work",
)

_GREETING_TERMS: tuple[str, ...] = (
    "hi",
    "hello",
    "hey",
    "hello there",
)

_WELLBEING_TERMS: tuple[str, ...] = (
    "how are you",
    "how's it going",
    "how is it going",
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
    return 'Noted. Add more or say "draft it" when done.'


def not_ready_reply(workspace: CaseWorkspace) -> str:
    """Explain what is missing without exposing engine state names."""
    return (
        f"Not enough to preview yet. {captured_fact_summary(workspace)}\n\n"
        f"{next_missing_prompt(workspace)}"
    )


def side_chat_reply(text: str | None, workspace: CaseWorkspace) -> str:
    """Answer non-case chat without turning it into case collection."""
    normalised = _normalise(text)
    if _contains_any(normalised, _FEATURE_TERMS):
        return (
            "I am the private Portfolio Guru vNext test bot. Right now I can:\n"
            "- collect a case over multiple messages\n"
            "- keep source-tied facts separate from chat\n"
            "- ask for the next useful missing detail\n"
            "- recommend a likely portfolio form\n"
            "- show a local dogfood preview when you ask to finish\n\n"
            "Kaizen filing is deliberately not connected here yet."
        )
    if _contains_any(normalised, _WELLBEING_TERMS):
        return (
            "I am good - and currently in dogfood mode. Send me a case in natural "
            "language, or ask what I can do."
        )
    if _is_greeting(normalised):
        return (
            "Hi. I am the private Portfolio Guru vNext test bot. Tell me a case "
            "in your own words, or ask what I can do."
        )
    if workspace.draft_eligible_facts():
        return (
            "I have kept that as chat, not case detail. "
            f"{captured_fact_summary(workspace)}\n\n"
            "Add more case detail, or say 'done' when you want the form recommendation."
        )
    return (
        "I can help capture portfolio evidence conversationally. Tell me what "
        "happened in the case, or ask what I can do."
    )


def _normalise(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(text.strip().lower().split())


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _is_greeting(text: str) -> bool:
    return text in _GREETING_TERMS
