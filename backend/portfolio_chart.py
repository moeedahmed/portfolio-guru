"""
Portfolio health chart generator.

Renders a deterministic ~800x800 PNG with four panels:
  - Form types filed this month (horizontal bar)
  - Curriculum coverage (SLO1–SLO12 grid)
  - Weekly trend for the current month (bar)
  - Usage headline ("X of Y cases filed this month — tier")

Pure matplotlib so the layout is reproducible and cheap. No LLM in the
visual path; the data comes from usage.db and profile_store.
"""
from __future__ import annotations

import asyncio
import calendar
import io
import os
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from usage import (
    TIER_LIMITS,
    get_case_history,
    get_cases_this_month,
    get_kc_coverage,
    get_kc_stats,
    get_user_tier,
    is_beta_tester,
)
from profile_store import get_training_level

# Brand palette
COLOR_BG = "#F8FAFC"
COLOR_HEADER = "#0B1F33"
COLOR_ACCENT = "#1E6FBA"
COLOR_ACCENT_LIGHT = "#4A90D9"
COLOR_ACCENT_DIM = "#A8C6E5"
COLOR_TEXT_LIGHT = "#FFFFFF"
COLOR_TEXT_DARK = "#1A1A1A"
COLOR_LABEL = "#4A5568"
COLOR_MUTED = "#7A8794"
COLOR_GRID_EMPTY = "#E6EBF0"
COLOR_GRID_FILLED = "#1E6FBA"
COLOR_SLO_FILLED = "#22C55E"

# Gradient of brand blues used for ranked bars (dark = highest rank).
PALETTE_BLUES = ["#0B3D6B", "#15579A", "#1E6FBA", "#4A90D9", "#7FB3E5", "#A8C6E5"]

# Each WPBA / activity form maps to one or more SLOs.
# Conservative mapping — only credit SLOs where the form type genuinely
# provides evidence. Used solely for the visual coverage grid; the LLM
# analysis path is unchanged.
FORM_TO_SLOS = {
    "CBD": [1, 2, 3, 4, 5, 6, 7],
    "MINI_CEX": [1, 2, 3, 4, 5, 6, 7],
    "DOPS": [3, 4, 7],
    "ACAT": [1, 2, 3, 5],
    "LAT": [1, 2, 3, 5],
    "ACAF": [10],
    "STAT": [1, 3, 5],
    "MSF": [11],
    "QIAT": [10],
    "JCF": [11],
    "TEACH": [12],
    "TEACH_OBS": [12],
    "TEACH_CONFID": [12],
    "PROC_LOG": [3, 4, 7],
    "SDL": [12],
    "US_CASE": [3, 7],
    "COMPLAINT": [11],
    "SERIOUS_INC": [10, 11],
    "EDU_ACT": [12],
    "FORMAL_COURSE": [12],
    "ESLE_ASSESS": [1, 2, 3, 4, 5, 6, 7, 8, 9],
    "REFLECT_LOG": [11],
    "MGMT_ROTA": [10],
    "MGMT_RISK": [10],
    "MGMT_RECRUIT": [10],
    "MGMT_PROJECT": [10],
    "MGMT_RISK_PROC": [10],
    "MGMT_TRAINING_EVT": [10, 12],
    "MGMT_GUIDELINE": [10],
    "MGMT_INFO": [10],
}


