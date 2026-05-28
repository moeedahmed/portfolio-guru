"""Adapter contract: Telegram-shaped messages → vNext IngestEvents.

The adapter is the boundary between the future private vNext polling
loop and the deterministic case engine. It must:

* Preserve the user's raw wording on text inputs without inventing
  facts or extracting fields itself.
* Tag voice/image/document inputs with the correct source type so the
  engine can apply its stricter-source policy without ever seeing
  fabricated extractions from this slice.
* Stay inert for unsupported media — never escalate an unknown blob
  into a case-state mutation.

These tests pin those invariants. They use light SimpleNamespace
fixtures rather than importing ``python-telegram-bot`` so the adapter
contract stays duck-typed and cheap to test.
"""

from types import SimpleNamespace

import pytest

from conversational_case_engine import (
    IngestEvent,
    IngestKind,
    SourceType,
    apply_event,
    new_workspace,
)
from telegram_vnext_adapter import (
    SUPPORTED_DOCUMENT_EXTENSIONS,
    event_from_telegram_message,
)


def _text_message(text: str, *, message_id: int = 101, chat_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        caption=None,
        voice=None,
        audio=None,
        photo=[],
        document=None,
        message_id=message_id,
        chat=SimpleNamespace(id=chat_id),
    )


def _voice_message(*, caption: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        text=None,
        caption=caption,
        voice=SimpleNamespace(file_id="voice-1", duration=14),
        audio=None,
        photo=[],
        document=None,
        message_id=102,
        chat=SimpleNamespace(id=7),
    )


def _audio_message() -> SimpleNamespace:
    return SimpleNamespace(
        text=None,
        caption=None,
        voice=None,
        audio=SimpleNamespace(file_id="audio-1", duration=22),
        photo=[],
        document=None,
        message_id=103,
        chat=SimpleNamespace(id=7),
    )


def _photo_message(*, caption: str | None = None) -> SimpleNamespace:
    photo_size = SimpleNamespace(file_id="photo-1", width=1280, height=960)
    return SimpleNamespace(
        text=None,
        caption=caption,
        voice=None,
        audio=None,
        photo=[photo_size],
        document=None,
        message_id=104,
        chat=SimpleNamespace(id=7),
    )


def _document_message(file_name: str, *, caption: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        text=None,
        caption=caption,
        voice=None,
        audio=None,
        photo=[],
        document=SimpleNamespace(file_name=file_name, mime_type="application/pdf"),
        message_id=105,
        chat=SimpleNamespace(id=7),
    )


# --- Text -----------------------------------------------------------------


def test_text_message_preserves_raw_wording_as_text_source():
    raw = "62M chest pain, ECG showed STEMI, activated cath lab"
    message = _text_message(raw)

    event = event_from_telegram_message(message)

    assert isinstance(event, IngestEvent)
    assert event.text == raw
    assert event.source_type is SourceType.TEXT
    # Conservative demographics extractor pulls verbatim "62M" only; it
    # never invents diagnosis/management facts even though the router
    # classifies this as a case description.
    assert event.extracted_facts == (("age", "62"), ("sex", "M"))
    assert event.corrections == ()


def test_clinical_text_routes_to_possible_case_detail_with_source_tied_demographics():
    message = _text_message(
        "Had a difficult airway case with a 62M in resus, managed RSI with the consultant."
    )

    event = event_from_telegram_message(message)

    assert event.kind is IngestKind.POSSIBLE_CASE_DETAIL
    # Only literal demographics — no fabricated diagnosis/supervision/etc.
    assert event.extracted_facts == (("age", "62"), ("sex", "M"))

    snapshot = apply_event(new_workspace(), event)
    fact_keys = {fact.key for fact in snapshot.workspace.facts}
    assert fact_keys == {"age", "sex"}
    assert snapshot.workspace.chat_turns[0].text == message.text


def test_clinical_text_without_demographics_yields_no_extracted_facts():
    message = _text_message(
        "Had a difficult airway case in resus, managed RSI with the consultant, "
        "transferred to ICU after intubation."
    )

    event = event_from_telegram_message(message)

    assert event.kind is IngestKind.POSSIBLE_CASE_DETAIL
    assert event.extracted_facts == ()

    snapshot = apply_event(new_workspace(), event)
    assert snapshot.workspace.facts == ()
    assert snapshot.workspace.chat_turns[0].text == message.text


def test_extraction_only_runs_for_possible_case_detail_text():
    """SIDE_QUESTION / REQUEST_SAVE text never feeds the case workspace.

    Even when a portfolio question or save command literally contains
    "62M", we refuse to promote it into a case fact — the router has
    already decided this turn is not case material.
    """

    portfolio_q = event_from_telegram_message(
        _text_message("What forms would a 62M chest pain case support?")
    )
    assert portfolio_q.kind is IngestKind.SIDE_QUESTION
    assert portfolio_q.extracted_facts == ()

    save_cmd = event_from_telegram_message(
        _text_message("File this 45F sepsis case as a CBD in Kaizen")
    )
    assert save_cmd.kind is IngestKind.REQUEST_SAVE
    assert save_cmd.extracted_facts == ()


def test_portfolio_question_text_routes_to_side_question():
    event = event_from_telegram_message(
        _text_message("What forms would this support for my portfolio?")
    )

    assert event.kind is IngestKind.SIDE_QUESTION
    assert event.source_type is SourceType.TEXT


def test_file_request_text_routes_to_request_save():
    event = event_from_telegram_message(
        _text_message("File this as a CBD in Kaizen")
    )

    assert event.kind is IngestKind.REQUEST_SAVE


