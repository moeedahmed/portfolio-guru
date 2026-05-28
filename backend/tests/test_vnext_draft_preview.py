"""Tests for the vNext local draft preview builder.

Covers:
- Preview always contains dogfood marker and non-Kaizen-draft header
- All captured fact values appear verbatim in the preview
- Source-tied invariant: no values outside the fact set appear in the narrative
- FormRecommendation is shown with form type and reason
- InsufficientFacts is shown with missing_prompt (no form type fabricated)
- Empty facts tuple does not crash
- Narrative outline uses only fact values, no fabrication
- Preview with partial facts (no demographics, no procedure) stays honest
"""

from __future__ import annotations

from conversational_case_engine import CaseFact, SourceType
from vnext_draft_preview import build_draft_preview
from vnext_form_recommender import (
    FormRecommendation,
    InsufficientFacts,
    recommend,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_facts(**kwargs: str) -> tuple[CaseFact, ...]:
    return tuple(
        CaseFact(key=k, value=v, source_type=SourceType.TEXT, source_turn_id="t1")
        for k, v in kwargs.items()
    )


_STEMI_FACTS = _make_facts(
    age="62",
    sex="M",
    setting="ED",
    presenting_complaint="chest pain",
    diagnosis="STEMI",
    procedure="cath lab",
    supervision="consultant",
    learning_point="learned to escalate early",
)


# ---------------------------------------------------------------------------
# Header / marker invariants
# ---------------------------------------------------------------------------


def test_preview_always_contains_not_kaizen_draft_marker():
    preview = build_draft_preview(_STEMI_FACTS, recommend(_STEMI_FACTS))
    assert "not a Kaizen draft" in preview


def test_preview_always_contains_dogfood_footer():
    preview = build_draft_preview(_STEMI_FACTS, recommend(_STEMI_FACTS))
    assert "dogfood" in preview.lower()


def test_preview_with_empty_facts_contains_markers():
    preview = build_draft_preview((), recommend(()))
    assert "not a Kaizen draft" in preview
    assert "dogfood" in preview.lower()


def test_preview_header_is_first_line():
    preview = build_draft_preview(_STEMI_FACTS, recommend(_STEMI_FACTS))
    first_line = preview.split("\n")[0]
    assert "vNext local preview" in first_line


# ---------------------------------------------------------------------------
# Source-tied invariant
# ---------------------------------------------------------------------------


def test_all_fact_values_appear_in_preview():
    """Every captured fact value must appear verbatim in the preview."""
    facts = _STEMI_FACTS
    preview = build_draft_preview(facts, recommend(facts))
    for fact in facts:
        assert fact.value in preview, f"Fact value {fact.value!r} missing from preview"


def test_preview_does_not_contain_unfact_clinical_words():
    """Preview should not invent clinical terms absent from the fact set."""
    facts = _make_facts(age="62", sex="M", setting="ED", diagnosis="STEMI")
    preview = build_draft_preview(facts, recommend(facts))
    # These words are not in the facts
    assert "anaphylaxis" not in preview
    assert "sepsis" not in preview
    assert "pneumothorax" not in preview


def test_narrative_uses_only_fact_values_no_age_inflation():
    """Narrative must not invent demographics beyond what is in the facts."""
    facts = _make_facts(setting="ED", diagnosis="PE")
    preview = build_draft_preview(facts, recommend(facts))
    # No age or sex was captured - the narrative should not mention any
    assert "year-old" not in preview
    assert "male" not in preview
    assert "female" not in preview


# ---------------------------------------------------------------------------
# FormRecommendation display
# ---------------------------------------------------------------------------


def test_preview_shows_form_type_from_recommendation():
    rec = FormRecommendation(form_type="CBD", confidence="high", reason="test reason")
    preview = build_draft_preview(_STEMI_FACTS, rec)
    assert "CBD" in preview
    assert "high" in preview


def test_preview_shows_reason_from_recommendation():
    rec = FormRecommendation(form_type="CBD", confidence="high", reason="ED case with STEMI")
    preview = build_draft_preview(_STEMI_FACTS, rec)
    assert "ED case with STEMI" in preview


def test_stemi_case_shows_cbd_in_preview():
    preview = build_draft_preview(_STEMI_FACTS, recommend(_STEMI_FACTS))
    assert "CBD" in preview
    assert "high" in preview


# ---------------------------------------------------------------------------
# InsufficientFacts display
# ---------------------------------------------------------------------------


def test_preview_with_insufficient_facts_shows_missing_prompt():
    insufficient = InsufficientFacts(missing_prompt="What was the clinical setting?")
    preview = build_draft_preview(_STEMI_FACTS, insufficient)
    assert "What was the clinical setting?" in preview


def test_preview_with_insufficient_does_not_show_fabricated_form_type():
    """If InsufficientFacts is returned, no form type code should be shown as a recommendation."""
    insufficient = InsufficientFacts(missing_prompt="Add setting detail")
    facts = _make_facts(age="62", diagnosis="STEMI")
    preview = build_draft_preview(facts, insufficient)
    assert "Recommended form:" not in preview
    assert "not enough context" in preview.lower()


# ---------------------------------------------------------------------------
# Empty / minimal fact sets
# ---------------------------------------------------------------------------


def test_preview_with_empty_facts_does_not_crash():
    preview = build_draft_preview((), InsufficientFacts("Add more detail"))
    assert isinstance(preview, str)
    assert len(preview) > 0
    assert "no facts captured yet" in preview.lower()


def test_preview_with_only_demographics():
    facts = _make_facts(age="45", sex="F")
    preview = build_draft_preview(facts, recommend(facts))
    assert "45" in preview
    assert "F" in preview


def test_preview_with_only_learning_point():
    facts = _make_facts(learning_point="learned to escalate early")
    preview = build_draft_preview(facts, recommend(facts))
    assert "learned to escalate early" in preview
    assert "REFLECT_LOG" in preview


# ---------------------------------------------------------------------------
# Narrative construction
# ---------------------------------------------------------------------------


def test_narrative_includes_demographics_setting_complaint():
    facts = _make_facts(
        age="62", sex="M", setting="ED", presenting_complaint="chest pain"
    )
    preview = build_draft_preview(facts, recommend(facts))
    assert "62" in preview
    assert "M" in preview
    assert "ED" in preview
    assert "chest pain" in preview


def test_narrative_includes_procedure_and_supervision():
    facts = _make_facts(
        setting="ED", procedure="RSI", supervision="consultant"
    )
    preview = build_draft_preview(facts, recommend(facts))
    assert "RSI" in preview
    assert "consultant" in preview


def test_long_learning_point_is_truncated_in_narrative():
    long_lp = "learned " + "x" * 100
    facts = _make_facts(learning_point=long_lp)
    preview = build_draft_preview(facts, recommend(facts))
    # Truncation marker must appear somewhere (in the narrative outline section)
    assert "..." in preview
    # The narrative outline line (indented) should be capped; the raw facts
    # section above it may still show the verbatim value as captured.
    narrative_lines = [
        line for line in preview.split("\n")
        if line.startswith("  Learning:") and "..." in line
    ]
    assert narrative_lines, "narrative outline Learning line should be truncated with ..."


def test_preview_is_pure_does_not_mutate_facts():
    """build_draft_preview is a pure function - facts unchanged after call."""
    facts = _STEMI_FACTS
    facts_before = tuple(facts)
    build_draft_preview(facts, recommend(facts))
    assert facts == facts_before
