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

import re
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
        "Started by you, not yet finished.",
    ),
    "awaiting": (
        "Awaiting sign-off",
        "Submitted, waiting for someone else to sign.",
    ),
}

QUEUE_EMOJI = {"draft": "📝", "awaiting": "⏳"}

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
    return "_Some are over a year old: check they're still needed._"


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
            f"{QUEUE_EMOJI[queue]} *{heading}*\n\n"
            f"No {label} were highlighted in this scan."
        )

    pages = action_queue_page_count(assessment, queue, page_size=page_size)
    page = max(0, min(page, pages - 1))
    start = page * page_size
    window = items[start:start + page_size]

    # The heading already carries the total, so the subtitle only adds the
    # page when there is more than one.
    subtitle = note if pages == 1 else f"{note} Page {page + 1} of {pages}."
    lines = [
        f"{QUEUE_EMOJI[queue]} *{heading} — {len(items)}*",
        subtitle,
        "",
    ]
    lines.extend(_describe(item) for item in window)

    old_note = _old_item_note(window)
    if old_note:
        lines.extend(["", old_note])
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
    lines.append("_Signed-off DOPS, Mini-CEX and CBD, capped at 12 each (GMC guidance)._")
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


# ── Pathway landings ────────────────────────────────────────────────────────

# One phone screen: three actions, each tied to a named requirement.
DO_NEXT_LIMIT = 3


def _weeks_until(target: date, today: date) -> str:
    days = (target - today).days
    weeks = days // 7
    return f"{weeks} weeks" if weeks >= 2 else f"{days} days"


def _chase_action(assessment: HealthAssessment) -> Optional[str]:
    """The oldest item waiting on someone else, as one action."""
    if not assessment.stuck_awaiting:
        return None
    oldest = min(assessment.stuck_awaiting, key=lambda s: s.event_date)
    name = form_label(oldest.form_type, fallback=None) if oldest.form_type else oldest.title
    return (
        f"Chase your {name} from {oldest.event_date.strftime('%-d %b %Y')} "
        "if it still needs signing"
    )


def _do_next(
    pathway_actions: list[str], assessment: HealthAssessment, *, incomplete_scan: bool
) -> list[str]:
    # A partial scan can't tell a missing item from an unscanned one, so it
    # ranks nothing: the gaps above may be the scan's, not the portfolio's.
    if incomplete_scan:
        return ["_Gaps above may just be unscanned. Run a full Kaizen scan before acting on them._", ""]
    actions = list(pathway_actions)
    chase = _chase_action(assessment)
    if chase:
        actions.insert(min(1, len(actions)), chase)
    actions = actions[:DO_NEXT_LIMIT]
    if not actions:
        return []
    lines = ["*Do next*"]
    lines.extend(f"{n}. {text}" for n, text in enumerate(actions, 1))
    return lines + [""]


def _queue_line(assessment: HealthAssessment) -> list[str]:
    drafts = len(assessment.stuck_drafts)
    awaiting = len(assessment.stuck_awaiting)
    if not drafts and not awaiting:
        return []
    parts = []
    if drafts:
        parts.append(f"{drafts} draft{'s' if drafts != 1 else ''}")
    if awaiting:
        parts.append(f"{awaiting} awaiting sign-off")
    return [f"Older than 3 weeks: {' and '.join(parts)} (buttons below).", ""]


def _landing_footer(
    assessment: HealthAssessment,
    *,
    limited_view: bool,
    partial_scan: bool,
    scan_is_fresh: bool,
) -> list[str]:
    lines = _queue_line(assessment)
    notice = _scan_notice(
        limited_view=limited_view, partial_scan=partial_scan, scan_is_fresh=scan_is_fresh
    )
    if notice:
        lines.extend([notice, ""])
    lines.append(
        f"_From {assessment.total_items} Kaizen items. "
        + SAFETY_LINE.strip("_")
        + "_"
    )
    return lines


def _tick(ok: bool) -> str:
    return "✅" if ok else "⬜"


