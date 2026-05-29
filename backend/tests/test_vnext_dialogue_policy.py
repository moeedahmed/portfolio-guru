"""Tests for the private vNext conversational reply policy."""

from __future__ import annotations

from conversational_case_engine import (
    CaseFact,
    CaseWorkspace,
    SourceType,
    new_workspace,
)
from vnext_dialogue_policy import (
    captured_fact_summary,
    collecting_reply,
    is_completion_request,
    next_missing_prompt,
    not_ready_reply,
)


def _workspace_with_facts(*facts: CaseFact) -> CaseWorkspace:
    workspace = new_workspace()
    return CaseWorkspace(
        case_id=workspace.case_id,
        state=workspace.state,
        facts=facts,
        chat_turns=workspace.chat_turns,
        pending_clarification=workspace.pending_clarification,
    )


def _fact(key: str, value: str) -> CaseFact:
    return CaseFact(
        key=key,
        value=value,
        source_type=SourceType.TEXT,
        source_turn_id="turn-1",
    )


def test_completion_request_detects_done_and_draft_intents():
    for text in ("done", "that's all", "draft it", "show draft", "file this"):
        assert is_completion_request(text)


def test_completion_request_ignores_case_detail():
    assert not is_completion_request("62M chest pain in ED with STEMI")


def test_captured_fact_summary_lists_human_labels():
    workspace = _workspace_with_facts(
        _fact("age", "62"),
        _fact("presenting_complaint", "chest pain"),
        _fact("learning_point", "escalate early"),
    )

    assert (
        captured_fact_summary(workspace)
        == "I have captured age, presenting complaint, learning point."
    )


def test_next_missing_prompt_asks_highest_value_gap():
    workspace = _workspace_with_facts(_fact("age", "62"), _fact("sex", "M"))

    assert next_missing_prompt(workspace).startswith("Where did this happen")


def test_collecting_reply_asks_for_done_without_raw_state():
    workspace = _workspace_with_facts(
        _fact("setting", "ED"),
        _fact("presenting_complaint", "chest pain"),
    )

    reply = collecting_reply(workspace)

    assert "Captured" in reply
    assert "Done" in reply
    assert "state" not in reply.lower()


def test_not_ready_reply_explains_gap_without_engine_reason():
    reply = not_ready_reply(new_workspace())

    assert "Not enough" in reply
    assert "no_source_backed_facts" not in reply
