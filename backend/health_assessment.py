"""Portfolio Health assessment — the deterministic reading behind /health.

The old `/health` scored presence: at least five of six domains holding at
least one item earned Green. That cannot distinguish a balanced portfolio from
one with 250 clinical items and 7 QI items, and it cannot see evidence that was
filed but never signed off. On a real portfolio scanned 2026-08-26 it reported
"Green — main evidence domains are covered" while 27 items sat unfinished, the
oldest for 1112 days.

This module computes what a doctor would actually act on:

- **Stuck evidence**, split by who can move it. Only the assessor can complete
  an item that sits with them; an unfinished draft is the doctor's own. Those
  are different situations, so they are never pooled into one number.
- **Balance against the portfolio's own baseline**, not an absolute target.
  Requirements differ by stage and pathway, and this layer is deliberately
  pathway-agnostic, so "thin" means thin *relative to what this doctor has
  filed* — a claim the evidence supports without importing curriculum rules —
  and is not computed at all below ``IMBALANCE_MIN_ITEMS``.
- **Staleness**, per domain, so a domain that was covered years ago and left
  alone does not read as covered today.

There is no overall score. A green/amber/red light is a readiness claim, and
nothing here verifies readiness against a pathway's own rules — the same
evidence means different things to an ST4, a CESR applicant and an SAS doctor.
The findings are stated as facts about the scanned evidence and the views
render them directly.

Pure: no I/O, no network, no clock beyond the injectable ``today``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from form_labels import form_label
from health_models import CORE_DOMAINS, EvidenceItem, HealthDomain

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

# Below this many scanned core-domain items, a comparison between domains says
# more about the size of the sample than about the portfolio: with 12 items,
# one domain holding 1 and another 6 is noise. The minimum is deliberately a
# single explicit number rather than a derived threshold, so the rule can be
# stated to the doctor in one line.
IMBALANCE_MIN_ITEMS = 20

# A covered domain whose newest evidence is older than this is not current.
STALE_AFTER_DAYS = 365 * 3

RECENT_WINDOW_DAYS = 365


@dataclass(frozen=True)
class StuckEvidence:
    """One piece of evidence that has not completed its Kaizen workflow."""

    id: str
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
    recent_count: int = 0

    @property
    def is_empty(self) -> bool:
        return self.count == 0


@dataclass(frozen=True)
class HealthAssessment:
    """Everything the views render, computed once, deterministically."""

    stuck_awaiting: list[StuckEvidence] = field(default_factory=list)
    stuck_drafts: list[StuckEvidence] = field(default_factory=list)
    domains: list[DomainStat] = field(default_factory=list)
    total_items: int = 0
    items_last_year: int = 0
    newest_evidence: Optional[date] = None
    next_actions: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    slo_counts: dict[int, int] = field(default_factory=dict)
    tagged_items: int = 0
    untagged_items: int = 0
    untagged_by_form: dict[str, int] = field(default_factory=dict)
    # Evidence that the classifier could not place in one of the six universal
    # Health categories. It remains in the scan denominator and is disclosed in
    # Coverage rather than disappearing from the report.
    outside_core_items: int = 0
    # False when the portfolio is too small for a domain comparison to mean
    # anything. Coverage renders the reason instead of the comparison.
    balance_is_comparable: bool = False

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
        id=item.id,
        title=item.title,
        form_type=item.form_type,
        event_date=item.event_date,
        days_waiting=days,
        waits_on_others=state == AWAITING_OTHERS,
        url=item.source_ref,
    )


def _domain_stats(items: list[EvidenceItem], today: date) -> list[DomainStat]:
    total = sum(1 for item in items if item.domain in CORE_DOMAINS)
    comparable = total >= IMBALANCE_MIN_ITEMS
    stats: list[DomainStat] = []
    for domain in CORE_DOMAINS:
        in_domain = [item for item in items if item.domain == domain]
        count = len(in_domain)
        newest = max((item.event_date for item in in_domain), default=None)
        thin = bool(
            comparable
            and count
            and count < THIN_MAX_ITEMS
            and count / total < THIN_MAX_SHARE
        )
        stale = bool(newest and (today - newest).days > STALE_AFTER_DAYS)
        recent = sum(
            1 for item in in_domain if (today - item.event_date).days <= RECENT_WINDOW_DAYS
        )
        stats.append(
            DomainStat(
                domain=domain,
                count=count,
                newest=newest,
                is_thin=thin,
                is_stale=stale,
                recent_count=recent,
            )
        )
    return stats


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def compute_health_assessment(
    items: list[EvidenceItem], *, today: Optional[date] = None
) -> HealthAssessment:
    """Assess a portfolio from its evidence. Pure and explainable."""
    reference = today or date.today()
    if not items:
        return HealthAssessment(
            next_actions=["No portfolio evidence has been scanned yet"],
        )

    stats = _domain_stats(items, reference)
    stuck = [s for s in (_stuck_from(item, reference) for item in items) if s]
    # Total order, not just a sort by age: two items filed on the same day must
    # land in the same position on every render, or a doctor paging through
    # Actions sees items move between pages.
    stuck.sort(key=lambda s: (-s.days_waiting, s.form_type or "", s.id))
    awaiting = [s for s in stuck if s.waits_on_others]
    drafts = [s for s in stuck if not s.waits_on_others]

    core_total = sum(stat.count for stat in stats)
    recent = sum(
        1 for item in items if (reference - item.event_date).days <= RECENT_WINDOW_DAYS
    )
    newest = max((item.event_date for item in items), default=None)

    slo_counts: dict[int, int] = {}
    for item in items:
        for slo in getattr(item, "slo_numbers", None) or []:
            slo_counts[slo] = slo_counts.get(slo, 0) + 1
    untagged_forms = _untagged_by_form(items)
    untagged = sum(untagged_forms.values())
    tagged = sum(1 for item in items if getattr(item, "slo_numbers", None))

    return HealthAssessment(
        stuck_awaiting=awaiting,
        stuck_drafts=drafts,
        domains=stats,
        total_items=core_total,
        items_last_year=recent,
        newest_evidence=newest,
        next_actions=_next_actions(awaiting, drafts, stats),
        patterns=_patterns(awaiting, drafts, stats, items),
        slo_counts=slo_counts,
        tagged_items=tagged,
        untagged_items=untagged,
        untagged_by_form=untagged_forms,
        outside_core_items=sum(1 for item in items if item.domain not in CORE_DOMAINS),
        balance_is_comparable=core_total >= IMBALANCE_MIN_ITEMS,
    )


# Three stuck items of the same type is not three incidents, it is one habit —
# but only if that form type gets stuck more often than the rest. Six unfinished
# CBDs in a portfolio of 250 clinical items is proportionate, not a finding.
REPEAT_PATTERN_MIN = 3
REPEAT_PATTERN_RATE_MULTIPLE = 2.0


def _untagged_by_form(items: list[EvidenceItem]) -> dict[str, int]:
    """Which forms the untagged evidence actually is.

    A count on its own tells a doctor there is a problem but not where to go.
    Naming the forms turns "158 items are untagged" into "your reflective logs
    are untagged", which is somewhere to start.

    Much evidence cannot carry curriculum tags at all — MSF, e-learning, exams,
    document uploads — and counting those as untagged turns a structural fact
    into an alarming number: on a real portfolio that was ~100 of 247. A form
    type counts as taggable here only because this doctor has tagged one of
    them before.
    """
    taggable_forms = {
        item.form_type
        for item in items
        if item.form_type and (getattr(item, "slo_numbers", None) or [])
    }
    counts: dict[str, int] = {}
    for item in items:
        if item.form_type in taggable_forms and not (getattr(item, "slo_numbers", None) or []):
            counts[item.form_type] = counts.get(item.form_type, 0) + 1
    return counts


def _patterns(
    awaiting: list[StuckEvidence],
    drafts: list[StuckEvidence],
    stats: list[DomainStat],
    items: list[EvidenceItem],
) -> list[str]:
    """Findings that only appear when you look across items rather than at them.

    A list of facts is not a diagnosis. Three Teaching Observations stuck for
    1112, 937 and 798 days is not bad luck three times; it says teaching
    observations do not get signed off. And a domain can read as thin partly
    because its evidence is sitting unsigned, which is a different problem from
    having filed nothing — so the finding names which one it is and leaves the
    decision to the doctor.
    """
    found: list[str] = []

    stuck_all = awaiting + drafts
    total = len(items)
    overall_rate = len(stuck_all) / total if total else 0

    filed_by_form: dict[str, int] = {}
    for item in items:
        if item.form_type:
            filed_by_form[item.form_type] = filed_by_form.get(item.form_type, 0) + 1
    stuck_by_form: dict[str, int] = {}
    for stuck in stuck_all:
        if stuck.form_type:
            stuck_by_form[stuck.form_type] = stuck_by_form.get(stuck.form_type, 0) + 1

    # Ranked by how often the form gets stuck, not by how many are stuck:
    # 3 of 3 is a finding, 6 of 43 is a cluster. State the ratio and let the
    # reader draw the conclusion rather than editorialising over their data.
    ranked = sorted(
        (
            (form_type, count, filed_by_form.get(form_type, count))
            for form_type, count in stuck_by_form.items()
        ),
        key=lambda row: -(row[1] / row[2] if row[2] else 0),
    )
    for form_type, count, filed in ranked:
        rate = count / filed if filed else 0
        if count >= REPEAT_PATTERN_MIN and rate >= overall_rate * REPEAT_PATTERN_RATE_MULTIPLE:
            found.append(
                f"{form_label(form_type)}: {count} of your {filed} are unfinished"
            )

    blocked_ids = {stuck.id for stuck in stuck_all}
    for stat in stats:
        if not (stat.is_thin or stat.is_empty):
            continue
        stuck_here = sum(
            1 for item in items if item.domain == stat.domain and item.id in blocked_ids
        )
        if stuck_here:
            found.append(
                f"{DOMAIN_LABELS[stat.domain]} looks small partly because "
                f"{stuck_here} of its items are unfinished rather than missing"
            )
    return found[:2]


def _next_actions(
    awaiting: list[StuckEvidence],
    drafts: list[StuckEvidence],
    stats: list[DomainStat],
) -> list[str]:
    """Findings derived from this portfolio, never generic filler.

    The old report suggested "File a CBD from a recent supervised case" to a
    doctor with 250 clinical items, because the suggestions were fallback
    strings rather than a reading of the evidence.

    They are stated as findings with exact dates rather than as instructions.
    Nothing here knows whether a 2023 draft is still worth completing, whether
    an assessor has already been asked, or whether any deadline exists, so
    "chase this" and "overdue" would be claims the evidence cannot support.
    Relative domain size is deliberately absent: it belongs in Coverage, where
    it can be qualified as a comparison with the doctor's own portfolio.
    """
    actions: list[str] = []

    if drafts:
        oldest = drafts[0]
        actions.append(
            f"{_plural(len(drafts), 'draft')} of your own unfinished — oldest a "
            f"{form_label(oldest.form_type, 'form')} dated "
            f"{oldest.event_date.strftime('%-d %b %Y')}"
        )
    if awaiting:
        oldest = awaiting[0]
        actions.append(
            f"{_plural(len(awaiting), 'item')} with someone else — oldest a "
            f"{form_label(oldest.form_type, 'form')} dated "
            f"{oldest.event_date.strftime('%-d %b %Y')}"
        )

    for stat in stats:
        if stat.is_empty:
            actions.append(f"No {DOMAIN_LABELS[stat.domain]} evidence in this scan")
    for stat in stats:
        if stat.is_stale and stat.newest:
            actions.append(
                f"{DOMAIN_LABELS[stat.domain]} evidence stops at "
                f"{stat.newest.strftime('%b %Y')}"
            )

    if not actions:
        actions.append("Nothing in this scan is unfinished")
    return actions[:5]
