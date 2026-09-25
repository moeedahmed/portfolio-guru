"""MODALITY_CLAUSE must be wired into every "send me more" moment named in
its own docstring — initial capture, extending a case, missing essentials,
and attachment context — and must not bleed into state-specific prompts that
truthfully accept a narrower set of inputs.
"""

from __future__ import annotations

from message_policy import MODALITY_CLAUSE, render_message


def test_initial_capture_names_the_modality_clause():
    assert MODALITY_CLAUSE in render_message("file_case_prompt")


def test_missing_essentials_names_the_modality_clause():
    assert MODALITY_CLAUSE in render_message("pre_draft_completeness_request", items="a learning point")


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
