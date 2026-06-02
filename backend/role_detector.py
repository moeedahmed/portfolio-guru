"""Detect a Kaizen account's role (trainee vs supervisor/assessor).

Pure-supervisor accounts render either the historic ``"You cannot create any
events!"`` barrier or a ``Clinical Supervisor`` chrome/title marker on the
MyTimeline surface because the account has no personal portfolio. Trainee
accounts render the normal timeline / "new event" affordances. We classify
the role from that evidence.

The classification is split into two layers so it can be tested without a
live browser:

* :func:`classify_role_from_timeline_text` is pure — takes the rendered body
  text and returns one of ``"assessor"`` / ``"trainee"`` / ``"unknown"``.
* :func:`detect_role` is the thin async wrapper that drives Playwright.

Strict safety contract: the detector only navigates to read-only landing pages
and reads ``document.title`` / ``document.body.innerText``. It never clicks ``Fill in``, ``Save``,
``Submit``, ``Sign``, ``Approve``, ``Delete`` or ``Send``.
"""

from __future__ import annotations

import logging
from typing import Literal

from playwright.async_api import Page

logger = logging.getLogger(__name__)

Role = Literal["assessor", "trainee", "unknown"]

MY_TIMELINE_URL = "https://kaizenep.com/events/list/MyTimeline"
DASHBOARD_URL = "https://kaizenep.com/dashboard"

# Evidence patterns. Matched case-insensitively on the rendered body text.
ASSESSOR_BARRIER_PATTERNS: tuple[str, ...] = (
    "you cannot create any events",  # Ahmed Mahdi's MyTimeline surface.
    "clinical supervisor",  # Current assessor chrome/title marker.
)

# Positive trainee markers. Only consulted when the assessor barrier is absent;
# their presence on a non-empty MyTimeline body confirms a trainee surface
# rather than a transient empty page (which should classify as ``unknown``).
TRAINEE_MARKER_PATTERNS: tuple[str, ...] = (
    "create new event",
    "create event",
    "my timeline",
)


def _matches_any(haystack: str, patterns: tuple[str, ...]) -> bool:
    lowered = haystack.lower()
    return any(pattern in lowered for pattern in patterns)


def classify_role_from_timeline_text(body_text: str | None) -> Role:
    """Return the role implied by a MyTimeline body-text snapshot.

    ``"assessor"``  → the assessor barrier text is present.
    ``"trainee"``   → no barrier and at least one trainee marker is present.
    ``"unknown"``   → empty / missing body, or no markers either way.
    """
    if not body_text or not body_text.strip():
        return "unknown"
    if _matches_any(body_text, ASSESSOR_BARRIER_PATTERNS):
        return "assessor"
    if _matches_any(body_text, TRAINEE_MARKER_PATTERNS):
        return "trainee"
    return "unknown"


async def detect_role(page: Page, *, timeline_url: str = MY_TIMELINE_URL) -> Role:
    """Navigate read-only to MyTimeline and classify the account's role.

    Any navigation or DOM read failure returns ``"unknown"`` rather than
    raising — the caller decides whether to retry. The Playwright session
    (CDP connection, login) is owned by the caller, mirroring the
    supervisor poller's contract.
    """
    role = await _classify_readonly_page(page, timeline_url, label="timeline")
    if role != "unknown":
        return role
    return await _classify_readonly_page(page, DASHBOARD_URL, label="dashboard")


async def _classify_readonly_page(page: Page, url: str, *, label: str) -> Role:
    try:
        await page.goto(url, wait_until="domcontentloaded")
    except Exception as exc:
        logger.warning("Role detector %s navigation failed: %s", label, exc)
        return "unknown"
    try:
        page_text = await page.evaluate(
            "() => [document.title || '', (document.body && document.body.innerText) ? document.body.innerText : ''].join('\\n')"
        )
    except Exception as exc:
        logger.warning("Role detector %s body read failed: %s", label, exc)
        return "unknown"
    return classify_role_from_timeline_text(page_text)
