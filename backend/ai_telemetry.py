"""Lightweight, test-friendly telemetry for AI/LLM layer usage.

Every LLM call in the extractor flows through ``extractor._generate``. Recording
each call here gives us a single, auditable view of how much we lean on the model
and which deterministic rails are actually saving calls — without coupling to any
external metrics service.

Design goals:
- Cheap and always-on: in-memory counters, no I/O on the hot path by default.
- Test seam: ``snapshot()`` / ``reset()`` let tests assert "an LLM call happened"
  or "no LLM call happened" for a given purpose, which is how we prove a
  deterministic path stayed deterministic.
- Optional durable sink: set ``PG_AI_TELEMETRY_LOG_PATH`` to append one NDJSON
  record per call, matching the existing filing_attempt_log / filing_result_logger
  conventions. Off by default so tests stay filesystem-clean.

Nothing in here may raise into the caller: telemetry must never break a filing.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import Counter
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_TOTAL = 0
_BY_PURPOSE: Counter[str] = Counter()
_BY_STATUS: Counter[str] = Counter()

_LOG_PATH_ENV = "PG_AI_TELEMETRY_LOG_PATH"


def record(
    purpose: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    status: str = "success",
    elapsed_ms: int | None = None,
) -> None:
    """Record a single LLM call.

    ``purpose`` is a stable, low-cardinality label for the calling concern
    (e.g. ``"intent_classification"``, ``"form_recommendation"``,
    ``"filing_recovery_copy"``). ``status`` is one of ``success`` | ``fallback``
    | ``error``. Never raises.
    """
    purpose = purpose or "unspecified"
    global _TOTAL
    try:
        with _LOCK:
            _TOTAL += 1
            _BY_PURPOSE[purpose] += 1
            _BY_STATUS[status] += 1
        logger.debug(
            "ai_call purpose=%s provider=%s model=%s status=%s elapsed_ms=%s",
            purpose, provider, model, status, elapsed_ms,
        )
        _maybe_write_ndjson(purpose, provider, model, status, elapsed_ms)
    except Exception as e:  # telemetry must never break the caller
        logger.debug("ai_telemetry.record failed: %s", e)


def snapshot() -> dict:
    """Return a copy of current counters. Safe to call from tests."""
    with _LOCK:
        return {
            "total": _TOTAL,
            "by_purpose": dict(_BY_PURPOSE),
            "by_status": dict(_BY_STATUS),
        }


def calls_for(purpose: str) -> int:
    """Number of recorded calls for a given purpose."""
    with _LOCK:
        return _BY_PURPOSE.get(purpose, 0)


def reset() -> None:
    """Clear all counters. Intended for test isolation."""
    global _TOTAL
    with _LOCK:
        _TOTAL = 0
        _BY_PURPOSE.clear()
        _BY_STATUS.clear()


def _maybe_write_ndjson(
    purpose: str,
    provider: str | None,
    model: str | None,
    status: str,
    elapsed_ms: int | None,
) -> None:
    path = os.environ.get(_LOG_PATH_ENV)
    if not path:
        return
    record_obj = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": "ai_call",
        "purpose": purpose,
        "provider": provider,
        "model": model,
        "status": status,
        "elapsed_ms": elapsed_ms,
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record_obj) + "\n")
    except Exception as e:
        logger.debug("ai_telemetry ndjson write failed: %s", e)
