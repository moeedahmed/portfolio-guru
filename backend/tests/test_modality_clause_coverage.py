"""MODALITY_CLAUSE must be wired into every "send me more" moment named in
its own docstring — initial capture, extending a case, missing essentials,
and attachment context — and must not bleed into state-specific prompts that
truthfully accept a narrower set of inputs.
"""

from __future__ import annotations

from message_policy import MODALITY_CLAUSE, render_message


def test_initial_capture_names_the_modality_clause():
    assert MODALITY_CLAUSE in render_message("file_case_prompt")


def test_draft_gaps_invite_a_short_reply():
    """Missing details are listed under the draft (draft first, 25 Sep 2026),
    with one short line rather than the full modality list."""
    text = render_message("draft_gap_hint", items="a learning point", it_or_them="it")
    assert "reply with it" in text.lower()
    assert "voice note" in text


def test_extending_a_case_names_the_modality_clause():
    assert MODALITY_CLAUSE in render_message("gathering_captured")


def test_attachment_context_names_the_modality_clause():
    text = render_message("attachment_captured", attachment_label="Document", context_note="")
    assert MODALITY_CLAUSE in text


def test_source_and_photo_grounding_requests_were_retired():
    """The per-source refusal copy ('more clinical context needed' when the
    real cause was our own source-based filter) must not exist any more —
    intake facilitates a draft instead of blocking on input source."""
    from message_policy import MESSAGE_TEMPLATES

    assert "source_grounding_detail_request" not in MESSAGE_TEMPLATES
    assert "photo_grounding_detail_request" not in MESSAGE_TEMPLATES
