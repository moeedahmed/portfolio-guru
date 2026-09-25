"""Portfolio Health watcher — the proactive layer over the Kaizen index.

`/health` answers "how is my portfolio right now?". This module answers the
question a snapshot cannot: "what has been stuck, and for how long?"

Kaizen renders every timeline row's sign-off workflow as a run of progress
icons, which `kaizen_sync` now reads into `evidence_items.state`. A row sitting
at `pending` is evidence the doctor has already submitted that nobody has
signed; a row at `draft` is evidence they started and never finished. Both are
invisible in Kaizen's own UI until you scroll a category and count icons, which
is exactly the chore Portfolio Guru exists to remove.

Design notes:

- **Age is measured from the event date, not from when we first saw it.**
  `state_since` records when *we* observed the current state, which is ~0 days
  on a first scan even for evidence stuck for months. The doctor's question is
  "my case was in June and still isn't signed", so the event date is the honest
  clock. `state_since` is kept for detecting transitions, not for ageing.
- **Read-only.** Nothing here opens Kaizen, chases an assessor, or writes to a
  portfolio. It reads the local index and produces text for the doctor.
- **No clinical content.** Chase copy names the form type, category and date.
  Descriptions hold patient detail and never appear in a nudge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Optional

from form_labels import form_label

from kaizen_index import _parse_kaizen_date, list_evidence_items

# States that mean the item is not finished. "pending" is waiting on someone
# else, "draft" is the doctor's own to close — a distinction the copy keeps,
# because only one of them is actionable by the doctor alone.
BLOCKED_STATES: frozenset[str] = frozenset({"pending", "draft"})

# Below this, an unsigned item is just a normal turnaround, not a problem worth
# interrupting someone for.
DEFAULT_CHASE_AFTER_DAYS = 14

# Past this, an item has almost certainly been forgotten rather than delayed.
STALE_AFTER_DAYS = 180


@dataclass(frozen=True)
class StuckItem:
    """One piece of evidence that has not completed its Kaizen workflow."""

    id: str
    event_type: str
    category: Optional[str]
    state: str
    event_date: date
    days_waiting: int
    detail_url: Optional[str]
    blocking_label: Optional[str]

    @property
    def is_stale(self) -> bool:
        return self.days_waiting >= STALE_AFTER_DAYS

    @property
    def waits_on_someone_else(self) -> bool:
        return self.state == "pending"


def _blocking_label(section_states: list[dict]) -> Optional[str]:
    """Return Kaizen's own words for the section holding the item up."""
    for entry in section_states or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("state") in BLOCKED_STATES and entry.get("label"):
            return str(entry["label"])
    return None


async def find_stuck_signoffs(
    user_id: str | int,
    *,
    min_days: int = DEFAULT_CHASE_AFTER_DAYS,
    today: Optional[date] = None,
) -> list[StuckItem]:
    """Return unfinished evidence older than ``min_days``, oldest first."""
    reference = today or date.today()
    stuck: list[StuckItem] = []

    for row in await list_evidence_items(user_id):
        state = (row.state or "").strip().lower()
        if state not in BLOCKED_STATES:
            continue
        if not row.date_occurred_on:
            continue

        event_date = _parse_kaizen_date(row.date_occurred_on)
        days = (reference - event_date).days
        if days < min_days:
            continue

        stuck.append(
            StuckItem(
                id=row.id,
                event_type=row.event_type or "Portfolio evidence",
                category=row.category,
                state=state,
                event_date=event_date,
                days_waiting=days,
                detail_url=row.detail_url,
                blocking_label=_blocking_label(row.section_states),
            )
        )

    stuck.sort(key=lambda item: item.days_waiting, reverse=True)
    return stuck


def summarise_stuck(items: list[StuckItem]) -> dict:
    """Counts the chase copy and any future dashboard both need."""
    awaiting = [item for item in items if item.waits_on_someone_else]
    return {
        "total": len(items),
        "awaiting_others": len(awaiting),
        "own_drafts": len(items) - len(awaiting),
        "stale": sum(1 for item in items if item.is_stale),
        "oldest_days": max((item.days_waiting for item in items), default=0),
    }


def _describe(item: StuckItem) -> str:
    when = item.event_date.strftime("%-d %b %Y")
    if item.waits_on_someone_else:
        return f"• {item.event_type} from {when} — waiting {item.days_waiting} days for sign-off"
    return f"• {item.event_type} from {when} — still a draft after {item.days_waiting} days"


def format_signoff_chase(items: list[StuckItem], *, limit: int = 5) -> Optional[str]:
    """Build the chase message, or None when there is nothing worth sending.

    Returning None matters as much as the text: a watcher that speaks every
    week regardless of whether anything changed gets muted, and then it cannot
    warn about the thing that does matter.
    """
    if not items:
        return None

    counts = summarise_stuck(items)
    lines = ["📌 *Evidence waiting in Kaizen*", ""]

    if counts["awaiting_others"] and counts["own_drafts"]:
        lines.append(
            f"{counts['awaiting_others']} item(s) waiting on someone else, "
            f"{counts['own_drafts']} still your own draft."
        )
    elif counts["awaiting_others"]:
        lines.append(f"{counts['awaiting_others']} item(s) submitted but not yet signed off.")
    else:
        lines.append(f"{counts['own_drafts']} item(s) started but never finished.")

    lines.append("")
    lines.extend(_describe(item) for item in items[:limit])

    if counts["total"] > limit:
        lines.append(f"…and {counts['total'] - limit} more.")

    lines.append("")
    lines.append(
        "Nothing here has been sent or chased for you — this is a read of your "
        "Kaizen portfolio so you can decide what to follow up."
    )
    return "\n".join(lines)


