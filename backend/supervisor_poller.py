"""Polling loop for the Clinical Supervisor mode.

The poller is invoked on a timer (Sprint 4 will wire a scheduler). On each
tick it:

1. Lists the supervisor's Kaizen assessor queue using the read-only mapper.
2. Diffs the live UUIDs against a :class:`state_tracker.TrackedState` mirror.
3. Records *every* newly-seen UUID so we never re-fire notifications for
   already-known tickets.
4. Returns only the *new* summaries (optionally filtered to unfilled) so the
   caller can produce one Telegram notification per row.

Strict safety contract:

- This module never opens a ticket form, never inspects ticket content
  beyond the queue row, and never clicks ``Fill in``, ``Save``, ``Submit``,
  ``Sign``, ``Approve``, ``Delete`` or ``Send``.
- On any error from Kaizen the poller returns an empty result with the
  failure recorded — the state file is **not** mutated so a transient
  outage cannot drop a real ticket from the to-notify set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from playwright.async_api import Page

from assessor_mapper import AssessorTicketSummary, extract_assessment_rows
from state_tracker import TrackedState

logger = logging.getLogger(__name__)


_UNFILLED_STATES = ("pending", "in progress", "awaiting", "not started", "open")
_FILLED_STATES = ("complete", "completed", "submitted", "signed", "filled", "done")


@dataclass
class PollResult:
    """Outcome of a single poll cycle."""

    new_tickets: list[AssessorTicketSummary] = field(default_factory=list)
    error: str | None = None


def classify_ticket_status(summary: AssessorTicketSummary) -> str:
    """Return ``"unfilled"`` / ``"filled"`` / ``"unknown"`` for a queue row.

    Classification priority, all from the queue row (no ticket open):

    1. Textual ``state`` (when Kaizen surfaces ``.event-section-progress-state``).
    2. ``fill_action`` — presence/absence of a Fill in anchor on the row.
       True → unfilled, False → filled (no assessor work to do).
    3. Otherwise → ``"unknown"`` (fixture without either signal).

    Ahmed Mahdi's supervisor queue does not render the state badge, so
    ``fill_action`` is the actual unfilled signal there.
    """
    state = (summary.state or "").strip().lower()
    if state:
        if any(token in state for token in _FILLED_STATES):
            return "filled"
        if any(token in state for token in _UNFILLED_STATES):
            return "unfilled"
    if summary.fill_action is True:
        return "unfilled"
    if summary.fill_action is False:
        return "filled"
    return "unknown"


def diff_against_state(
    summaries: Iterable[AssessorTicketSummary],
    state: TrackedState,
) -> list[AssessorTicketSummary]:
    """Return only the summaries whose UUIDs are not already in ``state``."""
    new_summaries: list[AssessorTicketSummary] = []
    for summary in summaries:
        if not summary.uuid:
            continue
        if state.is_new_ticket(summary.uuid):
            new_summaries.append(summary)
    return new_summaries


async def poll_assessment_queue(
    state: TrackedState,
    *,
    page: Page,
    limit: int = 20,
    unfilled_only: bool = False,
    persist: bool = False,
) -> PollResult:
    """Run one poll cycle against the supervisor's Kaizen assessor queue.

    The caller is responsible for the Playwright session (CDP connection,
    login). This keeps the poller decoupled from credential handling and lets
    a single browser context drive both polling and on-demand ticket reads.
    """
    try:
        summaries = await extract_assessment_rows(page, limit=limit)
    except Exception as exc:
        logger.warning("Supervisor poll failed: %s", exc)
        return PollResult(new_tickets=[], error=str(exc))

    fresh = diff_against_state(summaries, state)
    # Record every freshly-seen UUID so we don't re-fire notifications.
    for summary in fresh:
        state.mark_seen(summary.uuid or "", status=classify_ticket_status(summary))

    if unfilled_only:
        emitted = [s for s in fresh if classify_ticket_status(s) == "unfilled"]
    else:
        emitted = list(fresh)

    if persist:
        try:
            state.save()
        except OSError as exc:
            logger.warning("State tracker save failed: %s", exc)

    return PollResult(new_tickets=emitted)