def _short_form_name(form_type: str) -> str:
    """Compact label for charting. Falls back to the raw code."""
    try:
        from form_schemas import FORM_SCHEMAS
        full = FORM_SCHEMAS.get(form_type, {}).get("name", form_type)
    except Exception:
        full = form_type
    # Pick a short label — Kaizen names are long.
    aliases = {
        "CBD": "CBD",
        "MINI_CEX": "Mini-CEX",
        "DOPS": "DOPS",
        "ACAT": "ACAT",
        "LAT": "LAT",
        "ACAF": "ACAF",
        "STAT": "STAT",
        "MSF": "MSF",
        "QIAT": "QIAT",
        "JCF": "Journal Club",
        "TEACH": "Teaching",
        "TEACH_OBS": "Teach Obs",
        "TEACH_CONFID": "Teach Confid",
        "PROC_LOG": "Procedural",
        "SDL": "SDL",
        "US_CASE": "US Case",
        "COMPLAINT": "Complaint",
        "SERIOUS_INC": "Serious Inc",
        "EDU_ACT": "Educational",
        "FORMAL_COURSE": "Course",
        "ESLE_ASSESS": "ESLE",
        "REFLECT_LOG": "Reflection",
        "MGMT_ROTA": "Mgmt Rota",
        "MGMT_RISK": "Mgmt Risk",
        "MGMT_RECRUIT": "Mgmt Recruit",
        "MGMT_PROJECT": "Mgmt Project",
        "MGMT_RISK_PROC": "Mgmt Risk Proc",
        "MGMT_TRAINING_EVT": "Mgmt Training",
        "MGMT_GUIDELINE": "Mgmt Guideline",
        "MGMT_INFO": "Mgmt Info",
    }
    if form_type in aliases:
        return aliases[form_type]
    return full[:18]


def _filter_this_month(history: list[dict]) -> list[dict]:
    now = datetime.now()
    key = now.strftime("%Y-%m")
    out = []
    for row in history:
        filed_at = row.get("filed_at") or ""
        if filed_at.startswith(key):
            out.append(row)
    return out


