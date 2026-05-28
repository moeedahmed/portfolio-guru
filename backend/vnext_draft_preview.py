"""Local preview builder for vNext dogfood.

Constructs a short, source-tied summary from CaseFacts. This is NOT a Kaizen
draft - it is a local dogfood preview. Every value shown is sourced directly
from CaseFact.value; nothing is inferred, filled in, or fabricated.
"""

from __future__ import annotations

from conversational_case_engine import CaseFact
from vnext_form_recommender import FormRecommendation, InsufficientFacts, RecommendResult

_HEADER = "-- vNext local preview (not a Kaizen draft) --"
_FOOTER = "Kaizen filing not wired - dogfood only."
_DIVIDER = "-" * 46

_KEY_LABELS: dict[str, str] = {
    "age": "Age",
    "sex": "Sex",
    "setting": "Setting",
    "presenting_complaint": "Presenting complaint",
    "diagnosis": "Diagnosis",
    "procedure": "Procedure/intervention",
    "supervision": "Supervision",
    "learning_point": "Learning",
}


def build_draft_preview(
    facts: tuple[CaseFact, ...],
    recommendation: RecommendResult,
) -> str:
    """Build a local dogfood preview from source-tied facts.

    Args:
        facts: Draft-eligible CaseFacts (workspace.draft_eligible_facts()).
        recommendation: Result from vnext_form_recommender.recommend().

    Returns:
        A short multi-line string. Every displayed value comes from facts.
    """
    fd = {f.key: f.value for f in facts}

    lines: list[str] = [_HEADER, ""]

    # Facts section.
    if fd:
        lines.append("Facts captured (source-tied):")
        for key, label in _KEY_LABELS.items():
            if key in fd:
                lines.append(f"  {label}: {fd[key]}")
        lines.append("")
    else:
        lines.append("  (no facts captured yet)")
        lines.append("")

    # Narrative outline: values from facts only, no fabrication.
    narrative = _build_narrative(fd)
    if narrative:
        lines.append("Narrative outline:")
        for sentence in narrative:
            lines.append(f"  {sentence}")
        lines.append("")

    # Form recommendation.
    if isinstance(recommendation, FormRecommendation):
        lines.append(
            f"Recommended form: {recommendation.form_type}"
            f" ({recommendation.confidence} confidence)"
        )
        lines.append(f"Reason: {recommendation.reason}")
    else:
        lines.append("Form recommendation: not enough context yet")
        lines.append(f"Missing: {recommendation.missing_prompt}")
    lines.append("")

    lines.append(_DIVIDER)
    lines.append(_FOOTER)

    return "\n".join(lines)


def _build_narrative(fd: dict[str, str]) -> list[str]:
    """Return a list of short source-tied sentences for the narrative outline."""
    sentences: list[str] = []

    age = fd.get("age", "")
    sex = fd.get("sex", "")
    setting = fd.get("setting", "")
    complaint = fd.get("presenting_complaint", "")
    diagnosis = fd.get("diagnosis", "")
    procedure = fd.get("procedure", "")
    supervision = fd.get("supervision", "")
    learning = fd.get("learning_point", "")

    # Opening: demographics + complaint + setting
    opening_parts: list[str] = []
    if age and sex:
        opening_parts.append(f"{age}-year-old {sex}")
    elif age:
        opening_parts.append(f"{age}-year-old patient")

    if complaint:
        opening_parts.append(f"presented with {complaint}")
    if setting:
        opening_parts.append(f"in {setting}")

    if opening_parts:
        sentences.append(" ".join(opening_parts) + ".")

    # Diagnosis
    if diagnosis:
        sentences.append(f"Diagnosis: {diagnosis}.")

    # Procedure + supervision
    if procedure:
        if supervision:
            sentences.append(f"Procedure: {procedure}, supervised by {supervision}.")
        else:
            sentences.append(f"Procedure: {procedure}.")
    elif supervision:
        sentences.append(f"Supervised by {supervision}.")

    # Learning point (capped at 80 chars to keep preview concise)
    if learning:
        short = learning if len(learning) <= 80 else learning[:77] + "..."
        sentences.append(f"Learning: {short}.")

    return sentences
