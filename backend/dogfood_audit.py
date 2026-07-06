"""Local dogfood audit trail for agent-readable Portfolio Guru behaviour.

The log is local NDJSON for trusted product agents inspecting dogfood behaviour
without screenshots: inputs, bot responses, route decisions, draft payload
summaries, media handling, prompt retirement, and Kaizen save outcomes.

Clinical content is redacted and capped, but still readable enough to understand
why a turn routed the way it did. Audit append failures never affect the user.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

logger = logging.getLogger(__name__)

_PATH_ENV = "PORTFOLIO_GURU_DOGFOOD_AUDIT_PATH"
_DISABLED_ENV = "PORTFOLIO_GURU_DOGFOOD_AUDIT_DISABLED"
_MAX_TEXT_CHARS = 1800
_MAX_FIELD_CHARS = 500

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)")
_NHS_RE = re.compile(r"\b(?:NHS\s*)?(?:\d[\s-]?){10}\b", re.I)
_LONG_ID_RE = re.compile(r"\b[A-Z]{0,4}\d{6,}[A-Z]{0,3}\b", re.I)


def default_log_path() -> pathlib.Path:
    override = os.environ.get(_PATH_ENV)
    if override:
        return pathlib.Path(override)
    return pathlib.Path.home() / ".openclaw" / "data" / "portfolio-guru" / "dogfood-audit.ndjson"


def _enabled() -> bool:
    return os.environ.get(_DISABLED_ENV, "").strip().lower() not in {"1", "true", "yes", "on"}


def _redact_text(value: Any, *, limit: int = _MAX_TEXT_CHARS) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return ""
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _NHS_RE.sub("[REDACTED_NHS_NUMBER]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    text = _LONG_ID_RE.sub("[REDACTED_ID]", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[:limit].rstrip() + f"... [truncated {len(text) - limit} chars]"
    return text


def text_fingerprint(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _safe_filename(value: Any) -> str | None:
    if value is None:
        return None
    name = os.path.basename(str(value)).strip()
    return _redact_text(name, limit=180)


def message_metadata(message: Any, *, include_text: bool = True) -> dict[str, Any]:
    """Return a redacted, path-free summary of a Telegram message-like object."""
    if message is None:
        return {}
    text = getattr(message, "text", None)
    caption = getattr(message, "caption", None)
    document = getattr(message, "document", None)
    voice = getattr(message, "voice", None) or getattr(message, "audio", None)
    video = getattr(message, "video", None)
    photos = getattr(message, "photo", None) or []
    kind = "text"
    if photos:
        kind = "photo"
    elif video:
        kind = "video"
    elif voice:
        kind = "voice"
    elif document:
        kind = "document"

    media = document or voice or video or (photos[-1] if photos else None)
    record: dict[str, Any] = {
        "message_kind": kind,
        "chat_type": getattr(getattr(message, "chat", None), "type", None),
        "message_id": getattr(message, "message_id", None),
        "has_text": bool(text),
        "has_caption": bool(caption),
    }
    if include_text:
        record["text_preview"] = _redact_text(text)
        record["text_sha256_16"] = text_fingerprint(text)
        record["caption_preview"] = _redact_text(caption)
        record["caption_sha256_16"] = text_fingerprint(caption)
    if media is not None:
        record.update(
            {
                "file_name": _safe_filename(getattr(media, "file_name", None)),
                "mime_type": getattr(media, "mime_type", None),
                "file_size": getattr(media, "file_size", None),
                "file_id_sha256_16": text_fingerprint(getattr(media, "file_id", None)),
            }
        )
    return record


def summarise_draft_payload(draft_or_fields: Any, *, form_type: str | None = None) -> dict[str, Any]:
    """Redacted summary of a draft payload, preserving field names and previews."""
    if hasattr(draft_or_fields, "fields"):
        fields = dict(getattr(draft_or_fields, "fields", {}) or {})
        form_type = form_type or getattr(draft_or_fields, "form_type", None)
    elif isinstance(draft_or_fields, dict):
        fields = dict(draft_or_fields)
    else:
        raw = getattr(draft_or_fields, "model_dump", lambda: {})()
        fields = dict(raw or {})

    previews: dict[str, Any] = {}
    for key, value in fields.items():
        if value in (None, "", [], {}):
            previews[str(key)] = None
        elif isinstance(value, (list, tuple, set)):
            previews[str(key)] = [_redact_text(item, limit=180) for item in list(value)[:12]]
        elif isinstance(value, dict):
            previews[str(key)] = {
                str(k): _redact_text(v, limit=180)
                for k, v in list(value.items())[:12]
            }
        else:
            previews[str(key)] = _redact_text(value, limit=_MAX_FIELD_CHARS)

    present = [key for key, value in fields.items() if value not in (None, "", [], {})]
    return {
        "form_type": form_type,
        "field_count": len(fields),
        "present_fields": [str(key) for key in present],
        "field_previews": previews,
    }


def record_event(
    event_type: str,
    *,
    user_id: int | str | None = None,
    username: str | None = None,
    session_id: str | None = None,
    payload: dict[str, Any] | None = None,
    log_path: pathlib.Path | None = None,
) -> dict[str, Any] | None:
    """Append a single audit event. Never raises into the caller."""
    if not _enabled():
        return None
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "user_id": user_id,
        "username": _redact_text(username, limit=120),
        "session_id": session_id,
        "payload": payload or {},
        "version": 1,
    }
    path = log_path or default_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        logger.debug("Dogfood audit append failed", exc_info=True)
        return None
    return record


def iter_records(log_path: pathlib.Path | None = None) -> Iterator[dict[str, Any]]:
    path = log_path or default_log_path()
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def count_by_event(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        event_type = str(record.get("event_type") or "unknown")
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


__all__ = [
    "count_by_event",
    "default_log_path",
    "iter_records",
    "message_metadata",
    "record_event",
    "summarise_draft_payload",
    "text_fingerprint",
]
