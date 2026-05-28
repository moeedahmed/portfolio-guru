"""Telegram → vNext engine adapter.

Pure conversion layer for the private vNext test bot. Given a
Telegram-like message it produces an :class:`IngestEvent` the
conversational case engine can apply. Nothing in this module reaches
for the network, an LLM, or Kaizen — voice, image and document inputs
are emitted with the correct source type and no extracted facts so the
engine can apply its stricter-source policy without ever seeing
fabricated content from this slice.

Text inputs additionally pass through the conservative
:func:`vnext_text_extractor.extract_text_facts` adapter when the router
classifies them as case material. The extractor is a pure regex over
demographic literals (age, sex) — it never calls out, never infers
diagnosis/management/supervision, and returns an empty tuple when the
text is ambiguous so the engine stays in ``possible_case`` and asks the
user to confirm before drafting.

The adapter is duck-typed: it works with ``telegram.Message`` from
``python-telegram-bot`` and with any lightweight stand-in that exposes
the same attributes.
"""

from __future__ import annotations

import uuid
from typing import Any

from conversational_case_engine import IngestEvent, IngestKind, SourceType
from conversational_router import ConversationalIntent, route_message
from vnext_text_extractor import extract_text_facts

SUPPORTED_DOCUMENT_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".pptx",
    ".docx",
    ".doc",
    ".txt",
    ".md",
    ".rtf",
)

_VOICE_PLACEHOLDER = "[voice note received; transcription not wired in this slice]"
_IMAGE_PLACEHOLDER = "[image received; extraction not wired in this slice]"
_DOCUMENT_PLACEHOLDER_TEMPLATE = "[document received: {name}]"
_UNSUPPORTED_DOCUMENT_TEMPLATE = "[unsupported document: {name}]"


def event_from_telegram_message(
    message: Any, *, turn_id: str | None = None
) -> IngestEvent:
    """Convert a Telegram-like message into a vNext :class:`IngestEvent`.

    The adapter never extracts clinical facts. It preserves the user's
    raw wording on text inputs and records the captured source type for
    media, leaving any extraction work to a future slice that the
    orchestrator can layer on top.
    """

    turn_id = turn_id or _resolve_turn_id(message)

    text = _stripped(_get(message, "text"))
    if text:
        kind = _kind_for_text(text)
        extracted = _extracted_facts_for_text(text, kind)
        return IngestEvent(
            turn_id=turn_id,
            text=text,
            source_type=SourceType.TEXT,
            kind=kind,
            extracted_facts=extracted,
        )

    voice = _get(message, "voice") or _get(message, "audio")
    if voice is not None:
        caption = _stripped(_get(message, "caption"))
        return IngestEvent(
            turn_id=turn_id,
            text=caption or _VOICE_PLACEHOLDER,
            source_type=SourceType.VOICE,
            kind=IngestKind.SIDE_QUESTION,
        )

    if _has_photo(message):
        caption = _stripped(_get(message, "caption"))
        return IngestEvent(
            turn_id=turn_id,
            text=caption or _IMAGE_PLACEHOLDER,
            source_type=SourceType.IMAGE,
            kind=IngestKind.POSSIBLE_CASE_DETAIL,
        )

    document = _get(message, "document")
    if document is not None:
        file_name = _stripped(_get(document, "file_name")) or "unnamed"
        caption = _stripped(_get(message, "caption"))
        if not _is_supported_document(file_name):
            return IngestEvent(
                turn_id=turn_id,
                text=caption or _UNSUPPORTED_DOCUMENT_TEMPLATE.format(name=file_name),
                source_type=SourceType.DOCUMENT,
                kind=IngestKind.SIDE_QUESTION,
            )
        return IngestEvent(
            turn_id=turn_id,
            text=caption or _DOCUMENT_PLACEHOLDER_TEMPLATE.format(name=file_name),
            source_type=SourceType.DOCUMENT,
            kind=IngestKind.POSSIBLE_CASE_DETAIL,
        )

    return IngestEvent(
        turn_id=turn_id,
        text="",
        source_type=SourceType.TEXT,
        kind=IngestKind.SIDE_QUESTION,
    )


def _kind_for_text(text: str) -> IngestKind:
    intent = route_message(text).intent
    if intent is ConversationalIntent.NEW_CASE:
        return IngestKind.POSSIBLE_CASE_DETAIL
    if intent is ConversationalIntent.FILE_TO_KAIZEN:
        return IngestKind.REQUEST_SAVE
    return IngestKind.SIDE_QUESTION


def _extracted_facts_for_text(
    text: str, kind: IngestKind
) -> tuple[tuple[str, str], ...]:
    """Run the conservative text extractor only on case-bearing text.

    Side questions and save requests are not case material, so we never
    extract from them even when they happen to mention a demographic
    literal — leaving the engine on its conversational path instead of
    promoting an off-topic message into the case workspace.
    """

    if kind is not IngestKind.POSSIBLE_CASE_DETAIL:
        return ()
    return extract_text_facts(text)


def _resolve_turn_id(message: Any) -> str:
    message_id = _get(message, "message_id")
    chat_id = _get(_get(message, "chat"), "id")
    if message_id and chat_id is not None:
        return f"tg:{chat_id}:{message_id}"
    if message_id:
        return f"tg:{message_id}"
    return f"tg:{uuid.uuid4().hex}"


def _has_photo(message: Any) -> bool:
    photo = _get(message, "photo")
    if photo is None:
        return False
    try:
        return len(photo) > 0
    except TypeError:
        return bool(photo)


def _is_supported_document(file_name: str) -> bool:
    if not file_name:
        return False
    return file_name.lower().endswith(SUPPORTED_DOCUMENT_EXTENSIONS)


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    return getattr(obj, name, None)


def _stripped(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()