def _weekly_buckets(history_this_month: list[dict]) -> list[tuple[str, int]]:
    """Bucket this month's filings into ISO weeks (Mon–Sun)."""
    now = datetime.now()
    _, days_in_month = calendar.monthrange(now.year, now.month)
    month_start = datetime(now.year, now.month, 1)

    # Build week buckets covering the calendar month.
    buckets: list[tuple[datetime, datetime, int]] = []
    cursor = month_start
    while cursor.day <= days_in_month and cursor.month == now.month:
        # Each bucket spans Mon..Sun, clipped to the month.
        week_start = cursor
        days_until_sunday = 6 - week_start.weekday()
        week_end = week_start + timedelta(days=days_until_sunday)
        if week_end.month != now.month:
            week_end = datetime(now.year, now.month, days_in_month)
        buckets.append([week_start, week_end, 0])
        cursor = week_end + timedelta(days=1)

    for row in history_this_month:
        try:
            ts = datetime.fromisoformat(row["filed_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        ts = ts.replace(tzinfo=None)
        for b in buckets:
            if b[0] <= ts <= b[1].replace(hour=23, minute=59, second=59):
                b[2] += 1
                break

    return [(f"{b[0].day}–{b[1].day}", b[2]) for b in buckets]


def _coverage_from_history(history_6mo: list[dict]) -> set[int]:
    covered: set[int] = set()
    for row in history_6mo:
        ft = row.get("form_type")
        for slo in FORM_TO_SLOS.get(ft, []):
            covered.add(slo)
    return covered


def _coverage_from_kcs(kc_coverage: dict[int, list[str]]) -> set[int]:
    """SLOs with at least one demonstrated KC."""
    return {slo for slo, kcs in kc_coverage.items() if kcs}


def _format_count_list(items: list[tuple[str, int]], empty: str) -> str:
    if not items:
        return empty
    return ", ".join(f"{label} ({count})" for label, count in items)


def format_health_activity_snapshot(
    history_6mo: list[dict],
    cases_this_month: int,
    tier: str,
    limit: int,
    training_level: Optional[str],
    kc_coverage: Optional[dict[int, list[str]]] = None,
    kc_stats: Optional[dict] = None,
) -> str:
    """Text equivalent of the four-panel /health chart.

    The /health command stays text-only, but still exposes the same useful
    activity detail the chart used to show.
    """
    this_month = _filter_this_month(history_6mo)
    form_counts = Counter(r["form_type"] for r in this_month if r.get("form_type"))
    top_forms = [(_short_form_name(ft), count) for ft, count in form_counts.most_common(6)]
    weekly = _weekly_buckets(this_month)

    if kc_coverage and any(kc_coverage.values()):
        coverage = _coverage_from_kcs(kc_coverage)
        coverage_source = "KC evidence"
    else:
        coverage = _coverage_from_history(history_6mo)
        coverage_source = "filed forms"

    coverage_line = (
        f"{len(coverage)}/12 SLOs touched by Portfolio Guru-linked evidence "
        f"({coverage_source}) — not your full Kaizen strength"
    )
    if coverage:
        coverage_line += " (" + ", ".join(f"SLO {slo}" for slo in sorted(coverage)) + ")"

    weekly_items = [(label, count) for label, count in weekly]
    lines = [
        "*Activity snapshot*",
        f"- This month: {cases_this_month} case{'s' if cases_this_month != 1 else ''}",
        f"- Form mix: {_format_count_list(top_forms, 'none yet this month')}",
        f"- Curriculum coverage: {coverage_line}",
        f"- Weekly filings: {_format_count_list(weekly_items, 'none yet this month')}",
        f"- Portfolio level: {training_level or 'not set'}",
    ]

    if kc_stats and kc_stats.get("total_kcs", 0) > 0:
        lines.append(
            "- KCs demonstrated: "
            f"{kc_stats['total_kcs']} across "
            f"{kc_stats['slos_covered']}/{kc_stats['slos_total']} SLOs"
        )

    return "\n".join(lines)


async def format_health_activity_snapshot_async(
    user_id: int,
    history_6mo: Optional[list[dict]] = None,
    training_level: Optional[str] = None,
) -> str:
    data = await _collect(user_id)
    return format_health_activity_snapshot(
        history_6mo=history_6mo if history_6mo is not None else data["history"],
        cases_this_month=data["cases_this_month"],
        tier=data["tier"],
        limit=data["limit"],
        training_level=training_level if training_level is not None else data["training_level"],
        kc_coverage=data.get("kc_coverage"),
        kc_stats=data.get("kc_stats"),
    )


def _render(
    user_id: int,
    history_6mo: list[dict],
    cases_this_month: int,
    tier: str,
    limit: int,
    training_level: Optional[str],
    kc_coverage: Optional[dict[int, list[str]]] = None,
    kc_stats: Optional[dict] = None,
) -> str:
    this_month = _filter_this_month(history_6mo)
    form_counts = Counter(r["form_type"] for r in this_month if r.get("form_type"))
    weekly = _weekly_buckets(this_month)
    # Prefer real KC-derived coverage when the user has any demonstrated KCs;
    # fall back to the form-type→SLO mapping for users whose filings predate
    # KC tracking.
    if kc_coverage and any(kc_coverage.values()):
        coverage = _coverage_from_kcs(kc_coverage)
    else:
        coverage = _coverage_from_history(history_6mo)

    fig = plt.figure(figsize=(8, 8), dpi=100, facecolor=COLOR_BG)
    plt.rcParams["font.family"] = "DejaVu Sans"

    # ===== Header band =====
    header_ax = fig.add_axes([0, 0.88, 1, 0.12])
    header_ax.axis("off")
    header_ax.add_patch(
        Rectangle((0, 0), 1, 1, transform=header_ax.transAxes,
                  color=COLOR_HEADER, zorder=0)
    )
    month_label = datetime.now().strftime("%B %Y")
    header_ax.text(
        0.04, 0.60, "Portfolio Health",
        fontsize=18, fontweight="bold", color=COLOR_TEXT_LIGHT,
        transform=header_ax.transAxes,
    )
    header_ax.text(
        0.04, 0.22, month_label,
        fontsize=11, color=COLOR_ACCENT_DIM,
        transform=header_ax.transAxes,
    )

    # Panel title positions (figure coords)
    TITLE_Y_TOP = 0.815
    TITLE_Y_BOT = 0.395

    # ===== Top-left: Form types (horizontal bar) =====
    fig.text(0.06, TITLE_Y_TOP, "This month by form type",
             fontsize=12, fontweight="bold", color=COLOR_TEXT_DARK)
    forms_ax = fig.add_axes([0.20, 0.50, 0.28, 0.28])
    forms_ax.set_facecolor(COLOR_BG)
    if form_counts:
        items = form_counts.most_common(6)
        labels = [_short_form_name(ft) for ft, _ in items]
        values = [c for _, c in items]
        n = len(items)
        colors = [PALETTE_BLUES[min(i, len(PALETTE_BLUES) - 1)] for i in range(n)]
        y_pos = list(range(n))
        forms_ax.barh(y_pos, values, color=colors, height=0.62, edgecolor="none")
        forms_ax.set_yticks(y_pos)
        forms_ax.set_yticklabels(labels, fontsize=9, color=COLOR_LABEL)
        forms_ax.invert_yaxis()
        max_v = max(values)
        forms_ax.set_xlim(0, max_v * 1.18 + 1)
        forms_ax.set_xticks([])
        for s in ("top", "right", "bottom"):
            forms_ax.spines[s].set_visible(False)
        forms_ax.spines["left"].set_color(COLOR_GRID_EMPTY)
        forms_ax.tick_params(axis="y", length=0)
        for i, v in enumerate(values):
            forms_ax.text(v + max_v * 0.05, i, str(v),
                          va="center", fontsize=9, fontweight="bold",
                          color=COLOR_TEXT_DARK)
    else:
        forms_ax.text(0.5, 0.5, "No filings yet this month",
                      ha="center", va="center", fontsize=10,
                      color=COLOR_MUTED, transform=forms_ax.transAxes)
        forms_ax.axis("off")

    # ===== Top-right: SLO coverage grid =====
    fig.text(0.54, TITLE_Y_TOP, "Curriculum coverage (last 6 mo)",
             fontsize=12, fontweight="bold", color=COLOR_TEXT_DARK)
    slo_ax = fig.add_axes([0.54, 0.50, 0.42, 0.28])
    slo_ax.set_xlim(0, 4)
    slo_ax.set_ylim(0, 3)
    slo_ax.invert_yaxis()
    slo_ax.axis("off")
    for idx in range(12):
        row, col = divmod(idx, 4)
        slo_num = idx + 1
        filled = slo_num in coverage
        x = col + 0.10
        y = row + 0.15
        slo_ax.add_patch(Rectangle(
            (x, y), 0.80, 0.70,
            facecolor=COLOR_SLO_FILLED if filled else COLOR_GRID_EMPTY,
            edgecolor=COLOR_BG, linewidth=2,
        ))
        slo_ax.text(
            x + 0.40, y + 0.35, str(slo_num),
            ha="center", va="center",
            fontsize=14, fontweight="bold",
            color=COLOR_TEXT_LIGHT if filled else COLOR_MUTED,
        )

    # ===== Bottom-left: Weekly trend =====
    fig.text(0.06, TITLE_Y_BOT, "Filings per week",
             fontsize=12, fontweight="bold", color=COLOR_TEXT_DARK)
    trend_ax = fig.add_axes([0.10, 0.08, 0.38, 0.27])
    trend_ax.set_facecolor(COLOR_BG)
    if weekly:
        labels = [w[0] for w in weekly]
        values = [w[1] for w in weekly]
        n = len(weekly)
        x_pos = list(range(n))
        max_v = max(values + [1])
        bar_colors = [COLOR_ACCENT_LIGHT if v < max_v else COLOR_ACCENT
                      for v in values]
        trend_ax.bar(x_pos, values, color=bar_colors, width=0.58,
                     edgecolor="none", zorder=2)
        trend_ax.set_xticks(x_pos)
        trend_ax.set_xticklabels(labels, fontsize=9, color=COLOR_LABEL)
        trend_ax.set_ylim(0, max_v * 1.25 + 0.5)
        trend_ax.set_yticks([])
        trend_ax.yaxis.grid(True, color=COLOR_GRID_EMPTY,
                            linewidth=0.7, zorder=0)
        trend_ax.set_axisbelow(True)
        for s in ("top", "right", "left"):
            trend_ax.spines[s].set_visible(False)
        trend_ax.spines["bottom"].set_color(COLOR_GRID_EMPTY)
        trend_ax.tick_params(axis="x", length=0)
        for i, v in enumerate(values):
            if v > 0:
                trend_ax.text(i, v + max_v * 0.06, str(v),
                              ha="center", fontsize=9, fontweight="bold",
                              color=COLOR_TEXT_DARK)
    else:
        trend_ax.axis("off")

    # ===== Bottom-right: Usage / activity summary card =====
    fig.text(0.54, TITLE_Y_BOT, "Activity summary",
             fontsize=12, fontweight="bold", color=COLOR_TEXT_DARK)
    usage_ax = fig.add_axes([0.54, 0.08, 0.42, 0.27])
    usage_ax.set_xlim(0, 1)
    usage_ax.set_ylim(0, 1)
    usage_ax.axis("off")

    tier_pretty = (tier or "").strip().title() or "Plan"
    if limit == -1:
        plan_line = f"{tier_pretty} Plan · unlimited"
    else:
        plan_line = f"{tier_pretty} Plan · {cases_this_month}/{limit}"
    level_line = training_level or "Portfolio not set"
    kc_line = ""
    if kc_stats and kc_stats.get("total_kcs", 0) > 0:
        kc_line = (
            f"KCs demonstrated: {kc_stats['total_kcs']} across "
            f"{kc_stats['slos_covered']}/{kc_stats['slos_total']} SLOs"
        )

    usage_ax.text(0.5, 0.74, str(cases_this_month),
                  fontsize=44, fontweight="bold",
                  color=COLOR_ACCENT, ha="center", va="center")
    usage_ax.text(0.5, 0.48, "cases filed this month",
                  fontsize=10, color=COLOR_MUTED,
                  ha="center", va="center")
    usage_ax.text(0.5, 0.30, plan_line,
                  fontsize=11, fontweight="bold",
                  color=COLOR_TEXT_DARK, ha="center", va="center")
    usage_ax.text(0.5, 0.18, level_line,
                  fontsize=10, color=COLOR_LABEL,
                  ha="center", va="center")
    if kc_line:
        usage_ax.text(0.5, 0.10, kc_line,
                      fontsize=9, color=COLOR_LABEL,
                      ha="center", va="center")

    if limit > 0:
        frac = min(cases_this_month / limit, 1.0)
        usage_ax.add_patch(Rectangle((0.15, 0.04), 0.70, 0.035,
                                     color=COLOR_GRID_EMPTY))
        usage_ax.add_patch(Rectangle((0.15, 0.04), 0.70 * frac, 0.035,
                                     color=COLOR_ACCENT))

    # Save to temp file
    fd, path = tempfile.mkstemp(prefix=f"portfolio_health_{user_id}_",
                                suffix=".png")
    os.close(fd)
    fig.savefig(path, dpi=100, facecolor=COLOR_BG)
    plt.close(fig)
    return path


async def _collect(user_id: int) -> dict:
    """Pull every input the chart needs in parallel-ish."""
    history = await get_case_history(user_id, months=6)
    cases = await get_cases_this_month(user_id)
    tier = await get_user_tier(user_id)
    beta = await is_beta_tester(user_id)
    if beta:
        tier_label = "beta"
        limit = -1
    else:
        tier_label = tier
        limit = TIER_LIMITS.get(tier, 5)
    try:
        kc_coverage = await get_kc_coverage(user_id)
        kc_stats = await get_kc_stats(user_id)
    except Exception:
        kc_coverage = {}
        kc_stats = None
    return {
        "history": history,
        "cases_this_month": cases,
        "tier": tier_label,
        "limit": limit,
        "training_level": get_training_level(user_id),
        "kc_coverage": kc_coverage,
        "kc_stats": kc_stats,
    }


async def generate_health_chart_async(user_id: int) -> str:
    data = await _collect(user_id)
    return _render(
        user_id=user_id,
        history_6mo=data["history"],
        cases_this_month=data["cases_this_month"],
        tier=data["tier"],
        limit=data["limit"],
        training_level=data["training_level"],
        kc_coverage=data.get("kc_coverage"),
        kc_stats=data.get("kc_stats"),
    )


def generate_health_chart(user_id: int) -> str:
    """Synchronous entry point — convenience wrapper used by smoke tests."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        raise RuntimeError(
            "generate_health_chart() called inside a running event loop — "
            "use generate_health_chart_async() instead."
        )
    return asyncio.run(generate_health_chart_async(user_id))


# ── Weekly nudge card (lean behaviour nudge, not dense dashboard) ────────────


async def generate_weekly_nudge_chart_async(user_id: int) -> str:
    data = await _collect_nudge(user_id)
    return _render_nudge_card(
        user_id=user_id,
        cases_this_week=data["cases_this_week"],
        form_types_this_month=data["form_types_this_month"],
        top_form=data.get("top_form"),
        gap=data.get("gap"),
    )


async def _collect_nudge(user_id: int) -> dict:
    from bot import _compute_weekly_stats
    return await _compute_weekly_stats(user_id)


def _render_nudge_card(
    user_id: int,
    cases_this_week: int,
    form_types_this_month: int,
    top_form: tuple | None,
    gap: tuple | None,
) -> str:
    fig = plt.figure(figsize=(6, 3.2), dpi=100, facecolor=COLOR_BG)

    # Header band
    header_ax = fig.add_axes([0, 0.80, 1, 0.20])
    header_ax.axis("off")
    header_ax.add_patch(
        Rectangle((0, 0), 1, 1, transform=header_ax.transAxes,
                  color=COLOR_HEADER, zorder=0)
    )
    header_ax.text(
        0.08, 0.55, "Portfolio Check-In",
        fontsize=17, fontweight="bold", color=COLOR_TEXT_LIGHT,
        va="center", transform=header_ax.transAxes,
    )

    # Body — up to 3 data rows + action callout
    body_y = 0.70
    row_spacing = 0.16
    fig.text(0.08, body_y,
             _nudge_line_win(cases_this_week),
             fontsize=13, color=COLOR_TEXT_DARK, fontweight="bold",
             va="center")

    fig.text(0.08, body_y - row_spacing,
             _nudge_line_signal(form_types_this_month, top_form),
             fontsize=13, color=COLOR_TEXT_DARK,
             va="center")

    gap_line, action_line = _nudge_line_deficiency_and_action(gap)
    if gap_line:
        fig.text(0.08, body_y - row_spacing * 2,
                 gap_line,
                 fontsize=13, color="#C0392B",
                 va="center")

    # Action callout strip at bottom
    if action_line:
        action_ax = fig.add_axes([0.06, 0.06, 0.88, 0.18])
        action_ax.set_facecolor("#EBF5FB")
        action_ax.axis("off")
        action_ax.text(0.04, 0.5, action_line,
                       fontsize=11, color=COLOR_ACCENT, fontweight="bold",
                       va="center", transform=action_ax.transAxes)

    fd, path = tempfile.mkstemp(prefix=f"weekly_nudge_{user_id}_", suffix=".png")
    os.close(fd)
    fig.savefig(path, dpi=100, facecolor=COLOR_BG, bbox_inches="tight")
    plt.close(fig)
    return path


def _nudge_line_win(cases: int) -> str:
    s = "s" if cases != 1 else ""
    return f"{cases} case{s} filed this week"


def _nudge_line_signal(form_types: int, top_form: tuple | None) -> str:
    if not form_types:
        return "No filings yet this month"
    line = f"{form_types} form type{'s' if form_types != 1 else ''} this month"
    if top_form:
        label, count = top_form
        line += f"  ·  Most: {label} ({count})"
    return line


def _nudge_line_deficiency_and_action(gap: tuple | None) -> tuple[str, str]:
    if gap:
        label, days = gap
        deficiency = f"Gap: {label}, {days} days"
        if "Procedure Log" in label:
            action = ("Add one " + label + "  /  Tap /health for full breakdown")
        else:
            action = ("Log a " + label + "  /  Tap /health for full breakdown")
        return deficiency, action
    return "", "Tap /health for full breakdown"


if __name__ == "__main__":
    import sys
    uid = int(sys.argv[1]) if len(sys.argv) > 1 else 6912896590
    print(generate_health_chart(uid))