def format_arcp_landing(
    assessment: HealthAssessment,
    checklist,
    *,
    today: date,
    limited_view: bool = False,
    partial_scan: bool = False,
    scan_is_fresh: bool = True,
) -> str:
    """Trainee landing: this year against the RCEM ARCP checklist."""
    lines = ["📊 *ARCP readiness*"]
    deadline = checklist.evidence_deadline
    if deadline and deadline >= today:
        lines.append(
            f"Evidence due {deadline.strftime('%-d %b %Y')} "
            f"({_weeks_until(deadline, today)}), 2 weeks before your "
            f"{checklist.review_date.strftime('%B')} panel"
        )
    elif deadline:
        lines.append(
            f"Your {checklist.review_date.strftime('%B %Y')} ARCP has passed. "
            "Set the next one with 📅 below."
        )
    else:
        lines.append("No ARCP month set, so this counts the last 12 months.")
    lines.extend(["", "*This year (signed off)*"])

    if checklist.msf and checklist.msf_late:
        msf = f"⚠️ MSF done {checklist.msf_first_date.strftime('%b %Y')}, after month 6"
    elif checklist.msf:
        msf = f"✅ MSF {checklist.msf_first_date.strftime('%b %Y')}"
    elif checklist.msf_overdue:
        msf = "⚠️ MSF not seen, and it's due in the first 6 months"
    else:
        msf = "⬜ MSF"
    lines.append(
        f"{msf} · {_tick(checklist.supervisor_reports > 0)} Supervisor report"
        f" · {_tick(checklist.esr > 0)} ESR"
    )
    esle_mark = "✅" if checklist.esles >= checklist.esle_target else "⬜"
    lines.append(
        f"{esle_mark} ESLEs {checklist.esles}/{checklist.esle_target} "
        "(one must be PEM: check this)"
    )
    if checklist.slos_without_evidence:
        slos = ", ".join(str(s) for s in checklist.slos_without_evidence)
        lines.append(f"⬜ SLOs with no assessed evidence: {slos}")
    else:
        lines.append("✅ Assessed evidence linked to every SLO")
    lines.extend([
        "_Form R and SLO6 procedure sign-offs aren't visible to this scan._",
        "",
    ])

    lines.extend(_do_next(
        checklist.actions, assessment, incomplete_scan=limited_view or partial_scan
    ))
    lines.extend(_landing_footer(
        assessment,
        limited_view=limited_view,
        partial_scan=partial_scan,
        scan_is_fresh=scan_is_fresh,
    ))
    return "\n".join(lines).strip()


def format_portfolio_landing(
    assessment: HealthAssessment,
    appraisal,
    cesr,
    *,
    today: date,
    limited_view: bool = False,
    partial_scan: bool = False,
    scan_is_fresh: bool = True,
) -> str:
    """Non-trainee landing: the next appraisal, then the Portfolio Pathway.

    ``cesr`` of ``None`` is the appraisal-only route (SAS and other
    non-trainees not applying): the Pathway section is left out entirely.
    """
    lines = [
        "📊 *Appraisal and Portfolio Pathway*" if cesr else "📊 *Appraisal readiness*"
    ]
    review = appraisal.review_date
    appraisal_soon = False
    if review and review >= today.replace(day=1):
        lines.append(
            f"Appraisal {review.strftime('%B %Y')} ({_weeks_until(review, today)})"
        )
        appraisal_soon = (review - today).days <= 84
    else:
        lines.append("No appraisal month set. Add it with 📅 below.")

    lines.extend(["", "*Before your appraisal (last 12 months)*"])
    lines.append(
        f"{_tick(appraisal.cpd > 0)} CPD {appraisal.cpd}"
        f" · {_tick(appraisal.qi > 0)} QI {appraisal.qi}"
        f" · {_tick(appraisal.colleague_feedback_5y > 0)} Colleague feedback"
    )
    lines.append(
        f"Significant events {appraisal.significant_events}"
        f" · Complaints/compliments {appraisal.complaints_compliments}"
        " (declare any, even if none are filed)"
    )
    lines.extend(["_Patient feedback and CPD hours aren't visible to this scan._", ""])
    if cesr:
        lines.extend(_pathway_section(cesr))

    # A near appraisal leads; otherwise the Pathway gap does.
    cesr_actions = cesr.actions if cesr else []
    ordered = (
        appraisal.actions + cesr_actions
        if appraisal_soon or not cesr
        else cesr_actions + appraisal.actions
    )
    lines.extend(_do_next(
        ordered, assessment, incomplete_scan=limited_view or partial_scan
    ))
    lines.extend(_landing_footer(
        assessment,
        limited_view=limited_view,
        partial_scan=partial_scan,
        scan_is_fresh=scan_is_fresh,
    ))
    return "\n".join(lines).strip()


def _pathway_section(cesr) -> list[str]:
    lines = ["*Portfolio Pathway (signed off, last 6 years)*"]
    per_type = " · ".join(
        f"{label} {min(cesr.wpba_counts[code], 12)}/12"
        + (" ✅" if cesr.wpba_counts[code] >= 12 else "")
        for code, label in (("DOPS", "DOPS"), ("MINI_CEX", "Mini-CEX"), ("CBD", "CBD"))
    )
    lines.append(f"WPBAs {cesr.wpba_counted}/{cesr.wpba_target}: {per_type}")
    lines.append(
        f"ESLEs {cesr.esles_3y}/6 in 3 years · {cesr.esles_12m}/3 in last 12 months"
    )
    lines.append(
        f"Reflective cases {cesr.reflections_counted}/{cesr.reflections_target}"
        f" (this year {cesr.reflections_by_year[0]}/50)"
    )
    lines.append(f"{_tick(cesr.msf_12m > 0)} MSF in last 12 months")
    if cesr.expiring_soon:
        lines.append(
            f"⚠️ {cesr.expiring_soon} "
            f"{'item leaves' if cesr.expiring_soon == 1 else 'items leave'} the 6-year window by {cesr.expiry_by.strftime('%b %Y')}"
        )
    lines.extend([
        "_Only EM WPBAs count, and cases need 20+ acute medicine and 20+ "
        "paediatric: the scan can't check either._",
        "",
    ])
    return lines


