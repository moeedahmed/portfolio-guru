"""Deterministic form-type recommendation from vNext CaseFacts.

No LLM, no network, no Kaizen. Pure rule-based inference from fact keys/values
produced by vnext_text_extractor. Conservative: only recommends when the signal
is defensible from the available facts; returns InsufficientFacts otherwise.

Rules mirror the authoritative decision logic in the production LLM recommender
(extractor.py) but are limited to signals the vNext extractor actually captures.
"""

from __future__ import annotations

from dataclasses import dataclass

from conversational_case_engine import CaseFact

# ---------------------------------------------------------------------------
# Procedure signal classification
# ---------------------------------------------------------------------------

# Procedures the trainee performs as a skilled technical act (DOPS/PROC_LOG).
_TRAINEE_SKILL_PROCEDURES: frozenset[str] = frozenset(
    {
        "rsi",
        "intubation",
        "intubated",
        "central line",
        "cvc",
        "arterial line",
        "chest drain",
        "lumbar puncture",
        "fascia iliaca",
        "femoral nerve block",
        "nerve block",
        "cardioversion",
        "dc cardioversion",
        "procedural sedation",
    }
)

# Ultrasound/POCUS procedures map to US_CASE rather than DOPS/PROC_LOG.
_US_PROCEDURES: frozenset[str] = frozenset({"pocus", "fast scan"})

# Supervision values that suggest a direct clinical observer (lean DOPS).
_OBSERVER_WORDS: frozenset[str] = frozenset({"consultant", "registrar"})


def _is_trainee_skill(procedure: str) -> bool:
    proc_lower = procedure.lower()
    return any(skill in proc_lower for skill in _TRAINEE_SKILL_PROCEDURES)


def _is_us_procedure(procedure: str) -> bool:
    proc_lower = procedure.lower()
    return any(us in proc_lower for us in _US_PROCEDURES)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormRecommendation:
    """A defensible form-type recommendation derived from source-tied facts."""

    form_type: str
    confidence: str  # "high" | "medium"
    reason: str


@dataclass(frozen=True)
class InsufficientFacts:
    """Facts are ambiguous or incomplete; provides a specific clarifying prompt."""

    missing_prompt: str


RecommendResult = FormRecommendation | InsufficientFacts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def recommend(facts: tuple[CaseFact, ...]) -> RecommendResult:
    """Return a defensible form recommendation or InsufficientFacts.

    Rules are applied in priority order. A recommendation is only made when
    the fact signals are unambiguous; partial or conflicting signals yield
    InsufficientFacts with a targeted clarifying prompt.

    This function is pure - it reads facts and returns a result without
    mutating anything or calling any external service.
    """
    fd = {f.key: f.value for f in facts}

    procedure = fd.get("procedure", "")
    diagnosis = fd.get("diagnosis", "")
    setting = fd.get("setting", "")
    complaint = fd.get("presenting_complaint", "")
    supervision = fd.get("supervision", "")
    learning = fd.get("learning_point", "")

    # Rule 1: POCUS / ultrasound - highest priority, orthogonal signal.
    if procedure and _is_us_procedure(procedure):
        return FormRecommendation(
            form_type="US_CASE",
            confidence="high",
            reason=f"ultrasound/POCUS procedure ({procedure}) as primary intervention",
        )

    # Rule 2: Named trainee skill procedure -> DOPS (with supervisor) or PROC_LOG.
    if procedure and _is_trainee_skill(procedure):
        if supervision.lower() in _OBSERVER_WORDS:
            return FormRecommendation(
                form_type="DOPS",
                confidence="medium",
                reason=(
                    f"supervised {procedure} - if {supervision} directly observed "
                    "for assessment, DOPS; otherwise PROC_LOG"
                ),
            )
        return FormRecommendation(
            form_type="PROC_LOG",
            confidence="medium",
            reason=f"trainee-performed {procedure} without confirmed direct observer",
        )

    # Rule 3: Clinical case management (CBD) - setting + diagnosis or complaint.
    if setting and (diagnosis or complaint):
        if diagnosis:
            return FormRecommendation(
                form_type="CBD",
                confidence="high",
                reason=(
                    f"{setting} case with {diagnosis} - "
                    "clinical management without standalone trainee procedure skill"
                ),
            )
        return FormRecommendation(
            form_type="CBD",
            confidence="medium",
            reason=(
                f"{setting} case presenting with {complaint} - "
                "add confirmed diagnosis for higher confidence"
            ),
        )

    # Rule 4: Pure reflection / learning without clinical case context.
    if learning and not (diagnosis or complaint or procedure or setting):
        return FormRecommendation(
            form_type="REFLECT_LOG",
            confidence="medium",
            reason="reflection/learning signal without clinical case context",
        )

    # Rule 5: Insufficient - be specific about the highest-value missing fact.
    if not setting and (diagnosis or complaint or procedure):
        return InsufficientFacts(
            missing_prompt=(
                "What was the clinical setting? (e.g. ED, ICU, resus, theatres)"
            )
        )
    if setting and not (diagnosis or complaint or procedure):
        return InsufficientFacts(
            missing_prompt=(
                f"What was the presenting complaint or diagnosis in {setting}?"
            )
        )

    return InsufficientFacts(
        missing_prompt=(
            "Not enough clinical context yet - add setting, diagnosis, "
            "presenting complaint, or procedure detail."
        )
    )