def test_unknown_text_routes_to_side_question_not_case_ingest():
    event = event_from_telegram_message(_text_message("blurple lampshade Tuesday"))

    assert event.kind is IngestKind.SIDE_QUESTION


def test_empty_text_is_inert_side_question():
    event = event_from_telegram_message(_text_message("   "))

    assert event.kind is IngestKind.SIDE_QUESTION
    assert event.text == ""


# --- Voice ----------------------------------------------------------------


def test_voice_message_uses_voice_source_with_no_extracted_facts():
    event = event_from_telegram_message(_voice_message())

    assert event.source_type is SourceType.VOICE
    assert event.extracted_facts == ()
    # Voice is treated as a chat turn until transcription is wired in a
    # later slice — the engine must not silently invent voice facts.
    assert event.kind is IngestKind.SIDE_QUESTION
    assert "voice" in event.text.lower()


def test_voice_caption_is_preserved_as_text_content():
    event = event_from_telegram_message(
        _voice_message(caption="Quick reflection on the airway case")
    )

    assert event.text == "Quick reflection on the airway case"
    assert event.source_type is SourceType.VOICE


def test_audio_message_is_treated_like_voice():
    event = event_from_telegram_message(_audio_message())

    assert event.source_type is SourceType.VOICE
    assert event.extracted_facts == ()


# --- Image ----------------------------------------------------------------


def test_image_message_keeps_stricter_image_source_with_no_facts():
    event = event_from_telegram_message(_photo_message())

    assert event.source_type is SourceType.IMAGE
    assert event.extracted_facts == ()
    assert event.kind is IngestKind.POSSIBLE_CASE_DETAIL


def test_image_message_never_produces_draft_eligible_facts():
    """Stricter sources must stay unconfirmed even after engine ingest."""

    snapshot = apply_event(new_workspace(), event_from_telegram_message(_photo_message()))

    assert snapshot.workspace.draft_eligible_facts() == ()
    assert snapshot.workspace.has_unconfirmed_stricter_facts() is False  # no facts at all yet


def test_image_caption_is_preserved_as_text_content():
    event = event_from_telegram_message(
        _photo_message(caption="ED obs chart after fluids")
    )

    assert event.text == "ED obs chart after fluids"


# --- Document -------------------------------------------------------------


def test_supported_document_uses_stricter_document_source():
    event = event_from_telegram_message(_document_message("handover.pdf"))

    assert event.source_type is SourceType.DOCUMENT
    assert event.kind is IngestKind.POSSIBLE_CASE_DETAIL
    assert event.extracted_facts == ()


def test_supported_document_extension_set_covers_expected_formats():
    for ext in (".pdf", ".docx", ".pptx", ".txt"):
        assert ext in SUPPORTED_DOCUMENT_EXTENSIONS


def test_unsupported_document_is_safe_side_question():
    event = event_from_telegram_message(_document_message("trace.exe"))

    assert event.source_type is SourceType.DOCUMENT
    assert event.kind is IngestKind.SIDE_QUESTION
    assert event.extracted_facts == ()


def test_document_caption_is_preserved_as_text_content():
    event = event_from_telegram_message(
        _document_message("audit.docx", caption="QI audit handover")
    )

    assert event.text == "QI audit handover"


# --- Turn IDs & defaults --------------------------------------------------


def test_turn_id_defaults_to_chat_and_message_id():
    event = event_from_telegram_message(_text_message("hi", message_id=42, chat_id=99))

    assert event.turn_id == "tg:99:42"


def test_explicit_turn_id_overrides_default():
    event = event_from_telegram_message(_text_message("hi"), turn_id="custom-turn")

    assert event.turn_id == "custom-turn"


def test_missing_message_id_produces_unique_turn_id():
    bare = SimpleNamespace(
        text="hi",
        caption=None,
        voice=None,
        audio=None,
        photo=[],
        document=None,
    )

    a = event_from_telegram_message(bare)
    b = event_from_telegram_message(bare)

    assert a.turn_id != b.turn_id
    assert a.turn_id.startswith("tg:")


# --- Fallback / safety ----------------------------------------------------


def test_completely_empty_message_returns_inert_side_question():
    bare = SimpleNamespace(
        text=None,
        caption=None,
        voice=None,
        audio=None,
        photo=[],
        document=None,
    )

    event = event_from_telegram_message(bare)

    assert event.kind is IngestKind.SIDE_QUESTION
    assert event.extracted_facts == ()


def test_adapter_does_not_invoke_any_network_helpers(monkeypatch):
    """The adapter must not call vision/whisper/document extractors.

    This pins the safety contract for this slice: no network or LLM
    side effects from converting a Telegram message to an IngestEvent.
    """

    import vision
    import whisper

    def _explode(*_args, **_kwargs):
        raise AssertionError("adapter must not invoke extraction helpers")

    monkeypatch.setattr(vision, "extract_from_image", _explode)
    monkeypatch.setattr(whisper, "transcribe_voice", _explode)

    # Drive each media type through the adapter — none should call out.
    event_from_telegram_message(_text_message("safe text"))
    event_from_telegram_message(_voice_message())
    event_from_telegram_message(_photo_message())
    event_from_telegram_message(_document_message("notes.pdf"))


@pytest.mark.parametrize(
    "message_factory",
    [
        lambda: _text_message("hi"),
        _voice_message,
        _audio_message,
        _photo_message,
        lambda: _document_message("x.pdf"),
        lambda: _document_message("x.exe"),
    ],
)
def test_adapter_returns_frozen_ingest_event(message_factory):
    event = event_from_telegram_message(message_factory())

    assert isinstance(event, IngestEvent)
    with pytest.raises((AttributeError, TypeError)):
        event.text = "tampered"  # type: ignore[misc]
