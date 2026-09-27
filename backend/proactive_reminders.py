"""Proactive reminders: one daily "should I speak?" decision per doctor.

The bot looks at every portfolio each evening, but on most days it says
nothing. It speaks more often only as the ARCP or appraisal month gets close,
and it jumps the queue only for something genuinely urgent. Agreed caps
(Moeed, 27 Sep 2026):

* Quiet — no month set, or more than 3 months away: at most 2 a month, and
  only when something changed.
* Steady — the last 3 months: at most 1 a week.
* Final — the last 4 weeks and the review month itself: at most 2 a week,
  never two days running.
* After — the month has passed: one message asking for the next month.

Urgent alerts (evidence waiting 60+ days for someone else's sign-off,
Portfolio Pathway evidence about to leave the six-year window) sit outside the
caps. Nothing sends more than one message a day, and the doctor's own
controls (Normal / Only urgent / Off, pause, "Less like this") always win.

This module holds no gap logic of its own. The checklists come from
``pathway_checklist`` (what /health shows), the stuck list and change report
from ``health_watch``. Everything here is policy over those, kept as pure
functions over a small per-user state record so it can be tested with fixed
dates, plus the flat-file store for that record.

No clinical content ever appears in a reminder: the inputs are form names,
counts, dates and the checklist's own action lines.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from data_paths import data_path

# ── Tiers and caps ──────────────────────────────────────────────────────────

QUIET = "quiet"
STEADY = "steady"
FINAL = "final"
AFTER = "after"

STEADY_WITHIN_DAYS = 91  # three months
FINAL_WITHIN_DAYS = 28  # four weeks
AFTER_FOR_DAYS = 31  # how long after the review month the "how did it go" stays due

# (most non-urgent messages, in this many days)
CAPS: dict[str, tuple[int, int]] = {
    QUIET: (2, 30),
    STEADY: (1, 7),
    FINAL: (2, 7),
    AFTER: (1, 7),
}
# A doctor who ignores reminders gets the next quieter tier's caps until they
# next engage, so a reminder that is not landing backs off on its own.
QUIETER = {FINAL: STEADY, STEADY: QUIET, QUIET: QUIET, AFTER: AFTER}
IGNORED_BEFORE_BACKOFF = 3

# Someone who ran /health or tapped a reminder this recently is already
# engaged; only urgent news interrupts them.
ENGAGED_WITHIN_DAYS = 2

REPEAT_AFTER_DAYS = 14  # the same digest point is not repeated sooner
MUTE_DAYS = 30  # "Less like this"
HISTORY_DAYS = 120  # send log kept this long

URGENT_STUCK_DAYS = 60
CESR_EXPIRY_ALERT_DAYS = 30
STALE_SCAN_DAYS = 14

LEVEL_NORMAL = "normal"
LEVEL_URGENT = "urgent"
LEVEL_OFF = "off"
LEVELS = (LEVEL_NORMAL, LEVEL_URGENT, LEVEL_OFF)

URGENT = "urgent"
MILESTONE = "milestone"
DIGEST = "digest"
KINDS = (URGENT, MILESTONE, DIGEST, AFTER)


def tier_for(review_month: Optional[date], today: date) -> str:
    """Which tier today falls in, from the stored ARCP or appraisal month.

    The month is stored as its first day, and the review can fall anywhere in
    it, so the whole month counts as the final stretch.
    """
    if review_month is None:
        return QUIET
    month_start = review_month.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    if today >= next_month:
        return AFTER if (today - next_month).days < AFTER_FOR_DAYS else QUIET
    days = (month_start - today).days
    if days <= FINAL_WITHIN_DAYS:
        return FINAL
    if days <= STEADY_WITHIN_DAYS:
        return STEADY
    return QUIET


# ── Inputs and outputs ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class StuckAlert:
    """Evidence waiting on someone else long enough to be worth an alert."""

    key: str  # stable per item, so it is alerted once
    label: str  # form name and date, never the description
    days_waiting: int


@dataclass(frozen=True)
class Signals:
    """What the daily check knows about one doctor today."""

    deadline_name: str  # "ARCP" or "appraisal"
    review_month: Optional[date]
    actions: tuple[str, ...]  # the /health checklist's own "do next" lines
    change_text: Optional[str] = None  # health_watch.format_change_report
    stuck_alerts: tuple[StuckAlert, ...] = ()
    cesr_expiring: int = 0  # signed-off items leaving the window within 30 days
    scan_date: Optional[date] = None  # last Kaizen scan; None = no index


@dataclass(frozen=True)
class Reminder:
    kind: str
    key: str
    text: str
    carries_changes: bool = False  # sending it moves the change-report clock


# ── Per-user state ──────────────────────────────────────────────────────────


def empty_state() -> dict[str, Any]:
    return {
        "level": LEVEL_NORMAL,
        "quiet_until": None,
        "muted": {},
        "sent": [],
        "ignored": 0,
        "engaged_on": None,
        "changes_since": None,
    }


def _d(raw: Any) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _sent_within(state: dict, days: int, today: date, *, urgent: bool = False) -> list[dict]:
    start = today - timedelta(days=days - 1)
    return [
        s
        for s in state.get("sent", [])
        if (_d(s.get("on")) or date.min) >= start
        and (urgent or s.get("kind") != URGENT)
    ]


def _ever_sent(state: dict, key: str) -> bool:
    return any(s.get("key") == key for s in state.get("sent", []))


def _sent_key_within(state: dict, key: str, days: int, today: date) -> bool:
    return any(s.get("key") == key for s in _sent_within(state, days, today, urgent=True))


# ── Message text ────────────────────────────────────────────────────────────


def _month(day: date) -> str:
    return day.strftime("%B %Y")


def _countdown(signals: Signals, today: date) -> Optional[str]:
    if signals.review_month is None:
        return None
    days = (signals.review_month.replace(day=1) - today).days
    name = signals.deadline_name
    if days <= 0:
        return f"⏳ Your {name} is this month ({_month(signals.review_month)})"
    weeks = days // 7
    when = f"{weeks} weeks" if weeks >= 2 else f"{days} days"
    return f"⏳ {when} to your {name} ({_month(signals.review_month)})"


def _stale_note(signals: Signals, today: date) -> Optional[str]:
    if signals.scan_date is None:
        return None
    if (today - signals.scan_date).days <= STALE_SCAN_DAYS:
        return None
    return f"_Based on your last Kaizen scan on {signals.scan_date.strftime('%-d %b')}._"


def _join(*parts: Optional[str]) -> str:
    return "\n\n".join(p for p in parts if p)


def _bullets(actions: tuple[str, ...], limit: int) -> Optional[str]:
    if not actions:
        return None
    return "\n".join(f"• {a}" for a in actions[:limit])


def _arcp_lead(signals: Signals) -> Optional[str]:
    if signals.deadline_name != "ARCP" or signals.review_month is None:
        return None
    return "Evidence needs to be in Kaizen two weeks before the panel."


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# ── Candidates ──────────────────────────────────────────────────────────────


def candidates(signals: Signals, state: dict, today: date) -> list[Reminder]:
    """Everything worth saying today, most important first. Caps come later."""
    tier = tier_for(signals.review_month, today)
    stale = _stale_note(signals, today)
    found: list[Reminder] = []

    # Urgent: things a doctor would want to hear about the day they happen.
    fresh = [a for a in signals.stuck_alerts if not _ever_sent(state, f"stuck:{a.key}")]
    if fresh:
        lines = ["📌 *Waiting over two months for sign-off*", ""]
        lines += [f"• {a.label} — {a.days_waiting} days" for a in fresh[:4]]
        if len(fresh) > 4:
            lines.append(f"…and {len(fresh) - 4} more.")
        lines += ["", "Worth a nudge to your assessor. Nothing has been chased for you."]
        found.append(
            Reminder(URGENT, "stuck:" + ",".join(sorted(a.key for a in fresh)), "\n".join(lines))
        )
    if signals.cesr_expiring:
        key = f"cesr_expiry:{today.strftime('%Y-%m')}"
        if not _ever_sent(state, key):
            n = signals.cesr_expiring
            found.append(
                Reminder(
                    URGENT,
                    key,
                    _join(
                        f"📁 *{n} signed-off item{'s' if n > 1 else ''} will leave your "
                        "Portfolio Pathway window within a month*",
                        "The GMC counts evidence from the last six years. If you are "
                        "close to applying, check whether you still need "
                        f"{'them' if n > 1 else 'it'}.",
                    ),
                )
            )

    if tier == AFTER and signals.review_month is not None:
        key = f"after:{signals.review_month.isoformat()}"
        if not _ever_sent(state, key):
            found.append(
                Reminder(
                    AFTER,
                    key,
                    _join(
                        f"🎓 Your {signals.deadline_name} month ({_month(signals.review_month)}) "
                        "has passed. I hope it went well.",
                        f"Tap below to set your next {signals.deadline_name} month, and "
                        "I'll pace reminders to it.",
                    ),
                )
            )

    # Milestones: once per review month, as each tier starts.
    if tier in (STEADY, FINAL) and signals.review_month is not None:
        mark = "4w" if tier == FINAL else "3m"
        key = f"milestone:{signals.review_month.isoformat()}:{mark}"
        if not _ever_sent(state, key) and not (
            tier == FINAL and _sent_key_within(state, f"milestone:{signals.review_month.isoformat()}:3m", 7, today)
        ):
            found.append(
                Reminder(
                    MILESTONE,
                    key,
                    _join(
                        _countdown(signals, today),
                        _arcp_lead(signals),
                        ("*Still to do*\n" + _bullets(signals.actions, 4))
                        if signals.actions
                        else "Open /health to see what's left.",
                        stale,
                    ),
                    carries_changes=False,
                )
            )

    # Digest: what moved in Kaizen, led by the single biggest gap. Far from
    # the deadline an unchanged gap is not news at all; closer in, it is worth
    # repeating a fortnight on.
    top = signals.actions[0] if signals.actions else None
    repeat_after = HISTORY_DAYS if tier == QUIET else REPEAT_AFTER_DAYS
    top_is_new = bool(top) and not _sent_key_within(
        state, "digest_action:" + _key(top or ""), repeat_after, today
    )
    if signals.change_text or top_is_new:
        found.append(
            Reminder(
                DIGEST,
                "digest_action:" + _key(top or "") if top_is_new else "digest_changes:" + _key(signals.change_text or ""),
                _join(
                    _countdown(signals, today) if tier != QUIET else None,
                    signals.change_text,
                    f"*Next step:* {top}" if top else None,
                    stale,
                ),
                carries_changes=bool(signals.change_text),
            )
        )
    return found


# ── The decision ────────────────────────────────────────────────────────────


def decide(signals: Signals, state: dict, today: date) -> Optional[Reminder]:
    """Return the one reminder to send today, or None to stay quiet."""
    level = state.get("level") or LEVEL_NORMAL
    if level == LEVEL_OFF:
        return None
    paused = _d(state.get("quiet_until"))
    if paused and today <= paused:
        return None
    if _sent_within(state, 1, today, urgent=True):
        return None  # one message a day, whatever it is

    muted = {
        kind for kind, until in (state.get("muted") or {}).items()
        if (_d(until) or date.min) >= today
    }
    options = [c for c in candidates(signals, state, today) if c.kind not in muted]

    urgent = [c for c in options if c.kind == URGENT]
    if urgent:
        return urgent[0]
    if level == LEVEL_URGENT:
        return None

    engaged = _d(state.get("engaged_on"))
    if engaged and (today - engaged).days < ENGAGED_WITHIN_DAYS:
        return None

    tier = tier_for(signals.review_month, today)
    if int(state.get("ignored") or 0) >= IGNORED_BEFORE_BACKOFF:
        tier = QUIETER[tier]
    most, window = CAPS[tier]
    if len(_sent_within(state, window, today)) >= most:
        return None
    if tier == FINAL and _sent_within(state, 2, today):
        return None  # never two days running

    return options[0] if options else None


def record_sent(state: dict, reminder: Reminder, today: date) -> dict:
    """The state after sending ``reminder`` today."""
    new = {**empty_state(), **state}
    cutoff = today - timedelta(days=HISTORY_DAYS)
    sent = [s for s in new.get("sent", []) if (_d(s.get("on")) or date.min) >= cutoff]
    sent.append({"on": today.isoformat(), "kind": reminder.kind, "key": reminder.key})
    if reminder.kind == URGENT and reminder.key.startswith("stuck:"):
        # One record per item, so a later alert only names what is new.
        for part in reminder.key[len("stuck:"):].split(","):
            sent.append({"on": today.isoformat(), "kind": URGENT, "key": f"stuck:{part}"})
    new["sent"] = sent
    if reminder.kind != URGENT:
        new["ignored"] = int(new.get("ignored") or 0) + 1
    return new


def record_engaged(state: dict, today: date) -> dict:
    """The doctor used /health or tapped a reminder: they are listening."""
    return {**empty_state(), **state, "ignored": 0, "engaged_on": today.isoformat()}


def is_first_reminder(state: dict) -> bool:
    return not state.get("sent")


# ── CESR window alert ───────────────────────────────────────────────────────


def cesr_expiring_count(items, today: date) -> int:
    """Signed-off items that leave the six-year window within 30 days."""
    from pathway_checklist import CESR_WINDOW_YEARS, _years_before, is_completed

    window_start = _years_before(today, CESR_WINDOW_YEARS)
    cutoff = window_start + timedelta(days=CESR_EXPIRY_ALERT_DAYS)
    return sum(
        1 for i in items if is_completed(i) and window_start <= i.event_date < cutoff
    )


# ── Flat-file store ─────────────────────────────────────────────────────────


def _store_path() -> Path:
    return Path(
        os.environ.get(
            "PORTFOLIO_GURU_PROACTIVE_PATH", str(data_path("proactive_reminders.json"))
        )
    )


def _load_all() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_all(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_state(user_id: int | str) -> dict[str, Any]:
    return {**empty_state(), **(_load_all().get(str(user_id)) or {})}


def save_state(user_id: int | str, state: dict[str, Any]) -> None:
    data = _load_all()
    data[str(user_id)] = state
    _write_all(data)


def delete_state(user_id: int | str) -> None:
    data = _load_all()
    if data.pop(str(user_id), None) is not None:
        _write_all(data)
