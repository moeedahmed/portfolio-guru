"""Controlled-flexibility contract for free-text workflow turns."""

from workflow_turn_policy import (
    WorkflowPhase,
    WorkflowTurnKind,
    decide_workflow_turn,
)


def decide(text: str, *, phase: WorkflowPhase, legacy_intent: str | None = None, failed: bool = False):
    return decide_workflow_turn(
        text,
        phase=phase,
        legacy_intent=legacy_intent,
        classifier_failed=failed,
    )


def test_command_sounding_clinical_text_is_content_not_a_state_change():
    examples = (
        "My ultrasound image saving and documentation was incomplete during the procedure.",
        "The case was cancelled by theatre because the patient became unstable.",
        "We saved him with adrenaline and continued the resuscitation.",
    )

    for text in examples:
        decision = decide(text, phase=WorkflowPhase.CASE_OPEN, legacy_intent="new_case")
        assert decision.kind is WorkflowTurnKind.ENRICH
        assert decision.state_action is None


def test_portfolio_question_preserves_the_current_workflow():
    decision = decide(
        "What is the difference between a CBD and a mini-CEX?",
        phase=WorkflowPhase.DRAFT_OPEN,
        legacy_intent="question_general",
    )

    assert decision.kind is WorkflowTurnKind.SIDE_QUESTION
    assert decision.state_action is None


def test_explicit_edit_is_permitted_only_when_a_draft_is_open():
    active = decide(
        "Actually change the diagnosis in this draft to pulmonary embolism.",
        phase=WorkflowPhase.DRAFT_OPEN,
        legacy_intent="edit_detail",
    )
    idle = decide(
        "Actually change the diagnosis in this draft to pulmonary embolism.",
        phase=WorkflowPhase.IDLE,
        legacy_intent="edit_detail",
    )

    assert active.kind is WorkflowTurnKind.EXPLICIT_EDIT
    assert active.state_action == "edit_draft"
    assert idle.kind is WorkflowTurnKind.CLARIFY
    assert idle.state_action is None

    polite = decide(
        "Can you tweak the reflection to focus on leadership?",
        phase=WorkflowPhase.DRAFT_OPEN,
        legacy_intent="edit_detail",
    )
    assert polite.kind is WorkflowTurnKind.EXPLICIT_EDIT
    assert polite.state_action == "edit_draft"


def test_substantial_clinical_text_after_completion_starts_a_new_case():
    decision = decide(
        "A 72-year-old patient presented to ED resus with septic shock, hypotension and a new oxygen requirement.",
        phase=WorkflowPhase.COMPLETED,
        legacy_intent="new_case",
    )

    assert decision.kind is WorkflowTurnKind.NEW_CASE
    assert decision.state_action == "start_new_case"


def test_short_post_completion_turns_do_not_start_a_new_case():
    thanks = decide("Thanks", phase=WorkflowPhase.COMPLETED, legacy_intent="chitchat")
    tweak = decide(
        "Can you tweak the reflection?",
        phase=WorkflowPhase.COMPLETED,
        legacy_intent="edit_detail",
    )

    assert thanks.kind is WorkflowTurnKind.CHAT
    assert thanks.state_action is None
    assert tweak.kind is WorkflowTurnKind.CLARIFY
    assert tweak.state_action is None


def test_explicit_new_case_request_confirms_when_a_case_is_open():
    decision = decide(
        "Start a new case",
        phase=WorkflowPhase.DRAFT_OPEN,
        legacy_intent="new_case",
    )

    assert decision.kind is WorkflowTurnKind.CONFIRM_STATE_CHANGE
    assert decision.state_action == "start_new_case"


def test_ambiguous_destructive_text_never_executes():
    for text in ("Forget it", "Start over", "Never mind"):
        decision = decide(text, phase=WorkflowPhase.DRAFT_OPEN, legacy_intent="new_case")
        assert decision.kind is WorkflowTurnKind.CONFIRM_STATE_CHANGE
        assert decision.state_action == "cancel_current"


