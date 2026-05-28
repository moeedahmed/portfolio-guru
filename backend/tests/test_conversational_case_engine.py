"""Offline coverage for the vNext conversational case engine.

These tests enforce the case-memory vs chat-memory boundary: side
conversation never adds to ``CaseWorkspace.facts``, image/document
facts stay stricter/unconfirmed until the user confirms them, and
draft eligibility only ever exposes source-backed facts.
"""

import pytest

from conversational_case_engine import (
    ActionKind,
    CaseFact,
    CaseState,
    CaseWorkspace,
    IngestEvent,
    IngestKind,
    SourceType,
    apply_event,
    new_workspace,
)


def _action_kinds(snapshot) -> list[ActionKind]:
    return [action.kind for action in snapshot.actions]


def test_full_case_in_one_message_reaches_draft_ready():
    """A rich text case with ≥3 facts (including a clinical key) goes straight to DRAFT_READY."""
    workspace = new_workspace()
    event = IngestEvent(
        turn_id="t1",
        text=(
            "62M presented to ED with chest pain. Diagnosed STEMI, "
            "activated cath lab, transferred to PCI."
        ),
        source_type=SourceType.TEXT,
        kind=IngestKind.CASE_DETAIL,
        extracted_facts=(
            ("age", "62"),
            ("sex", "M"),
            ("presenting_complaint", "chest pain"),
            ("diagnosis", "STEMI"),
        ),
    )

    snapshot = apply_event(workspace, event)

    # 4 eligible facts including clinical keys → draft-ready threshold met
    assert snapshot.workspace.state is CaseState.DRAFT_READY
    assert {fact.key for fact in snapshot.workspace.facts} == {
        "age",
        "sex",
        "presenting_complaint",
        "diagnosis",
    }
    assert all(
        fact.source_type is SourceType.TEXT and fact.source_turn_id == "t1"
        for fact in snapshot.workspace.facts
    )
    assert _action_kinds(snapshot) == [ActionKind.OFFER_DRAFT]


def test_fragmented_case_details_accumulate_in_one_workspace():
    workspace = new_workspace()
    fragments = [
        IngestEvent(
            turn_id="t1",
            text="Saw a 62M in resus",
            source_type=SourceType.TEXT,
            kind=IngestKind.CASE_DETAIL,
            extracted_facts=(("age", "62"), ("sex", "M")),
        ),
        IngestEvent(
            turn_id="t2",
            text="Looked like a STEMI on ECG",
            source_type=SourceType.TEXT,
            kind=IngestKind.CASE_DETAIL,
            extracted_facts=(("diagnosis", "STEMI"),),
        ),
        IngestEvent(
            turn_id="t3",
            text="Consultant supervised RSI before transfer",
            source_type=SourceType.VOICE,
            kind=IngestKind.CASE_DETAIL,
            extracted_facts=(("supervision", "consultant"),),
        ),
    ]

    snapshot = None
    for event in fragments:
        snapshot = apply_event(workspace if snapshot is None else snapshot.workspace, event)

    assert snapshot is not None
    assert snapshot.workspace.case_id == workspace.case_id
    # 4 facts including clinical keys — draft-ready threshold met by t2 onward.
    assert snapshot.workspace.state is CaseState.DRAFT_READY
    assert {fact.key for fact in snapshot.workspace.facts} == {
        "age",
        "sex",
        "diagnosis",
        "supervision",
    }
    # Source provenance is preserved per turn.
    assert snapshot.workspace.fact_for("diagnosis").source_turn_id == "t2"
    assert snapshot.workspace.fact_for("supervision").source_type is SourceType.VOICE


def test_side_question_does_not_pollute_source_backed_facts():
    workspace = apply_event(
        new_workspace(),
        IngestEvent(
            turn_id="t1",
            text="62M chest pain, STEMI",
            source_type=SourceType.TEXT,
            kind=IngestKind.CASE_DETAIL,
            extracted_facts=(("age", "62"), ("diagnosis", "STEMI")),
        ),
    ).workspace

    side_event = IngestEvent(
        turn_id="t2",
        text="By the way, what forms would this support?",
        source_type=SourceType.TEXT,
        kind=IngestKind.SIDE_QUESTION,
    )

    snapshot = apply_event(workspace, side_event)

    assert snapshot.workspace.facts == workspace.facts
    assert snapshot.workspace.state == workspace.state
    assert any(turn.turn_id == "t2" for turn in snapshot.workspace.chat_turns)
    assert _action_kinds(snapshot) == [ActionKind.ANSWER_CHAT]