# ── Change detection ────────────────────────────────────────────────────────
#
# A weekly message that re-lists the same stuck items is the thing that gets
# muted, and once muted it can no longer report the item that just went
# unsigned. The index records every transition (``state_since``,
# ``previous_state``), so the watcher can speak only about what moved.


@dataclass(frozen=True)
class PortfolioChange:
    """One thing that moved since the watcher last spoke."""

    title: str
    form_type: Optional[str]
    event_date: date
    kind: str  # "stuck" | "cleared"
    url: Optional[str]


@dataclass(frozen=True)
class ChangeSet:
    newly_stuck: list[PortfolioChange]
    cleared: list[PortfolioChange]
    still_waiting: int

    @property
    def has_news(self) -> bool:
        return bool(self.newly_stuck or self.cleared)


def _transitioned_after(row, since: Optional[datetime]) -> bool:
    """True when the row's current state was first seen after ``since``."""
    if since is None:
        return True
    raw = (row.state_since or "").strip()
    if not raw:
        return False
    try:
        seen = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    return seen > since


async def detect_changes(
    user_id: str | int,
    *,
    since: Optional[datetime] = None,
    min_days: int = DEFAULT_CHASE_AFTER_DAYS,
    today: Optional[date] = None,
) -> ChangeSet:
    """What moved since ``since``: newly stuck evidence, and evidence cleared.

    ``since`` of None means "first run" — everything currently stuck counts as
    news, because the doctor has never been told.
    """
    reference = today or date.today()
    rows = await list_evidence_items(user_id)

    newly_stuck: list[PortfolioChange] = []
    cleared: list[PortfolioChange] = []
    still_waiting = 0

    for row in rows:
        state = (row.state or "").strip().lower()
        event_date = _parse_kaizen_date(row.date_occurred_on) if row.date_occurred_on else None

        if state in BLOCKED_STATES and event_date:
            if (reference - event_date).days < min_days:
                continue
            still_waiting += 1
            if _transitioned_after(row, since):
                newly_stuck.append(
                    PortfolioChange(
                        title=row.event_type or "Portfolio evidence",
                        form_type=row.event_type,
                        event_date=event_date,
                        kind="stuck",
                        url=row.detail_url,
                    )
                )
        elif (
            state
            and state not in BLOCKED_STATES
            and (row.previous_state or "").strip().lower() in BLOCKED_STATES
            and _transitioned_after(row, since)
            and event_date
        ):
            # Good news is worth sending too: it tells the doctor the chase
            # worked, and a watcher that only ever reports problems is one more
            # source of dread.
            cleared.append(
                PortfolioChange(
                    title=row.event_type or "Portfolio evidence",
                    form_type=row.event_type,
                    event_date=event_date,
                    kind="cleared",
                    url=row.detail_url,
                )
            )

    newly_stuck.sort(key=lambda c: c.event_date)
    cleared.sort(key=lambda c: c.event_date)
    return ChangeSet(newly_stuck=newly_stuck, cleared=cleared, still_waiting=still_waiting)


def format_change_report(changes: ChangeSet, *, limit: int = 4) -> Optional[str]:
    """Message the change, or return None and stay quiet.

    Returning None is the feature. A weekly repetition of an unchanged list
    trains the reader to ignore it, and then it cannot deliver the one week
    something actually moved.
    """
    if not changes.has_news:
        return None

    lines: list[str] = []
    if changes.cleared:
        lines.append(f"\u2705 *{len(changes.cleared)} signed off since last time*")
        for change in changes.cleared[:limit]:
            lines.append(f"\u2022 {_change_label(change)}")
        lines.append("")

    if changes.newly_stuck:
        lines.append(f"\U0001F4CC *{len(changes.newly_stuck)} newly waiting*")
        for change in changes.newly_stuck[:limit]:
            lines.append(f"\u2022 {_change_label(change)}")
        if len(changes.newly_stuck) > limit:
            lines.append(f"\u2026and {len(changes.newly_stuck) - limit} more")
        lines.append("")

    if changes.still_waiting:
        lines.append(f"_{changes.still_waiting} unfinished in total \u2014 /health for the list._")
    lines.append("_Nothing has been chased or submitted for you._")
    return "\n".join(lines).strip()


def _change_label(change: PortfolioChange) -> str:
    name = form_label(change.form_type, fallback=change.title)
    when = change.event_date.strftime("%-d %b %Y")
    label = f"{name} \u2014 {when}"
    return f"[{label}]({change.url})" if change.url else label
