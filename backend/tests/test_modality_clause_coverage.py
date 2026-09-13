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


def test_video_specific_grounding_stays_narrower_than_the_shared_clause():
    """The video-attachment grounding prompt only wants text/voice context —
    the bot never interprets video — so it must not claim the full modality
    clause is accepted for that follow-up."""
    from bot import _video_context_detail_request

    text = _video_context_detail_request()
    assert MODALITY_CLAUSE not in text
    assert "interpret" not in text.lower().replace("won't interpret", "")


def test_photo_grounding_stays_narrower_than_the_shared_clause():
    """The photo-grounding request wants the doctor's own words, not another
    image, so it must not advertise the full modality clause either."""
    text = render_message("photo_grounding_detail_request")
    assert MODALITY_CLAUSE not in text
