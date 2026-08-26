"""Portfolio Health assessment — the deterministic reading behind /health.

The old `/health` scored presence: at least five of six domains holding at
least one item earned Green. That cannot distinguish a balanced portfolio from
one with 250 clinical items and 7 QI items, and it cannot see evidence that was
filed but never signed off. On a real portfolio scanned 2026-08-26 it reported
"Green — main evidence domains are covered" while 27 items sat unfinished, the
oldest for 1112 days.

This module computes what a doctor would actually act on:

- **Stuck evidence**, split by who can move it. An item waiting on an assessor
  needs chasing; an unfinished draft is theirs to close. Those are different
  actions, so they are never pooled into one number.
- **Balance against the portfolio's own baseline**, not an absolute target.
  Requirements differ by stage and pathway, and this layer is deliberately
  pathway-agnostic, so "thin" means thin *relative to what this doctor has
  filed* — a claim the evidence supports without importing curriculum rules.
- **Staleness**, per domain, so a domain that was covered years ago and left
  alone does not read as covered today.

The score stays coarse and the reasons stay precise. Every assessment carries
the concrete reasons for its verdict; the spec's rule is that a traffic light
never appears alone, and the reasons — not the colour — are what tell a doctor
what to do.

Pure: no I/O, no network, no clock beyond the injectable ``today``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from form_labels import form_label
from health_models import CORE_DOMAINS, EvidenceItem, HealthDomain, HealthScore

# Kaizen workflow states that mean the item has not finished its journey.
AWAITING_OTHERS = "pending"
OWN_DRAFT = "draft"
BLOCKED_STATES = frozenset({AWAITING_OTHERS, OWN_DRAFT})

# Below this, an unsigned item is a normal turnaround rather than a problem.
STUCK_AFTER_DAYS = 21

# A domain is thin when it is both small in absolute terms and a small share of
# the portfolio. Both conditions matter: 7 items is thin next to 500, but not
# next to 30, and a new portfolio should not be scolded for being small.
THIN_MAX_ITEMS = 10
THIN_MAX_SHARE = 0.05

# A covered domain whose newest evidence is older than this is not current.
STALE_AFTER_DAYS = 365 * 3

RECENT_WINDOW_DAYS = 365


@dataclass(frozen=True)
class StuckEvidence:
    """One piece of evidence that has not completed its Kaizen workflow."""

    title: str
    form_type: Optional[str]
    event_date: date
    days_waiting: int
    waits_on_others: bool
    url: Optional[str]


@dataclass(frozen=True)
class DomainStat:
    """One core domain's standing within the portfolio."""

    domain: HealthDomain
    count: int
    newest: Optional[date]
    is_thin: bool
    is_stale: bool

    @property
    def is_empty(self) -> bool:
        return self.count == 0


@dataclass(frozen=True)
class HealthAssessment:
    """Everything the report renders, computed once, deterministically."""

    score: HealthScore
    reasons: list[str] = field(default_factory=list)
    stuck_awaiting: list[StuckEvidence] = field(default_factory=list)
    stuck_drafts: list[StuckEvidence] = field(default_factory=list)
    domains: list[DomainStat] = field(default_factory=list)
    total_items: int = 0
    items_last_year: int = 0
    newest_evidence: Optional[date] = None
    next_actions: list[str] = field(default_factory=list)

    @property
    def stuck_total(self) -> int:
        return len(self.stuck_awaiting) + len(self.stuck_drafts)


DOMAIN_LABELS: dict[HealthDomain, str] = {
    HealthDomain.clinical: "Clinical",
    HealthDomain.cpd: "CPD",
    HealthDomain.qi: "QI",
    HealthDomain.teaching: "Teaching",
    HealthDomain.leadership: "Leadership",
    HealthDomain.reflection: "Reflection",
}


def _stuck_from(item: EvidenceItem, today: date) -> Optional[StuckEvidence]:
    state = (item.workflow_state or "").strip().lower()
    if state not in BLOCKED_STATES:
        return None
    days = (today - item.event_date).days
    if days < STUCK_AFTER_DAYS:
        return None
    return StuckEvidence(
        title=item.title,
        form_type=item.form_type,
        event_date=item.event_date,
        days_waiting=days,
        waits_on_others=state == AWAITING_OTHERS,
        url=item.source_ref,
    )


def _domain_stats(items: list[EvidenceItem], today: date) -> list[DomainStat]:
    total = sum(1 for item in items if item.domain in CORE_DOMAINS)
    stats: list[DomainStat] = []
    for domain in CORE_DOMAINS:
        in_domain = [item for item in items if item.domain == domain]
        count = len(in_domain)
        newest = max((item.event_date for item in in_domain), default=None)
        thin = bool(
            count and count < THIN_MAX_ITEMS and (not total or count / total < THIN_MAX_SHARE)
        )
        stale = bool(newest and (today - newest).days > STALE_AFTER_DAYS)
        stats.append(
            DomainStat(domain=domain, count=count, newest=newest, is_thin=thin, is_stale=stale)
        )
    return stats