def test_late_correction_overrides_earlier_fact_as_user_confirmed():
    after_initial = apply_event(
        new_workspace(),
        IngestEvent(
            turn_id="t1",
            text="62M, STEMI",
            source_type=SourceType.TEXT,
            kind=IngestKind.CASE_DETAIL,
            extracted_facts=(("age", "62"), ("diagnosis", "STEMI")),
        ),
    ).workspace

    correction = IngestEvent(
        turn_id="t2",
        text="Actually he was 65, not 62.",
        source_type=SourceType.TEXT,
        kind=IngestKind.CORRECTION,
        corrections=(("age", "65"),),
    )

    snapshot = apply_event(after_initial, correction)

    age_fact = snapshot.workspace.fact_for("age")
    assert age_fact is not None
    assert age_fact.value == "65"
    assert age_fact.source_type is SourceType.USER_CONFIRMATION
    assert age_fact.source_turn_id == "t2"
    assert age_fact.confirmed is True
    # Untouched facts stay where they were.
    assert snapshot.workspace.fact_for("diagnosis").value == "STEMI"


def test_image_derived_facts_are_stricter_and_excluded_from_draft():
    workspace = new_workspace()
    image_event = IngestEvent(
        turn_id="t1",
        text="<photo of obs chart>",
        source_type=SourceType.IMAGE,
        kind=IngestKind.CASE_DETAIL,
        extracted_facts=(("bp", "92/60"), ("hr", "118")),
    )

    snapshot = apply_event(workspace, image_event)

    facts = snapshot.workspace.facts
    assert all(fact.is_stricter and not fact.confirmed for fact in facts)
    assert snapshot.workspace.draft_eligible_facts() == ()
    assert ActionKind.REQUEST_FACT_CONFIRMATION in _action_kinds(snapshot)

    # Asking for a draft now must refuse — no source-backed eligible facts.
    draft_attempt = apply_event(
        snapshot.workspace,
        IngestEvent(
            turn_id="t2",
            text="draft this",
            source_type=SourceType.TEXT,
            kind=IngestKind.REQUEST_DRAFT,
        ),
    )
    assert _action_kinds(draft_attempt) == [ActionKind.DRAFT_NOT_READY]

    # After explicit user confirmation, the stricter facts become eligible.
    confirmed = apply_event(
        snapshot.workspace,
        IngestEvent(
            turn_id="t3",
            text="Yes, those obs are right",
            source_type=SourceType.TEXT,
            kind=IngestKind.CONFIRMATION,
        ),
    )
    assert {fact.key for fact in confirmed.workspace.draft_eligible_facts()} == {
        "bp",
        "hr",
    }


def test_start_again_creates_a_separate_case():
    first = apply_event(
        new_workspace(),
        IngestEvent(
            turn_id="t1",
            text="62M chest pain",
            source_type=SourceType.TEXT,
            kind=IngestKind.CASE_DETAIL,
            extracted_facts=(("age", "62"),),
        ),
    ).workspace

    snapshot = apply_event(
        first,
        IngestEvent(
            turn_id="t2",
            text="Actually, new case: 45F with abdominal pain.",
            source_type=SourceType.TEXT,
            kind=IngestKind.NEW_CASE,
            extracted_facts=(("age", "45"), ("sex", "F")),
        ),
    )

    assert snapshot.workspace.case_id != first.case_id
    assert snapshot.workspace.fact_for("age").value == "45"
    assert snapshot.workspace.state is CaseState.COLLECTING
    assert ActionKind.START_NEW_CASE in _action_kinds(snapshot)


def test_portfolio_question_from_idle_produces_chat_action_without_filing():
    snapshot = apply_event(
        new_workspace(),
        IngestEvent(
            turn_id="t1",
            text="What forms count for SLO11 at ST5?",
            source_type=SourceType.TEXT,
            kind=IngestKind.SIDE_QUESTION,
        ),
    )

    assert snapshot.workspace.state is CaseState.IDLE
    assert snapshot.workspace.facts == ()
    assert _action_kinds(snapshot) == [ActionKind.ANSWER_CHAT]
    # No saving action ever fires from a side question.
    assert all(action.kind is not ActionKind.SAVE_DRAFT for action in snapshot.actions)


