"""Voice profile Kaizen sampler — read-only service boundary.

This module is the only place that reaches out to Kaizen to pull existing
portfolio entries for voice-profile learning. The Telegram flow only calls it
after the user chooses the Kaizen learning path.

Contract:
- Pure read-only: never submits, deletes, edits, or creates Kaizen content.
- Reads as the requesting user, never as whoever is signed in to the shared
  managed Chrome. It opens an isolated browser context (the same per-user
  bootstrap the portfolio sync uses), replays the user's kept Kaizen session
  first and falls back to their saved password. Passwordless users only have
  the kept session; when Kaizen has ended it they are asked to reconnect.
- Normal tests mock the page reader and never touch live Kaizen.
- Returns a typed result so callers can branch on availability without parsing
  free text.

History: until 2026-09-28 this read through the ``browser-harness`` CLI in the
shared Chrome profile. A harness upgrade removed its ``-c`` flag, every read
failed, and the shared profile could in any case be signed in as a different
user from the one asking.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


logger = logging.getLogger(__name__)

KAIZEN_ENTRIES_URL = "https://kaizenep.com/events/list/All"
# How many recent entries to read. Ten gives the style model enough to work
# with and keeps the read to about a minute.
SAMPLE_ENTRY_COUNT = 10
# Admin records ("Add a Supervisor", "Add a Post") hold no free text.
_ADMIN_TITLE_PREFIXES = ("add a ",)
MAX_ENTRIES_SCANNED = 20
MIN_FINISHED_SAMPLES = 3
READ_TIMEOUT_SECONDS = 180
CLOSE_TIMEOUT_SECONDS = 10


class SampleWindow(str, Enum):
    """Kept for stale buttons: every window now reads the recent entries."""

    RECENT_10 = "recent_10"
    LAST_6M = "last_6m"
    LAST_12M = "last_12m"


WINDOW_LABELS = {
    SampleWindow.RECENT_10: "your recent Kaizen entries",
    SampleWindow.LAST_6M: "your recent Kaizen entries",
    SampleWindow.LAST_12M: "your recent Kaizen entries",
}


class SamplerStatus(str, Enum):
    NOT_AVAILABLE = "not_available"
    NO_SAMPLES = "no_samples"
    OK = "ok"


@dataclass
class SamplerResult:
    status: SamplerStatus
    window: SampleWindow
    samples: List[str] = field(default_factory=list)
    message: Optional[str] = None
    reason: Optional[str] = None

    @property
    def has_samples(self) -> bool:
        return self.status == SamplerStatus.OK and bool(self.samples)


def parse_window(raw: str) -> Optional[SampleWindow]:
    """Map a raw callback token (e.g. ``"recent_10"``) to ``SampleWindow``.

    Returns ``None`` for unknown tokens so callers can show a friendly error
    instead of crashing on a stale button.
    """
    try:
        return SampleWindow(raw)
    except ValueError:
        return None


# Reasons that mean "sign in to Kaizen again" rather than "something broke".
RECONNECT_REASONS = frozenset({
    "login_required",
    "session_expired",
    "credentials_missing",
    "credentials_unavailable",
    "credentials_rejected",
})


_ROWS_JS = r"""
(() => {
  const text = el => (el && el.textContent ? el.textContent.trim().replace(/\s+/g, ' ') : '');
  const rows = Array.from(document.querySelectorAll('.row.event-inner'));
  return rows.map(row => {
    const a = row.querySelector('a[href*="/events/view"], a[router-link]');
    const href = a ? a.href : '';
    return {
      href,
      title: text(row.querySelector('h2.entry-title, .entry-title') || a),
    };
  }).filter(r => r.href && /\/events\/view/.test(r.href));
})()
"""

_DETAIL_JS = r"""
(() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const fields = Array.from(document.querySelectorAll('.form-text__form-group')).map(g => {
    const labelEl = g.querySelector('.form-text__control-label, .control-label, label');
    const label = clean(labelEl ? labelEl.textContent : '');
    let value = clean(g.innerText);
    if (label && value.startsWith(label)) value = value.slice(label.length).trim();
    return {label, value};
  }).filter(f => f.value);
  const draft = Array.from(document.querySelectorAll('.label'))
    .some(e => clean(e.textContent).toUpperCase() === 'DRAFT');
  return {fields, draft};
})()
"""

_SKIP_LABELS = ("attach", "file", "assessor", "supervisor", "curriculum", "procedural", "title", "date")
# Free text a doctor wrote reads as sentences; curriculum ticks and form
# boilerplate do not.
_SKIP_VALUE_MARKERS = ("key capability", "please select", "em curriculum")


def _is_login_url(url: str) -> bool:
    lowered = (url or "").lower()
    return (
        "auth.kaizenep.com" in lowered
        or "eportfolio.rcem.ac.uk" in lowered
        or "login" in lowered
    )


def _sample_from_fields(fields: list[dict]) -> Optional[str]:
    chunks = []
    for item in fields or []:
        label = (item.get("label") or "").lower()
        value = (item.get("value") or "").strip()
        if len(value) < 80:
            continue
        if any(skip in label for skip in _SKIP_LABELS):
            continue
        if any(marker in value.lower() for marker in _SKIP_VALUE_MARKERS):
            continue
        chunks.append(value[:2500])
    if not chunks:
        return None
    return "\n\n".join(chunks[:4])


async def _open_readonly(page: Any, url: str) -> None:
    await page.goto(url, wait_until="load", timeout=30000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    # Kaizen renders with Angular after the network settles.
    await asyncio.sleep(2)


async def _read_entries(page: Any, limit: int) -> dict:
    """Read the free-text of the user's most recent entries. Navigation only."""
    await _open_readonly(page, KAIZEN_ENTRIES_URL)
    if _is_login_url(page.url):
        return {"status": "not_available", "reason": "login_required", "samples": []}

    rows = await page.evaluate(_ROWS_JS) or []
    # Prefer finished entries: drafts are often ones Portfolio Guru wrote, and
    # learning the doctor's style from the bot's own output is circular.
    finished: list[str] = []
    drafts: list[str] = []
    seen: set[str] = set()
    scanned = 0
    for row in rows:
        if len(finished) >= limit or scanned >= MAX_ENTRIES_SCANNED:
            break
        href = row.get("href")
        title = (row.get("title") or "").strip().lower()
        if not href or href in seen or title.startswith(_ADMIN_TITLE_PREFIXES):
            continue
        seen.add(href)
        scanned += 1
        try:
            await _open_readonly(page, href)
            detail = await page.evaluate(_DETAIL_JS) or {}
        except Exception as exc:
            logger.info("voice sampler: skipped one entry: %s", type(exc).__name__)
            continue
        sample = _sample_from_fields(detail.get("fields", []))
        if sample:
            (drafts if detail.get("draft") else finished).append(sample)
    samples = finished[:limit]
    if len(samples) < MIN_FINISHED_SAMPLES:
        samples = (samples + drafts)[:limit]
    return {"status": "ok" if samples else "no_samples", "samples": samples}