def _presence_score(stats: list[DomainStat]) -> HealthScore:
    covered = sum(1 for stat in stats if stat.count > 0)
    if covered >= 5:
        return HealthScore.green
    if covered >= 3:
        return HealthScore.amber
    if covered >= 1:
        return HealthScore.red
    return HealthScore.grey


def _demote(score: HealthScore) -> HealthScore:
    if score == HealthScore.green:
        return HealthScore.amber
    if score == HealthScore.amber:
        return HealthScore.red
    return score


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def compute_health_assessment(
    items: list[EvidenceItem], *, today: Optional[date] = None
) -> HealthAssessment:
    """Assess a portfolio from its evidence. Pure and explainable."""
    reference = today or date.today()
    if not items:
        return HealthAssessment(
            score=HealthScore.grey,
            reasons=["No portfolio evidence has been scanned yet"],
            next_actions=["Connect Kaizen and run a scan to see your portfolio"],
        )

    stats = _domain_stats(items, reference)
    stuck = [s for s in (_stuck_from(item, reference) for item in items) if s]
    stuck.sort(key=lambda s: s.days_waiting, reverse=True)
    awaiting = [s for s in stuck if s.waits_on_others]
    drafts = [s for s in stuck if not s.waits_on_others]

    core_total = sum(stat.count for stat in stats)
    recent = sum(
        1 for item in items if (reference - item.event_date).days <= RECENT_WINDOW_DAYS
    )
    newest = max((item.event_date for item in items), default=None)

    reasons: list[str] = []
    for stat in stats:
        if stat.is_empty:
            reasons.append(f"No {DOMAIN_LABELS[stat.domain]} evidence")
    if awaiting:
        reasons.append(
            f"{_plural(len(awaiting), 'item')} waiting on someone else, "
            f"oldest {awaiting[0].days_waiting} days"
        )
    if drafts:
        reasons.append(
            f"{_plural(len(drafts), 'draft')} of your own unfinished, "
            f"oldest {drafts[0].days_waiting} days"
        )
    biggest = max((stat.count for stat in stats), default=0)
    for stat in stats:
        if stat.is_thin:
            reasons.append(
                f"{DOMAIN_LABELS[stat.domain]} is thin: {stat.count} against {biggest} at your strongest"
            )
        elif stat.is_stale and stat.newest:
            reasons.append(
                f"{DOMAIN_LABELS[stat.domain]} evidence stops at {stat.newest.strftime('%b %Y')}"
            )

    score = _presence_score(stats)
    # One demotion, however many concerns fire. The colour is a coarse signal;
    # stacking demotions would drive every large portfolio to red and teach
    # doctors to ignore it. The reasons above carry the detail.
    if stuck or any(stat.is_thin or stat.is_stale for stat in stats):
        score = _demote(score)

    if not reasons:
        reasons.append("Every core domain is covered, current, and nothing is waiting")

    return HealthAssessment(
        score=score,
        reasons=reasons,
        stuck_awaiting=awaiting,
        stuck_drafts=drafts,
        domains=stats,
        total_items=core_total,
        items_last_year=recent,
        newest_evidence=newest,
        next_actions=_next_actions(awaiting, drafts, stats),
    )


def _next_actions(
    awaiting: list[StuckEvidence],
    drafts: list[StuckEvidence],
    stats: list[DomainStat],
) -> list[str]:
    """Actions derived from this portfolio, never generic filler.

    The old report suggested "File a CBD from a recent supervised case" to a
    doctor with 250 clinical items, because the suggestions were fallback
    strings rather than a reading of the evidence.
    """
    actions: list[str] = []

    if awaiting:
        oldest = awaiting[0]
        actions.append(
            f"Chase sign-off on your {form_label(oldest.form_type, 'oldest item')} from "
            f"{oldest.event_date.strftime('%-d %b %Y')} — waiting {oldest.days_waiting} days"
        )
    if drafts:
        oldest = drafts[0]
        actions.append(
            f"Finish or delete your {form_label(oldest.form_type, 'draft')} from "
            f"{oldest.event_date.strftime('%-d %b %Y')} — unfinished for {oldest.days_waiting} days"
        )

    for stat in stats:
        if stat.is_empty:
            actions.append(f"Add {DOMAIN_LABELS[stat.domain]} evidence — you have none")
    for stat in stats:
        if stat.is_thin:
            actions.append(
                f"Add {DOMAIN_LABELS[stat.domain]} evidence — only {stat.count} items"
            )
        elif stat.is_stale and stat.newest:
            actions.append(
                f"Refresh {DOMAIN_LABELS[stat.domain]} — nothing since "
                f"{stat.newest.strftime('%b %Y')}"
            )

    if not actions:
        actions.append("Nothing is outstanding — keep filing as you go")
    return actions[:5]
