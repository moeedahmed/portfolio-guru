"""Deterministic recognition of non-case evidence artifacts.

A certificate, award, commendation or thank-you letter is portfolio evidence,
but it is not a clinical case. Before this module every uploaded document was
offered as case material and pushed through form recommendation, which is how a
"Registrar of the Month" PDF ended up being steered into a Self-directed
Learning Reflection with an invented SLO mapping.

The classification here is keyword/pattern only — no model call — so the
routing decision cannot hallucinate. The copy is capability-based: it says what
Portfolio Guru does and does not do, and makes no claim about RCEM, ARCP or
Kaizen platform rules, none of which are verified anywhere in this repository.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Only the opening of an extracted document is scanned. A certificate says what
# it is in its first lines; scanning further only invites false positives from
# a long clinical document that happens to mention an award.
ARTIFACT_TEXT_SCAN_CHARS = 500

# Concrete, conservative signals. Each one names the artifact rather than
# describing clinical work.
_ARTIFACT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("award", r"\bawards?\b|\bawarded\b"),
    ("certificate", r"\bcertificates?\b|\bcertificate of\b"),
    ("recognition", r"\brecognition\b|\brecognised for\b|\brecognized for\b"),
    ("commendation", r"\bcommendations?\b|\bcommended\b|\bhighly commended\b"),
    ("of_the_month", r"\bof the (month|year|week)\b"),
    ("achievement", r"\bachievements?\b|\bachievement award\b"),
    ("prize", r"\bprizes?\b|\bprize[- ]?winner\b"),
    ("thank_you", r"\bthank[- ]?you\b|\bletter of thanks\b|\bwith thanks\b"),
    ("appreciation", r"\bappreciation\b|\bletter of appreciation\b"),
    ("nomination", r"\bnominations?\b|\bnominated for\b"),
    ("confirmation", r"\bconfirmation of (attendance|appointment|employment|role|post|completion)\b"),
    ("employee_of", r"\b(employee|doctor|registrar|nurse|clinician|star) of the\b"),
    ("long_service", r"\blong service\b"),
)

# Signals that the doctor has described something they actually did — clinical
# work, or an activity such as a course. A case that merely mentions an
# attached certificate, or a course the doctor says they completed, must keep
# filing normally, so any of these vetoes the artifact route. Only a bare
# award/recognition artifact with no described activity takes the honest
# evidence lane.
_SUBSTANCE_MARKERS: tuple[str, ...] = (
    # clinical
    "patient",
    "presented",
    "presentation",
    "presenting complaint",
    "history of",
    "examination",
    "observations",
    "obs were",
    "diagnos",
    "differential",
    "resus",
    "triage",
    "admitted",
    "discharged",
    "referred",
    "escalated",
    "prescrib",
    "analgesia",
    "bloods",
    "ecg",
    "ct head",
    "x-ray",
    "chest pain",
    "sepsis",
    "airway",
    "intubat",
    "management plan",
    "safety net",
    "i reviewed",
    "i assessed",
    "i managed",
    "i performed",
    # activity the doctor says they did
    "i completed",
    "i attended",
    "i passed",
    "i took part",
    "i taught",
    "i delivered",
    "i presented",
    "i organised",
    "i organized",
    "i led",
    "i ran",
    "i learned",
    "i learnt",
    "i reflected",
    "course",
    "study day",
    "training day",
    "simulation",
)

# Phrases where an artifact keyword is really clinical paperwork.
_NEGATIVE_PATTERNS: tuple[str, ...] = (
    r"\bdeath certificate\b",
    r"\bcertificate of death\b",
    r"\bcremation\b",
    r"\bmedical certificate of cause of death\b",
    r"\bfit note\b",
    r"\bsick note\b",
)


@dataclass(frozen=True)
class ArtifactSignal:
    """Why (or why not) an input was treated as a non-case evidence artifact."""

    is_artifact: bool
    matched: tuple[str, ...] = ()
    source: str = ""


def _normalise_file_name(file_name: str) -> str:
    stem = os.path.splitext(os.path.basename(file_name))[0]
    return re.sub(r"[_\-.]+", " ", stem).lower()


def _matches(haystack: str) -> tuple[str, ...]:
    if any(re.search(pattern, haystack) for pattern in _NEGATIVE_PATTERNS):
        return ()
    return tuple(name for name, pattern in _ARTIFACT_PATTERNS if re.search(pattern, haystack))


def _describes_an_activity(text: str) -> bool:
    """True when the doctor's own words describe work or an activity."""
    lowered = text.lower()
    return any(marker in lowered for marker in _SUBSTANCE_MARKERS)


