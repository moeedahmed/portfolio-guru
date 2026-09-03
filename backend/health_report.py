"""Render Portfolio Health views for Telegram.

Separated from the assessment so the reading and the wording can be tested
apart, and so a future web dashboard renders the same numbers differently
without reimplementing the logic.

The everyday journey has three views, each with one job:

- **What to do next** — draft and awaiting counts, with doctor-controlled work
  first.
- **Draft/Awaiting queues** — five per page, each item linked to Kaizen.
- **About** — only the provenance and limits needed to trust the report.

Actions, Coverage, Curriculum and Scan info remain renderable for buttons in
older Telegram messages, but are not routes in the everyday journey.

Three rules shape all of them:

- **Nothing claims more than the scan saw.** No overall verdict, no colour, no
  readiness light. Health reads evidence workflow and dates; it does not know
  any pathway's requirements, so it cannot say whether a portfolio is on track.
  Pathway-specific counters appear only where a verified overlay supplies them.
- **Nothing instructs an action the data cannot justify.** Old evidence is
  described with its exact date and offered for review. "Overdue", "stale" and
  "chase this" all assert a deadline that no scanned field contains.
- **Every denominator is explicit.** Coverage states what sits outside the six
  categories; curriculum spread covers tagged items only and says what it
  excludes.

Clinical narrative never appears. Items are named by form type and date only —
descriptions hold patient detail.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from form_labels import form_label
from health_assessment import (
    DOMAIN_LABELS,
    HealthAssessment,
    StuckEvidence,
)

# One phone screen of items per Actions page.
ACTIONS_PAGE_SIZE = 5

SAFETY_LINE = (
    "_Read-only planning aid, not a formal training or appraisal judgement._"
)


def _describe(item: StuckEvidence, *, link: bool = True) -> str:
    """One unfinished item: what it is, when it is dated, where it lives.

    The date is the finding. A duration ("waiting 1112 days") reads as a breach
    of something, and nothing in Kaizen says what an acceptable wait is.
    """
    # Doctors recognise "Teaching Observation", not "TEACH_OBS".
    name = form_label(item.form_type, fallback=None) if item.form_type else item.title
    label = f"{name} — {item.event_date.strftime('%-d %b %Y')}"
    # Naming an item from 2023 and leaving the doctor to find it is half a
    # feature. The URL is already indexed for every item.
    if link and item.url:
        safe = label.replace("[", "(").replace("]", ")")
        return f"• [{safe}]({item.url})"
    return f"• {label}"


# ── Actions ─────────────────────────────────────────────────────────────────


def ordered_actions(assessment: HealthAssessment) -> list[tuple[str, StuckEvidence]]:
    """Every older unfinished item selected by the assessment, in stable order.

    Doctor-controlled drafts come first, then items awaiting somebody else;
    within each group the assessment's own total order (oldest first, then form
    type, then id) already breaks every tie. The order must not depend on dict
    iteration or on anything that changes between two renders of the same
    stored report, or page 2 will show an item page 1 already showed.
    """
    return [("draft", item) for item in assessment.stuck_drafts] + [
        ("awaiting", item) for item in assessment.stuck_awaiting
    ]


def actions_page_count(
    assessment: HealthAssessment, *, page_size: int = ACTIONS_PAGE_SIZE
) -> int:
    total = len(ordered_actions(assessment))
    if total <= 0:
        return 1
    return (total + page_size - 1) // page_size


GROUP_HEADINGS = {
    "draft": (
        "Older drafts",
        "Started by you, still incomplete, and highlighted after waiting.",
    ),
    "awaiting": (
        "Older items awaiting sign-off",
        "Submitted, waiting for someone else, and highlighted after waiting.",
    ),
}

QUEUE_LABELS = {
    "draft": "older drafts",
    "awaiting": "older items awaiting sign-off",
}

# Entries older than this are offered for review rather than listed as work
# still to do — a draft from three years ago is as likely to be abandoned as
# outstanding, and only the doctor knows which.
REVIEW_AFTER_DAYS = 365


def _queue_items(
    assessment: HealthAssessment, queue: str
) -> list[StuckEvidence]:
    if queue == "draft":
        return assessment.stuck_drafts
    if queue == "awaiting":
        return assessment.stuck_awaiting
    raise ValueError(f"Unknown Health action queue: {queue}")


def action_queue_page_count(
    assessment: HealthAssessment,
    queue: str,
    *,
    page_size: int = ACTIONS_PAGE_SIZE,
) -> int:
    total = len(_queue_items(assessment, queue))
    return max(1, (total + page_size - 1) // page_size)


def _old_item_note(items: list[StuckEvidence]) -> Optional[str]:
    if not any(item.days_waiting > REVIEW_AFTER_DAYS for item in items):
        return None
    return (
        "_Entries dated more than a year ago are worth reviewing before "
        "acting: some will no longer be worth completing._"
    )


def format_action_queue(
    assessment: HealthAssessment,
    queue: str,
    *,
    page: int = 0,
    page_size: int = ACTIONS_PAGE_SIZE,
) -> str:
    """One independently paginated queue of older unfinished evidence."""
    items = _queue_items(assessment, queue)
    heading, note = GROUP_HEADINGS[queue]
    label = QUEUE_LABELS[queue]
    if not items:
        return (
            f"📌 *{heading}*\n\n"
            f"No {label} were highlighted in this scan."
        )

    pages = action_queue_page_count(assessment, queue, page_size=page_size)
    page = max(0, min(page, pages - 1))
    start = page * page_size
    window = items[start:start + page_size]

    lines = [
        f"📌 *{heading} — {len(items)}*",
        f"Showing {start + 1}–{start + len(window)} of {len(items)} {label}",
        f"_{note}_",
        "",
    ]
    lines.extend(_describe(item) for item in window)
    lines.append("")

    old_note = _old_item_note(window)
    if old_note:
        lines.append(old_note)
    lines.append("_Nothing is chased, submitted, edited or deleted for you._")
    return "\n".join(lines).strip()


def _format_legacy_actions_page(
    assessment: HealthAssessment,
    *,
    page: int,
    page_size: int,
) -> str:
    """Combined pages kept only for callbacks on older Health messages."""
    items = ordered_actions(assessment)
    if not items:
        return (
            "📌 *Actions*\n\n"
            "No older unfinished items were highlighted in this scan."
        )
    pages = actions_page_count(assessment, page_size=page_size)
    page = max(0, min(page, pages - 1))
    start = page * page_size
    window = items[start:start + page_size]
    lines = [
        "📌 *Actions*",
        f"Showing {start + 1}–{start + len(window)} of {len(items)} older unfinished items",
        "",
    ]
    current_group: Optional[str] = None
    for group, item in window:
        if group != current_group:
            current_group = group
            heading, note = GROUP_HEADINGS[group]
            total_in_group = sum(1 for item_group, _ in items if item_group == group)
            lines.extend([f"*{heading} — {total_in_group}*", f"_{note}_"])
        lines.append(_describe(item))
    lines.append("")
    old_note = _old_item_note([item for _group, item in window])
    if old_note:
        lines.append(old_note)
    lines.append("_Nothing is chased, submitted, edited or deleted for you._")
    return "\n".join(lines).strip()


def format_actions(
    assessment: HealthAssessment,
    *,
    page: Optional[int] = None,
    page_size: int = ACTIONS_PAGE_SIZE,
) -> str:
    """Agency-first Actions landing; ``page`` preserves legacy callbacks."""
    if page is not None:
        return _format_legacy_actions_page(
            assessment, page=page, page_size=page_size
        )
    if not assessment.stuck_total:
        return (
            "📌 *Actions*\n\n"
            "No older unfinished items were highlighted in this scan."
        )

    lines = ["📌 *Actions*", ""]
    shown: list[StuckEvidence] = []
    for queue in ("draft", "awaiting"):
        items = _queue_items(assessment, queue)
        heading, note = GROUP_HEADINGS[queue]
        lines.extend([f"*{heading} — {len(items)}*", f"_{note}_"])
        if items:
            examples = items[:3]
            shown.extend(examples)
            lines.extend(_describe(item) for item in examples)
            if len(items) > len(examples):
                lines.append(f"_{len(items) - len(examples)} more in this queue._")
        else:
            lines.append("• None highlighted in this scan")
        lines.append("")

    old_note = _old_item_note(shown)
    if old_note:
        lines.extend([old_note, ""])
    lines.append("_Nothing is chased, submitted, edited or deleted for you._")
    return "\n".join(lines).strip()


# ── Action-first landing ────────────────────────────────────────────────────


def _pathway_counter(readiness: Optional[dict]) -> list[str]:
    """A pathway overlay's own requirement counter, clearly labelled as one.

    Portfolio Health is pathway-agnostic — it reads evidence, not curricula.
    Anything depending on a pathway's own published rules belongs here, named
    as that pathway's requirement. With no overlay this renders nothing: an
    empty counter would read as an unmet requirement for doctors whose pathway
    has no such rule at all.
    """
    if not readiness or readiness.get("pathway") != "cesr_portfolio":
        return []
    target = readiness.get("wpba_target", 36)
    count = readiness.get("wpba_count", 0)
    lines = [
        "*Portfolio Pathway requirement*",
        f"{count}/{target} WPBAs counted in this scan",
    ]
    breakdown = readiness.get("wpba_breakdown") or {}
    if breakdown:
        lines.append(
            " · ".join(
                f"{label} {breakdown.get(key, 0)}/12"
                for key, label in (("dops", "DOPS"), ("mini_cex", "Mini-CEX"), ("cbd", "CBD"))
            )
        )
    lines.append("_RCEM's stated minimum. Confirm against current guidance._")
    return lines + [""]


def _scan_notice(
    *, limited_view: bool, partial_scan: bool, scan_is_fresh: bool
) -> Optional[str]:
    """One factual line when the reading cannot claim to be complete."""
    if limited_view:
        return (
            "_Partial scan: Portfolio Guru filings only, so this does not "
            "cover your whole Kaizen portfolio._"
        )
    if partial_scan:
        return "_Partial scan: some Kaizen evidence may be missing._"
    if not scan_is_fresh:
        return (
            "_Scan freshness unconfirmed: recent Kaizen activity may be "
            "missing._"
        )
    return None


def _review_lines(
    review_date: Optional[date], today: date, *, has_button: bool = True
) -> list[str]:
    """Review timing, pointing at whichever route this view actually offers."""
    route = (
        "open ☰ More and choose Review month"
        if has_button
        else "set it with /arcp"
    )
    if not review_date:
        return [f"No review month set — {route} to time this to your cycle."]
    when = review_date.strftime("%B %Y")
    days = (review_date - today).days
    if days < 0:
        return [f"Review month {when} has passed — {route} to set the next one."]
    weeks = days // 7
    countdown = f"{weeks} weeks" if weeks >= 2 else f"{days} days"
    return [f"Next review: {when} — {countdown} away."]


def format_priorities(
    assessment: HealthAssessment,
    *,
    month_label: str,
    review_date: Optional[date] = None,
    today: Optional[date] = None,
    limited_view: bool = False,
    partial_scan: bool = False,
    scan_is_fresh: bool = True,
    pathway_readiness: Optional[dict] = None,
) -> str:
    """The compact action-first landing; the legacy signature stays callable."""
    draft_total = len(assessment.stuck_drafts)
    awaiting_total = len(assessment.stuck_awaiting)
    lines = ["*What to do next*", ""]
    if draft_total:
        lines.extend([
            f"*Review older drafts — {draft_total}*",
            "These have been unfinished long enough to be worth reviewing. Decide whether each is still worth completing.",
        ])
    elif awaiting_total:
        lines.extend([
            "*No older drafts to review*",
            "No drafts have been waiting long enough to be highlighted here.",
        ])
    else:
        lines.extend([
            "*No older unfinished items to review*",
            "No drafts or awaiting-sign-off items have been waiting long enough to be highlighted here.",
        ])

    if awaiting_total:
        lines.extend([
            "",
            f"*Older items awaiting sign-off — {awaiting_total}*",
            "Review only if follow-up is still needed.",
        ])
    lines.append("")

    notice = _scan_notice(
        limited_view=limited_view,
        partial_scan=partial_scan,
        scan_is_fresh=scan_is_fresh,
    )
    if notice:
        lines.extend([notice, ""])
    lines.append(SAFETY_LINE)
    return "\n".join(lines).strip()


# ── About ───────────────────────────────────────────────────────────────────


def format_about(
    *,
    basis: str,
    limited_view: bool = False,
    scan_is_fresh: bool = True,
) -> str:
    """Concise provenance and limits for the everyday Health journey."""
    facts = [
        line
        for line in basis.strip().splitlines()
        if line.startswith(("Scanned:", "Refresh:", "Scope:"))
    ]
    joined = " ".join(facts).lower()
    if limited_view and "partial" not in joined:
        facts.append("Scope: partial — the full Kaizen index was unavailable")
        joined = " ".join(facts).lower()
    if not scan_is_fresh and not any(
        marker in joined
        for marker in (
            "freshness unconfirmed",
            "older than",
            "may be older",
            "may be missing",
            "partial",
            "failed",
            "did not complete",
            "still running",
            "reconnection",
        )
    ):
        facts.append(
            "Freshness unconfirmed: recent Kaizen activity may be missing."
        )

    lines = ["ℹ️ *About Portfolio Health*", ""]
    lines.extend(facts or ["Source: no scan provenance is available for this report."])
    lines.extend([
        "",
        "Counts highlight older Kaizen workflow items visible to this scan, not every unfinished item.",
        "Automated classification can be wrong; check the linked Kaizen item if something looks wrong.",
        "Portfolio Health does not edit, file, chase or delete anything.",
        "",
        SAFETY_LINE,
    ])
    return "\n".join(lines).strip()


# ── Coverage ────────────────────────────────────────────────────────────────


def format_coverage(
    assessment: HealthAssessment, *, today: Optional[date] = None
) -> str:
    """What the portfolio holds — with every comparison named as a comparison."""
    reference = today or date.today()
    lines = ["📊 *Coverage*", "", "*Domains — total · last 12 months*"]
    for stat in sorted(assessment.domains, key=lambda s: s.count, reverse=True):
        label = DOMAIN_LABELS[stat.domain]
        if not stat.count:
            lines.append(f"• {label}: none scanned")
            continue
        age_days = (reference - stat.newest).days if stat.newest else None
        recency = ""
        if stat.newest is not None and age_days is not None and age_days > 365:
            recency = f" · latest {stat.newest.strftime('%b %Y')}"
        # A total says how much exists; the recent count says whether the
        # domain is still alive. 250 items built years ago is not the same
        # portfolio as 250 with 58 this year.
        lines.append(
            f"• {label}: {stat.count} · {stat.recent_count}{recency}"
        )

    lines.append("")
    scanned_total = assessment.total_items + assessment.outside_core_items
    lines.append(
        f"{assessment.outside_core_items} of {scanned_total} scanned items sit "
        "outside these six core categories; they remain in the scan but are "
        "not assigned to a category above."
    )
    lines.append("")

    represented = len(assessment.slo_counts)
    lines.append("*Curriculum tag scope*")
    lines.append(
        f"{represented}/12 SLOs represented across "
        f"{assessment.tagged_items} tagged item(s). Presence does not assess adequacy."
    )
    lines.append(
        f"{assessment.untagged_items} usually-taggable item(s) are untagged and "
        "excluded from the SLO spread; evidence types that cannot carry tags "
        "are also outside it."
    )
    lines.append("_Open the curriculum detail for the tagged spread and limits._")
    lines.append("")

    lines.append(
        "_Nothing here is a curriculum requirement or a minimum. Check your "
        "own pathway's published expectations separately._"
    )
    return "\n".join(lines).strip()


def format_curriculum(assessment: HealthAssessment) -> str:
    """Tagged curriculum spread as an optional Coverage drill-down."""
    lines = ["🏷️ *Curriculum tags*", ""]
    block = _curriculum_block(assessment)
    if block and block[0] == "*Curriculum tags*":
        block = block[1:]
    lines.extend(block)
    lines.append(
        "_This describes tagged evidence visible to the scan. It is not a "
        "curriculum requirement, minimum or ARCP outcome._"
    )
    return "\n".join(lines).strip()


def _curriculum_block(assessment: HealthAssessment) -> list[str]:
    """Curriculum spread, stated as counts over tagged items only.

    "12/12 SLOs covered" is technically true of a portfolio holding 298 tags
    against one outcome and 13 against another. The count is the finding — and
    so is how much of the portfolio the count could not see.
    """
    counts = assessment.slo_counts
    if not counts:
        return [
            "*Curriculum tags*",
            "0/12 SLOs represented across 0 tagged items. Presence does not "
            "assess adequacy.",
            f"_Untagged: {assessment.untagged_items} usually-taggable item(s) "
            "are excluded from this view. Evidence types that cannot carry "
            "tags are outside it._",
            "",
        ]
    ranked = sorted(counts.items(), key=lambda kv: kv[1])
    strongest_slo, strongest = ranked[-1]
    lines = [
        "*Curriculum tags*",
        f"{len(counts)}/12 SLOs represented across "
        f"{assessment.tagged_items} tagged item(s). Presence does not assess adequacy.",
    ]
    spread = f"Largest SLO{strongest_slo} ({strongest})"
    # With one tagged outcome the largest is also the smallest, and printing it
    # twice reads as two findings.
    smallest = [f"SLO{slo} ({count})" for slo, count in ranked[:-1][:3]]
    if smallest:
        spread += f" · smallest {' · '.join(smallest)}"
    lines.append(spread)
    if assessment.untagged_items:
        # Untagged items are invisible to this view. Saying so stops a doctor
        # reading a small SLO as a gap when the evidence may simply be
        # untagged. Name the forms: a count says there is a problem, the forms
        # say where to go and fix it.
        worst = sorted(assessment.untagged_by_form.items(), key=lambda kv: -kv[1])[:3]
        where = ", ".join(f"{form_label(form)} {count}" for form, count in worst)
        lines.append(
            f"_Untagged: {assessment.untagged_items} item(s) of a form you tag "
            f"elsewhere carry no tag — {where}. They may not count toward "
            "curriculum coverage and are excluded from this view._"
        )
    else:
        lines.append(
            "_Untagged: 0 items of a form you tag elsewhere are missing tags._"
        )
    return lines + [""]


# ── Scan info ───────────────────────────────────────────────────────────────


def _pathway_expectations(readiness: Optional[dict]) -> list[str]:
    if not readiness or readiness.get("pathway") != "cesr_portfolio":
        return []
    return [
        "*Portfolio Pathway expectations*",
        "Beyond WPBAs: structured consultant reports, ESLEs across core "
        "specialties, CPD with reflection, and QI work.",
        "_A 5-year evidence window is the usual expectation, and this is a "
        "multi-year build rather than an annual cycle — explain any gaps._",
        "",
    ]


def format_scan_info(
    assessment: HealthAssessment,
    *,
    basis: str,
    review_date: Optional[date] = None,
    today: Optional[date] = None,
    limited_view: bool = False,
    pathway_readiness: Optional[dict] = None,
) -> str:
    """Provenance and limits: where the reading came from, what it cannot see.

    ``basis`` is the caller's source/freshness/pathway/confidence block — only
    the bot knows the sync state — and this adds the timing and the caveats
    that would crowd out the action-first landing.
    """
    reference = today or date.today()
    lines = ["🔎 *Scan info*", ""]
    lines.extend(basis.strip().splitlines())
    lines.append("")

    lines.append("*Review timing*")
    lines.extend(_review_lines(review_date, reference, has_button=False))
    lines.append("")

    lines.extend(_pathway_expectations(pathway_readiness))
    lines.extend(_pathway_counter(pathway_readiness))

    lines.append("*What this cannot see*")
    lines.append(
        "• Only evidence visible to this scan is counted; anything not indexed "
        "is absent rather than missing."
    )
    lines.append(
        "• Health highlights older unfinished items from Kaizen workflow states; "
        "it does not show every unfinished item. The scan holds no deadline, so "
        "nothing here is described as overdue."
    )
    lines.append(
        "• Curriculum spread covers tagged items only; category and SLO counts "
        "are inventory, not a requirement."
    )
    lines.append(
        "• Automated classification is not certified; check the source item "
        "if a category looks wrong."
    )
    lines.append(
        "• Curriculum adequacy is not certified. SLO presence and tag counts "
        "do not assess the quality, sufficiency or currency of evidence."
    )
    if limited_view:
        lines.append(
            "• Limited view: based on Portfolio Guru filings only. A full "
            "Kaizen scan was not available."
        )
    lines.append("")
    lines.append(SAFETY_LINE)
    return "\n".join(lines).strip()
