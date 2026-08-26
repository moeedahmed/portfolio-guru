"""Render a Portfolio Health assessment for Telegram.

Separated from the assessment so the reading and the wording can be tested
apart, and so a future web dashboard renders the same numbers differently
without reimplementing the logic.

Two rules shape everything here:

- **The verdict never appears alone.** Every score is followed by the concrete
  reasons behind it. A colour on its own invites either false comfort or
  unexplained alarm.
- **Nothing claims more than the scan saw.** The report is a reading of
  indexed Kaizen evidence, not an ARCP outcome, and it says so.

Clinical narrative never appears. Items are named by form type and date only —
descriptions hold patient detail.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from form_labels import form_label
from health_assessment import DOMAIN_LABELS, HealthAssessment, StuckEvidence
from health_models import HealthScore

SCORE_LABEL: dict[HealthScore, str] = {
    HealthScore.green: "🟢 Well covered",
    HealthScore.amber: "🟠 Needs attention",
    HealthScore.red: "🔴 Thin",
    HealthScore.grey: "⚪ Not enough scanned yet",
}

# How many stuck items to name before summarising the rest. Enough to act on,
# short enough to read on a phone.
STUCK_PREVIEW = 4


def _describe(item: StuckEvidence) -> str:
    # Doctors recognise "Teaching Observation", not "TEACH_OBS".
    name = form_label(item.form_type, fallback=None) if item.form_type else item.title
    when = item.event_date.strftime("%-d %b %Y")
    return f"• {name} — {when}, {item.days_waiting} days"


def _stuck_block(title: str, items: list[StuckEvidence], note: str) -> list[str]:
    if not items:
        return []
    lines = [f"*{title} — {len(items)}*", f"_{note}_"]
    lines.extend(_describe(item) for item in items[:STUCK_PREVIEW])
    if len(items) > STUCK_PREVIEW:
        lines.append(f"…and {len(items) - STUCK_PREVIEW} more")
    return lines + [""]


def _pathway_block(readiness: Optional[dict]) -> list[str]:
    """Pathway-specific counters the universal assessment cannot know.

    Portfolio Health is deliberately pathway-agnostic — it reads evidence, not
    curricula. Anything depending on a pathway's own rules (CESR's 36-WPBA
    minimum, later an ARCP countdown) belongs here, labelled as a pathway
    requirement rather than a health signal.
    """
    if not readiness or readiness.get("pathway") != "cesr_portfolio":
        return []
    target = readiness.get("wpba_target", 36)
    count = readiness.get("wpba_count", 0)
    breakdown = readiness.get("wpba_breakdown") or {}
    lines = ["*WPBA progress toward 36*", f"{count}/{target} counted so far"]
    if breakdown:
        lines.append(
            " · ".join(
                f"{label} {breakdown.get(key, 0)}/12"
                for key, label in (("dops", "DOPS"), ("mini_cex", "Mini-CEX"), ("cbd", "CBD"))
            )
        )
    lines.append(
        "_RCEM's stated Portfolio Pathway minimum: 12 DOPS, 12 Mini-CEX, 12 CBD. "
        "Confirm against current guidance._"
    )
    lines.append(
        "Also expected: structured consultant reports, ESLEs across core "
        "specialties, CPD with reflection, and QI work."
    )
    lines.append(
        "_Evidence window: a 5-year evidence window is the usual expectation, "
        "and this is a multi-year, long-term build rather than an annual "
        "cycle — explain any gaps._"
    )
    return lines + [""]


def format_health_report(
    assessment: HealthAssessment,
    *,
    pathway_label: str,
    month_label: str,
    scanned_count: int,
    last_scanned: Optional[str] = None,
    limited_view: bool = False,
    pathway_assumed: bool = False,
    pathway_readiness: Optional[dict] = None,
    pathway_name: Optional[str] = None,
) -> str:
    """Build the main /health message."""
    lines = [f"📊 *Portfolio Health — {pathway_label}*", month_label, ""]
    lines.append(f"*{SCORE_LABEL[assessment.score]}*")

    # The reasons are the report. The colour is only an index into them.
    for reason in assessment.reasons[:4]:
        lines.append(f"• {reason}")
    lines.append("")

    lines.extend(
        _stuck_block(
            "Waiting on someone else",
            assessment.stuck_awaiting,
            "Filed in Kaizen, not yet signed off",
        )
    )
    lines.extend(
        _stuck_block(
            "Your own unfinished drafts",
            assessment.stuck_drafts,
            "Started but never completed — yours to close",
        )
    )

    covered = [stat for stat in assessment.domains if stat.count]
    if covered:
        spread = " · ".join(
            f"{DOMAIN_LABELS[stat.domain]} {stat.count}"
            for stat in sorted(covered, key=lambda s: s.count, reverse=True)
        )
        lines.append("*Coverage*")
        lines.append(spread)
        empty = [DOMAIN_LABELS[s.domain] for s in assessment.domains if not s.count]
        if empty:
            lines.append(f"Nothing yet in: {', '.join(empty)}")
        lines.append("")

    lines.extend(_pathway_block(pathway_readiness))

    if assessment.newest_evidence:
        lines.append("*Activity*")
        lines.append(
            f"{assessment.items_last_year} items in the last 12 months, "
            f"most recent {assessment.newest_evidence.strftime('%-d %b %Y')}"
        )
        lines.append("")

    lines.append("*Next*")
    for index, action in enumerate(assessment.next_actions[:3], start=1):
        lines.append(f"{index}. {action}")
    lines.append("")

    basis = f"Read from {scanned_count} indexed Kaizen item(s)"
    if last_scanned:
        basis += f", scanned {last_scanned}"
    lines.append(f"_{basis}._")
    # State the confidence explicitly. A limited read that looks identical to a
    # full one invites a doctor to trust a partial picture.
    lines.append(
        "_Confidence: low — Portfolio Guru filings only._"
        if limited_view
        else "_Confidence: high for what was scanned; cross-check anything it could not see._"
    )
    if limited_view:
        lines.append(
            "_Limited view: based on Portfolio Guru filings only. "
            "Full Kaizen scan not available._"
        )
    if pathway_assumed:
        # The whole reading is interpreted through the pathway. Presenting an
        # assumed one as settled would let a CESR candidate read a trainee's
        # verdict without ever being told which was applied.
        lines.append(
            f"_Assumed pathway: {pathway_name or pathway_label} — change if wrong, with /pathway._"
        )
    lines.append(
        "_A planning aid, not a formal training, registration or appraisal "
        "outcome. Check against your own portfolio and current guidance._"
    )
    return "\n".join(lines).strip()


def format_stuck_detail(assessment: HealthAssessment) -> str:
    """The full stuck list, for the drill-down button."""
    if not assessment.stuck_total:
        return "✅ *Nothing waiting*\n\nEvery scanned item has completed its workflow."

    lines = ["📌 *Everything unfinished*", ""]
    if assessment.stuck_awaiting:
        lines.append(f"*Waiting on someone else — {len(assessment.stuck_awaiting)}*")
        lines.extend(_describe(item) for item in assessment.stuck_awaiting)
        lines.append("")
    if assessment.stuck_drafts:
        lines.append(f"*Your own drafts — {len(assessment.stuck_drafts)}*")
        lines.extend(_describe(item) for item in assessment.stuck_drafts)
        lines.append("")
    lines.append("_Nothing has been chased or submitted for you._")
    return "\n".join(lines).strip()


def format_domain_detail(assessment: HealthAssessment, *, today: Optional[date] = None) -> str:
    """Per-domain counts with recency, for the drill-down button."""
    reference = today or date.today()
    lines = ["📋 *Domain detail*", ""]
    for stat in sorted(assessment.domains, key=lambda s: s.count, reverse=True):
        label = DOMAIN_LABELS[stat.domain]
        if not stat.count:
            lines.append(f"• {label}: none")
            continue
        newest = stat.newest.strftime("%b %Y") if stat.newest else "unknown"
        age_days = (reference - stat.newest).days if stat.newest else None
        flags = []
        if stat.is_thin:
            flags.append("thin")
        if stat.is_stale:
            flags.append("not current")
        suffix = f" — {', '.join(flags)}" if flags else ""
        recency = f"latest {newest}"
        if age_days is not None and age_days > 365:
            recency += f" ({age_days // 365}y ago)"
        lines.append(f"• {label}: {stat.count}, {recency}{suffix}")
    lines.append("")
    lines.append(
        "_\"Thin\" is measured against your own portfolio, not a curriculum "
        "requirement — check your stage's minimums separately._"
    )
    return "\n".join(lines).strip()
