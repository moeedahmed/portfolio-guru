"""Clinical Supervisor workflow helpers.

Wires the previously-shipped backend pieces together so the rest of the
bot can integrate at well-defined seams:

* Role caching with a "don't demote known-good" rule — protects against a
  transient MyTimeline outage flipping an `"assessor"` account back to
  `"unknown"`.
* PHI-free notification payloads — every queue row that a supervisor sees
  before they tap **Open** carries form type, ticket UUID, and Kaizen URL
  only. Trainee names, dates, narratives, attachment metadata are
  withheld until explicit Open.
* Callable poll orchestrator (``run_supervisor_poll``) — combines the
  existing :mod:`supervisor_poller` + :mod:`state_tracker` into a single
  awaitable that a future scheduler or a /supervisor command can drive.
  Read-only; never opens a ticket, never clicks Save/Submit/Sign/Fill in.
* Render helpers — turn payloads / ticket-data into Telegram-safe text.
  These are pure formatters, no I/O.

Nothing here registers Telegram handlers or starts a scheduler — that
wiring is the next slice. Keeping the orchestration callable lets the
test suite drive the full read-only path against mocked Playwright + a
``tmp_path`` state file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from playwright.async_api import Page

import role_detector
import supervisor_poller
from assessor_mapper import AssessorTicketSummary, redact_ticket_title
from assessor_reader import AssessorTicketData, detect_form_type
from profile_store import get_kaizen_role, store_kaizen_role
from state_tracker import TrackedState

logger = logging.getLogger(__name__)

CanonicalRole = Literal["assessor", "trainee", "unknown"]
TicketStatus = Literal["unfilled", "filled", "unknown"]

# Map every raw role string produced upstream to the canonical three-way
# vocabulary used everywhere downstream (cache, notification gating, UX).
# The trainee bucket merges every trainee surface KaizenProvider exposes
# today; "" and unrecognised strings collapse to "unknown".
_RAW_ROLE_TO_CANONICAL: dict[str, CanonicalRole] = {
    "assessor": "assessor",
    "supervisor": "assessor",
    "trainee": "trainee",
    "hst": "trainee",
    "accs": "trainee",
    "intermediate": "trainee",
    "accs_intermediate": "trainee",
    "sas": "trainee",
    # Non-training shapes (SAS / CESR / Portfolio Pathway) still own a
    # personal portfolio, so they live in the trainee bucket for
    # notification gating. The split between ``non_training_*`` and ``sas``
    # only affects the user-facing settings label and form-catalogue
    # picker, not the canonical role surface.
    "non_training_higher": "trainee",
    "non_training_unknown": "trainee",
    "unknown": "unknown",
    "": "unknown",
}


def normalize_role(raw_role: str | None) -> CanonicalRole:
    """Translate any upstream role string into ``assessor`` / ``trainee`` / ``unknown``."""
    if not raw_role:
        return "unknown"
    return _RAW_ROLE_TO_CANONICAL.get(raw_role.strip().lower(), "unknown")


def set_role_if_better(telegram_user_id: int, raw_role: str | None) -> CanonicalRole:
    """Cache the role for a user, refusing to overwrite a known-good value with ``unknown``.

    Returns the role that is now cached (which may be the existing value
    if the incoming probe was inconclusive). Idempotent.
    """
    incoming = normalize_role(raw_role)
    existing_raw = get_kaizen_role(telegram_user_id)
    existing = normalize_role(existing_raw) if existing_raw is not None else None

    if incoming == "unknown" and existing in ("assessor", "trainee"):
        # Defensive: a single MyTimeline timeout / provider hiccup must never
        # flip a Clinical Supervisor account back to a trainee-onboarding path.
        return existing

    if existing == incoming:
        # No-op when nothing changes; still re-write None → "unknown" so a
        # caller can rely on `get_kaizen_role` returning a string post-call.
        if existing_raw is None:
            store_kaizen_role(telegram_user_id, incoming)
        return incoming

    store_kaizen_role(telegram_user_id, incoming)
    return incoming


@dataclass
class SupervisorNotificationPayload:
    """PHI-free notification payload for an unread assessor queue row.

    The bot may render this in chat *before* the supervisor taps Open.
    It deliberately excludes trainee names, case narrative, dates,
    attachments, and any other field captured by deeper Kaizen probes.
    """

    ticket_uuid: str
    ticket_url: str
    form_type: str | None  # CBD / DOPS / MINI_CEX / QIAT / ESLE / None
    redacted_title: str | None
    status: TicketStatus


def notification_payload_from_summary(
    summary: AssessorTicketSummary,
    *,
    status: TicketStatus | None = None,
) -> SupervisorNotificationPayload | None:
    """Build a PHI-free payload from a queue summary, or ``None`` when un-emittable."""
    if not summary.uuid or not summary.href:
        return None
    classified = status or supervisor_poller.classify_ticket_status(summary)
    return SupervisorNotificationPayload(
        ticket_uuid=summary.uuid,
        ticket_url=summary.href,
        form_type=detect_form_type(summary.title),
        redacted_title=redact_ticket_title(summary.title),
        status=classified,  # type: ignore[arg-type]
    )


def build_notification_payloads(
    summaries: Iterable[AssessorTicketSummary],
    *,
    statuses: Iterable[TicketStatus] = ("unfilled",),
) -> list[SupervisorNotificationPayload]:
    """Turn a list of summaries into PHI-free payloads filtered by status.

    Defaults to ``("unfilled",)`` because the first notification slice only
    surfaces actionable rows. Callers can pass ``("unfilled", "unknown")``
    or similar when a wider net is desired.
    """
    allowed = {s for s in statuses}
    out: list[SupervisorNotificationPayload] = []
    for summary in summaries:
        payload = notification_payload_from_summary(summary)
        if payload is None:
            continue
        if payload.status not in allowed:
            continue
        out.append(payload)
    return out


def render_supervisor_notification_text(payload: SupervisorNotificationPayload) -> str:
    """Render a Telegram-safe notification line. PHI-free."""
    form_label = payload.form_type or (payload.redacted_title or "Assessment")
    return (
        f"📥 New assessment to review: *{form_label}*\n"
        f"Open or skip — tap *Open* to fetch the trainee section. The bot won't open the ticket on Kaizen until you do."
    )


def render_supervisor_ticket_detail_text(data: AssessorTicketData) -> str:
    """Render the trainee section once the supervisor explicitly tapped Open.

    PHI is visible here by design — the supervisor requested it. The render
    is still defensive: each field uses the canonical label captured by
    ``assessor_reader.build_ticket_data``; raw HTML never reaches Telegram.
    """
    header_bits: list[str] = []
    if data.form_type:
        header_bits.append(f"*{data.form_type}*")
    if data.title and data.title.strip() != (data.form_type or "").strip():
        header_bits.append(redact_ticket_title(data.title) or data.title)
    header = " — ".join(header_bits) if header_bits else "Assessment ticket"

    lines: list[str] = [f"📄 {header}"]
    if data.trainee_section:
        lines.append("\n*Trainee section*")
        for row in data.trainee_section:
            label = row.get("label") or ""
            value = row.get("value") or "—"
            lines.append(f"• {label}: {value}")
    if data.pending_assessor_fields:
        lines.append("\n*Assessor fields the bot will prompt you for*")
        for field_def in data.pending_assessor_fields:
            label = field_def.get("label") or field_def.get("key") or ""
            lines.append(f"• {label}")
    if data.ticket_url:
        lines.append(f"\nKaizen link: {data.ticket_url}")
    return "\n".join(lines)


@dataclass
class SupervisorPollOutcome:
    """Outcome of a single :func:`run_supervisor_poll` invocation."""

    role: CanonicalRole
    payloads: list[SupervisorNotificationPayload] = field(default_factory=list)
    state: TrackedState | None = None
    error: str | None = None
    skipped_reason: str | None = None


async def run_supervisor_poll(
    telegram_user_id: int,
    page: Page,
    *,
    state_path: Path | str,
    unfilled_only: bool = True,
    persist: bool = True,
    refresh_role: bool = True,
    notification_statuses: Iterable[TicketStatus] = ("unfilled",),
) -> SupervisorPollOutcome:
    """One read-only supervisor poll, end to end.

    1. (optional) Re-detect the account role from MyTimeline and update
       the cache — never demoting a known-good role.
    2. If the cached role is not ``"assessor"``, skip the poll entirely.
    3. Load the on-disk state tracker, run :func:`supervisor_poller.poll_assessment_queue`.
    4. Build PHI-free payloads for the freshly-seen rows whose classified
       status is in ``notification_statuses``.

    The caller owns the Playwright ``page`` (CDP login already done) and
    decides whether to schedule this on a timer or trigger it from a
    Telegram /supervisor command. Returning a dataclass instead of
    side-effecting Telegram keeps this testable without a bot mock.
    """
    refreshed_role: CanonicalRole | None = None
    if refresh_role:
        try:
            probe = await role_detector.detect_role(page)
        except Exception as exc:  # defensive — detect_role already swallows most
            logger.warning("Supervisor poll role probe failed: %s", exc)
            probe = "unknown"
        refreshed_role = set_role_if_better(telegram_user_id, probe)

    cached = normalize_role(get_kaizen_role(telegram_user_id))
    effective_role = refreshed_role or cached
    if effective_role != "assessor":
        return SupervisorPollOutcome(
            role=effective_role,
            skipped_reason=f"role={effective_role}; supervisor poll only runs for assessor accounts",
        )

    state = TrackedState.load(state_path)
    poll_result = await supervisor_poller.poll_assessment_queue(
        state,
        page=page,
        unfilled_only=unfilled_only,
        persist=persist,
    )
    if poll_result.error:
        return SupervisorPollOutcome(
            role=effective_role,
            state=state,
            error=poll_result.error,
        )
    payloads = build_notification_payloads(
        poll_result.new_tickets,
        statuses=notification_statuses,
    )
    return SupervisorPollOutcome(
        role=effective_role,
        payloads=payloads,
        state=state,
    )