# ── About ───────────────────────────────────────────────────────────────────


def _about_facts(basis: str, *, limited_view: bool) -> list[str]:
    """Turn the stored evidence basis into a few plain lines.

    The basis is written for Scan info; About only needs how much was read,
    when, and whether the scan was partial.
    """
    facts: list[str] = []
    partial = False
    for line in basis.strip().splitlines():
        if line.startswith("Scanned:"):
            count = re.search(r"(\d+) visible evidence item", line)
            n = count.group(1) if count else None
            if "filing history only" in line:
                facts.append(
                    f"Read {n} items filed through Portfolio Guru only."
                    if n else "Read items filed through Portfolio Guru only."
                )
            else:
                facts.append(f"Read {n} items from your Kaizen." if n else line)
        elif line.startswith("Refresh:"):
            facts.append("Last refresh: " + line[len("Refresh:"):].strip())
        elif line.startswith("Scope:") and "partial" in line.lower():
            partial = True
            detail = line[len("Scope:"):].strip()
            detail = detail.split("—", 1)[1].strip() if "—" in detail else detail
            facts.append(f"Partial scan: {detail}.")
    if limited_view and not partial:
        facts.append("Partial scan: the full Kaizen index was unavailable.")
    return facts


def format_about(
    *,
    basis: str,
    limited_view: bool = False,
    scan_is_fresh: bool = True,
) -> str:
    """Concise provenance and limits for the everyday Health journey."""
    facts = _about_facts(basis, limited_view=limited_view)
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
    lines.extend(facts or ["No scan details are available for this report."])
    lines.extend([
        "",
        "• It only reads Kaizen. It never edits, files, chases or deletes.",
        "• It can misread an item, so open it in Kaizen to check.",
        "• It lists older unfinished items it can see, not every one.",
        "• A planning aid, not a formal training or appraisal judgement.",
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
    """Tagged curriculum spread as a short, scannable drill-down.

    "12/12 SLOs covered" is technically true of a portfolio holding 298 tags
    against one outcome and 13 against another. The count is the finding — and
    so is how much of the portfolio the count could not see.
    """
    counts = assessment.slo_counts
    lines = ["🎯 *SLO map*"]
    if counts:
        lines.append(
            f"{len(counts)} of 12 SLOs have tagged evidence, "
            f"from {assessment.tagged_items} tagged items."
        )
    else:
        lines.append("No tagged evidence yet, so there is no SLO spread to show.")
    lines.append("")

    if counts:
        ranked = sorted(counts.items(), key=lambda kv: kv[1])
        strongest_slo, strongest = ranked[-1]
        lines.append(f"*Most:* SLO{strongest_slo} ({strongest})")
        # With one tagged outcome the most is also the fewest, and printing it
        # twice reads as two findings.
        fewest = [f"SLO{slo} ({count})" for slo, count in ranked[:-1][:3]]
        if fewest:
            lines.append(f"*Fewest:* {', '.join(fewest)}")
        missing = [f"SLO{slo}" for slo in range(1, 13) if slo not in counts]
        if missing:
            lines.append(f"*None yet:* {', '.join(missing)}")

    # Untagged items are invisible to this view. Saying so stops a doctor
    # reading a small SLO as a gap when the evidence may simply be untagged.
    # Name the forms: a count says there is a problem, the forms say where to
    # go and fix it.
    untagged = assessment.untagged_items
    if untagged:
        worst = sorted(assessment.untagged_by_form.items(), key=lambda kv: -kv[1])[:3]
        where = ", ".join(f"{form_label(form)} {count}" for form, count in worst)
        noun = "item" if untagged == 1 else "items"
        detail = f" ({where})" if where else ""
        lines.append(
            f"*Untagged:* {untagged} {noun}{detail}, which may not count toward any SLO"
        )
    else:
        lines.append("*Untagged:* none")

    lines.extend([
        "",
        "_Counts tags only. More tags doesn't mean enough evidence, and this "
        "isn't an ARCP outcome._",
    ])
    return "\n".join(lines).strip()


# ── Scan info ───────────────────────────────────────────────────────────────


def _pathway_expectations(readiness: Optional[dict]) -> list[str]:
    if not readiness or readiness.get("pathway") != "cesr_portfolio":
        return []
    return [
        "*Portfolio Pathway expectations*",
        "Beyond WPBAs: structured consultant reports, ESLEs across core "
        "specialties, CPD with reflection, and QI work.",
        "_A 6-year evidence window applies (GMC guidance), and this is a "
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