def test_classifier_failure_fails_closed_without_state_change():
    decision = decide(
        "Please do something with that",
        phase=WorkflowPhase.DRAFT_OPEN,
        legacy_intent=None,
        failed=True,
    )

    assert decision.kind is WorkflowTurnKind.CLARIFY
    assert decision.state_action is None


def test_classifier_failure_fails_closed_for_implicit_case_enrichment():
    decision = decide(
        "The patient remained hypotensive so I gave oxygen and escalated to the consultant.",
        phase=WorkflowPhase.CASE_OPEN,
        legacy_intent=None,
        failed=True,
    )

    assert decision.kind is WorkflowTurnKind.CLARIFY
    assert decision.state_action is None


def test_add_that_clinical_detail_enriches_open_case_not_invalid_edit():
    decision = decide(
        "Add that I gave adrenaline, oxygen, and escalated to the consultant in resus.",
        phase=WorkflowPhase.CASE_OPEN,
        legacy_intent="edit_detail",
    )

    assert decision.kind is WorkflowTurnKind.ENRICH
    assert decision.state_action is None


def test_portfolio_evidence_detail_enriches_open_case():
    decision = decide(
        "I completed ATLS and have a certificate from the course.",
        phase=WorkflowPhase.CASE_OPEN,
        legacy_intent=None,
    )

    assert decision.kind is WorkflowTurnKind.ENRICH
    assert decision.state_action is None


def test_mixed_case_detail_and_portfolio_question_is_both_not_a_transition():
    decision = decide(
        "His BP was 80/40 and I escalated early — does this count for SLO3?",
        phase=WorkflowPhase.CASE_OPEN,
        legacy_intent="question_about_case",
    )

    assert decision.kind is WorkflowTurnKind.ENRICH_AND_ANSWER
    assert decision.state_action is None
    assert decision.case_detail == "His BP was 80/40 and I escalated early"


def test_a_whole_new_case_while_one_is_open_asks_before_merging():
    """A second patient pasted mid-draft used to be merged into the first."""
    second_case = (
        "72F presented with sudden onset left sided weakness, NIHSS 9, CT head showed no bleed, "
        "I discussed thrombolysis with the stroke team and the patient was treated within the window."
    )
    for phase in (WorkflowPhase.CASE_OPEN, WorkflowPhase.DRAFT_OPEN):
        decision = decide(second_case, phase=phase, legacy_intent="add_detail")
        assert decision.kind is WorkflowTurnKind.CONFIRM_STATE_CHANGE
        assert decision.state_action == "start_new_case"


def test_a_short_addition_still_enriches_the_open_case():
    addition = "The patient was also tachycardic at 130 and the consultant reviewed him in resus."
    decision = decide(addition, phase=WorkflowPhase.DRAFT_OPEN, legacy_intent="add_detail")
    assert decision.kind is WorkflowTurnKind.ENRICH


def test_a_short_answer_fills_a_draft_that_has_gaps():
    """The draft asks the doctor to reply with what is missing; "indirect"
    must fill it, not be turned away as unclear."""
    for text in ("indirect", "Supervision was indirect"):
        with_gaps = decide_workflow_turn(text, phase=WorkflowPhase.DRAFT_OPEN, legacy_intent="add_detail", draft_has_gaps=True)
        assert with_gaps.kind is WorkflowTurnKind.ENRICH
        without = decide(text, phase=WorkflowPhase.DRAFT_OPEN, legacy_intent="add_detail")
        assert without.kind is WorkflowTurnKind.CLARIFY


def test_gaps_never_turn_destructive_or_failed_turns_into_edits():
    assert decide_workflow_turn("Start over", phase=WorkflowPhase.DRAFT_OPEN, legacy_intent="new_case",
                                draft_has_gaps=True).kind is WorkflowTurnKind.CONFIRM_STATE_CHANGE
    assert decide_workflow_turn("Please do something with that", phase=WorkflowPhase.DRAFT_OPEN, legacy_intent=None,
                                classifier_failed=True, draft_has_gaps=True).kind is WorkflowTurnKind.CLARIFY