def classify_evidence_artifact(
    *,
    file_name: str = "",
    text: str = "",
) -> ArtifactSignal:
    """Classify an upload or a message as a non-case evidence artifact.

    Deterministic and conservative: any described clinical work or activity in
    the supplied text vetoes the artifact route, so a real case that mentions a
    certificate, or a course the doctor says they completed, still files
    normally.
    """
    body = (text or "").strip()
    if body and _describes_an_activity(body):
        return ArtifactSignal(False)

    if file_name:
        matched = _matches(_normalise_file_name(file_name))
        if matched:
            return ArtifactSignal(True, matched, "filename")

    if body:
        matched = _matches(body[:ARTIFACT_TEXT_SCAN_CHARS].lower())
        if matched:
            return ArtifactSignal(True, matched, "text")

    return ArtifactSignal(False)


def looks_like_evidence_artifact(*, file_name: str = "", text: str = "") -> bool:
    return classify_evidence_artifact(file_name=file_name, text=text).is_artifact


# Questions like "should I record this as a reflection or just upload the file?"
_ARTIFACT_QUESTION_RE = re.compile(
    r"\b(reflection|reflect|upload|attach|file it|record it|log it|add this|put this)\b"
)


def looks_like_artifact_filing_question(
    text: str,
    *,
    case_context: str = "",
    document_name: str = "",
) -> bool:
    """True when the doctor is asking how to file a certificate/award artifact.

    The question itself often carries no artifact keyword ("reflection or just
    upload the file?"), so the surrounding upload or case context decides.
    """
    lowered = (text or "").lower()
    if not _ARTIFACT_QUESTION_RE.search(lowered):
        return False
    if classify_evidence_artifact(text=text).is_artifact:
        return True
    return classify_evidence_artifact(
        file_name=document_name,
        text=case_context,
    ).is_artifact


# --- Copy -------------------------------------------------------------------
# Capability-based only. No claim about ARCP panels, RCEM curriculum mapping,
# or which forms exist on the Kaizen platform.

_CAPABILITY_LINES = (
    "I draft RCEM WPBA forms and save them to Kaizen as drafts once you approve them. "
    "I can't upload a standalone file to Kaizen on its own."
)

_EVIDENCE_LINES = (
    "I can attach it as supporting evidence to a form you choose — for example a "
    "reflection you write yourself. I won't write a reflection about the award for "
    "you, and I won't map it to a curriculum area unless you tell me what you "
    "actually did."
)


def evidence_artifact_upload_message() -> str:
    """Shown when an uploaded file looks like a certificate or award."""
    return (
        "🏅 That looks like a certificate or award rather than a clinical case.\n\n"
        f"{_CAPABILITY_LINES}\n\n"
        f"{_EVIDENCE_LINES}\n\n"
        "If there's an activity behind it you want to file, tell me what you did in "
        "your own words and I'll suggest a form."
    )


def evidence_artifact_text_message() -> str:
    """Shown when a message describes only a certificate or award."""
    return (
        "🏅 That sounds like a certificate or award rather than a clinical case.\n\n"
        f"{_CAPABILITY_LINES}\n\n"
        f"{_EVIDENCE_LINES}\n\n"
        "Tell me what you actually did — the work, teaching, or case behind it — and "
        "I'll suggest a form for that."
    )


def evidence_artifact_answer() -> str:
    """Answer to 'should I record this as a reflection or just upload it?'."""
    return (
        "🏅 Two different things, so it's your call.\n\n"
        f"{_CAPABILITY_LINES}\n\n"
        "Attaching it as evidence: send it with a form you choose and I'll attach it "
        "to that draft.\n\n"
        "Writing a reflection: only worth doing if you have something of your own to "
        "say — what you did, what you took from it, what you'd change. I can structure "
        "and edit your words, but I won't invent the reflection from the certificate.\n\n"
        "How it should be recorded for your portfolio review is worth a quick ask with "
        "your educational supervisor."
    )