def test_possible_case_detail_requests_user_confirmation():
    snapshot = apply_event(
        new_workspace(),
        IngestEvent(
            turn_id="t1",
            text="Had a thing in resus, not sure if it counts",
            source_type=SourceType.TEXT,
            kind=IngestKind.POSSIBLE_CASE_DETAIL,
            extracted_facts=(("note", "resus event"),),
        ),
    )

    assert snapshot.workspace.state is CaseState.POSSIBLE_CASE
    assert _action_kinds(snapshot) == [ActionKind.REQUEST_CASE_CONFIRMATION]


def test_request_save_only_succeeds_after_offer_draft():
    workspace = apply_event(
        new_workspace(),
        IngestEvent(
            turn_id="t1",
            text="62M chest pain, STEMI",
            source_type=SourceType.TEXT,
            kind=IngestKind.CASE_DETAIL,
            extracted_facts=(("age", "62"), ("diagnosis", "STEMI")),
        ),
    ).workspace

    premature_save = apply_event(
        workspace,
        IngestEvent(
            turn_id="t2",
            text="save it",
            source_type=SourceType.TEXT,
            kind=IngestKind.REQUEST_SAVE,
        ),
    )
    assert _action_kinds(premature_save) == [ActionKind.DRAFT_NOT_READY]

    after_draft = apply_event(
        premature_save.workspace,
        IngestEvent(
            turn_id="t3",
            text="draft it",
            source_type=SourceType.TEXT,
            kind=IngestKind.REQUEST_DRAFT,
        ),
    )
    assert after_draft.workspace.state is CaseState.DRAFT_READY

    save_now = apply_event(
        after_draft.workspace,
        IngestEvent(
            turn_id="t4",
            text="save it",
            source_type=SourceType.TEXT,
            kind=IngestKind.REQUEST_SAVE,
        ),
    )
    assert save_now.workspace.state is CaseState.SAVING
    assert _action_kinds(save_now) == [ActionKind.SAVE_DRAFT]


def test_abandon_marks_case_without_clearing_audit_trail():
    workspace = apply_event(
        new_workspace(),
        IngestEvent(
            turn_id="t1",
            text="62M chest pain",
            source_type=SourceType.TEXT,
            kind=IngestKind.CASE_DETAIL,
            extracted_facts=(("age", "62"),),
        ),
    ).workspace

    snapshot = apply_event(
        workspace,
        IngestEvent(
            turn_id="t2",
            text="cancel",
            source_type=SourceType.TEXT,
            kind=IngestKind.ABANDON,
        ),
    )

    assert snapshot.workspace.state is CaseState.ABANDONED
    assert snapshot.workspace.facts == workspace.facts  # audit trail preserved
    assert _action_kinds(snapshot) == [ActionKind.ABANDON_CASE]


def test_ingest_event_validates_required_fields():
    with pytest.raises(ValueError, match="turn_id"):
        IngestEvent(
            turn_id="",
            text="x",
            source_type=SourceType.TEXT,
            kind=IngestKind.SIDE_QUESTION,
        )


def test_case_fact_draft_eligibility_policy():
    text_fact = CaseFact(
        key="age",
        value="62",
        source_type=SourceType.TEXT,
        source_turn_id="t1",
    )
    image_fact = CaseFact(
        key="bp",
        value="92/60",
        source_type=SourceType.IMAGE,
        source_turn_id="t2",
    )
    confirmed_image = CaseFact(
        key="hr",
        value="118",
        source_type=SourceType.IMAGE,
        source_turn_id="t3",
        confirmed=True,
    )

    assert text_fact.draft_eligible is True
    assert image_fact.draft_eligible is False
    assert confirmed_image.draft_eligible is True