async def _read_as_user(telegram_user_id: int, limit: int) -> dict:
    """Authenticate an isolated context as this user, then read their entries."""
    from kaizen_sync import (
        _close_session,
        _load_user_credentials,
        _login_kaizen_page,
        _open_kaizen_session_page,
        _persist_session_state,
        _restore_cached_session,
    )

    page, pw = await _open_kaizen_session_page()
    if page is None:
        return {"status": "not_available", "reason": "browser_unavailable", "samples": []}
    context = getattr(page, "context", None)
    try:
        try:
            credentials = _load_user_credentials(telegram_user_id)
        except Exception:
            credentials = None
        username = credentials[0] if credentials else None

        try:
            authed = await _restore_cached_session(page, telegram_user_id, username)
        except Exception:
            authed = False

        if not authed:
            if not credentials:
                from kaizen_connection import is_passwordless

                reason = "session_expired" if is_passwordless(telegram_user_id) else "credentials_missing"
                return {"status": "not_available", "reason": reason, "samples": []}
            username, password = credentials
            try:
                logged_in = await _login_kaizen_page(page, username, password)
            except Exception:
                return {"status": "not_available", "reason": "reconnect_failed", "samples": []}
            if not logged_in:
                return {"status": "not_available", "reason": "credentials_rejected", "samples": []}
            if context is not None:
                try:
                    await _persist_session_state(context, telegram_user_id, username)
                except Exception:
                    pass

        return await _read_entries(page, limit)
    finally:
        try:
            await asyncio.wait_for(_close_session(context, pw), timeout=CLOSE_TIMEOUT_SECONDS)
        except Exception:
            pass


async def sample_kaizen_entries(
    telegram_user_id: int,
    window: SampleWindow = SampleWindow.RECENT_10,
) -> SamplerResult:
    """Read the user's recent Kaizen entries, read-only, as that user."""
    try:
        payload = await asyncio.wait_for(
            _read_as_user(telegram_user_id, SAMPLE_ENTRY_COUNT),
            timeout=READ_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        payload = {"status": "not_available", "reason": "timeout", "samples": []}
    except Exception as exc:
        logger.warning("voice sampler: unexpected error: %s", type(exc).__name__)
        payload = {"status": "not_available", "reason": "unexpected_error", "samples": []}

    status = payload.get("status")
    samples = [str(sample) for sample in payload.get("samples", []) if str(sample).strip()]
    if status == "ok" and samples:
        return SamplerResult(status=SamplerStatus.OK, window=window, samples=samples)
    if status == "no_samples":
        return SamplerResult(status=SamplerStatus.NO_SAMPLES, window=window)

    reason = payload.get("reason") or "unavailable"
    logger.warning("voice sampler: Kaizen read unavailable for user %s: %s", telegram_user_id, reason)
    message = (
        "I couldn't read your Kaizen entries just now. "
        "Try again in a minute, or add 3-5 examples manually."
    )
    if reason in RECONNECT_REASONS:
        message = (
            "Kaizen needs reconnecting before I can learn from your entries. "
            "Reconnect Kaizen, then try again, or add examples manually."
        )
    return SamplerResult(
        status=SamplerStatus.NOT_AVAILABLE,
        window=window,
        message=message,
        reason=reason,
    )