def test_rich_text_case_reaches_draft_ready_in_one_turn():
    """Acceptance-criteria: full STEMI case → DRAFT_READY in a single message."""
    workspace = new_workspace()
    event = IngestEvent(
        turn_id="t1",
        text=(
            "62M chest pain in ED, STEMI on ECG, cath lab activated, "
            "consultant supervised, learned to escalate early"
        ),
        source_type=SourceType.TEXT,
        kind=IngestKind.CASE_DETAIL,
        extracted_facts=(
            ("age", "62"),
            ("sex", "M"),
            ("setting", "ED"),
            ("presenting_complaint", "chest pain"),
            ("diagnosis", "STEMI"),
            ("procedure", "cath lab"),
            ("supervision", "consultant"),
            ("learning_point", "learned to escalate early"),
        ),
    )
    snapshot = apply_event(workspace, event)

    assert snapshot.workspace.state is CaseState.DRAFT_READY
    assert ActionKind.OFFER_DRAFT in _action_kinds(snapshot)
    eligible_keys = {f.key for f in snapshot.workspace.draft_eligible_facts()}
    assert {"age", "diagnosis", "supervision"} <= eligible_keys


def test_engine_readiness_threshold_requires_clinical_fact():
    """Demographics alone (age + sex) do not satisfy the draft-ready threshold."""
    workspace = new_workspace()
    event = IngestEvent(
        turn_id="t1",
        text="Saw 62M in clinic",
        source_type=SourceType.TEXT,
        kind=IngestKind.CASE_DETAIL,
        extracted_facts=(("age", "62"), ("sex", "M")),
    )
    snapshot = apply_event(workspace, event)

    # 2 eligible facts, none clinical → stays in COLLECTING
    assert snapshot.workspace.state is CaseState.COLLECTING
    assert _action_kinds(snapshot) == [ActionKind.ACK_CASE_DETAILS]


def test_side_question_does_not_advance_to_draft_ready():
    """Side questions in clinical language must not trigger DRAFT_READY."""
    workspace = apply_event(
        new_workspace(),
        IngestEvent(
            turn_id="t1",
            text="62M STEMI, cath lab",
            source_type=SourceType.TEXT,
            kind=IngestKind.CASE_DETAIL,
            extracted_facts=(("age", "62"), ("diagnosis", "STEMI")),
        ),
    ).workspace

    side_event = IngestEvent(
        turn_id="t2",
        text="By the way, what forms cover STEMI consultant supervisor cases in ED?",
        source_type=SourceType.TEXT,
        kind=IngestKind.SIDE_QUESTION,
    )
    snapshot = apply_event(workspace, side_event)

    # Side question must not inject facts or advance state
    assert snapshot.workspace.facts == workspace.facts
    assert snapshot.workspace.state == workspace.state
    assert _action_kinds(snapshot) == [ActionKind.ANSWER_CHAT]


def test_save_request_does_not_become_case_facts():
    """'File this to Kaizen' must not promote clinical terms into the workspace."""
    workspace = apply_event(
        new_workspace(),
        IngestEvent(
            turn_id="t1",
            text="62M STEMI, supervised",
            source_type=SourceType.TEXT,
            kind=IngestKind.CASE_DETAIL,
            extracted_facts=(("age", "62"), ("diagnosis", "STEMI")),
        ),
    ).workspace

    save_event = IngestEvent(
        turn_id="t2",
        text="file this STEMI consultant case to Kaizen",
        source_type=SourceType.TEXT,
        kind=IngestKind.REQUEST_SAVE,
    )
    snapshot = apply_event(workspace, save_event)

    # Save request should not add facts
    assert snapshot.workspace.facts == workspace.facts
    assert ActionKind.DRAFT_NOT_READY in _action_kinds(snapshot)


def test_chat_turns_record_separately_from_case_facts():
    initial = apply_event(
        new_workspace(),
        IngestEvent(
            turn_id="t1",
            text="62M chest pain",
            source_type=SourceType.TEXT,
            kind=IngestKind.CASE_DETAIL,
            extracted_facts=(("age", "62"),),
        ),
    ).workspace

    chatty = apply_event(
        initial,
        IngestEvent(
            turn_id="t2",
            text="By the way, can I file this from my phone?",
            source_type=SourceType.TEXT,
            kind=IngestKind.SIDE_QUESTION,
        ),
    )

    fact_turn_ids = {fact.source_turn_id for fact in chatty.workspace.facts}
    chat_turn_ids = {turn.turn_id for turn in chatty.workspace.chat_turns}
    assert "t1" in fact_turn_ids
    assert "t2" not in fact_turn_ids
    assert {"t1", "t2"} <= chat_turn_ids
