"""Pathway checklists: what each audience is actually judged on.

Portfolio Health's assessment is pathway-agnostic — it reads evidence, not
curricula. This module is the verified pathway overlay on top of it: three
pure checklists, each built from its audience's published rules, counting only
what the Kaizen index can see.

* ARCP (RCEM Higher ARCP guide, Gold Guide): no WPBA quota. Each year needs an
  ESR, MSF (ideally in the first six months), a supervisor report per post, at
  least three ESLEs including one PEM, and evidence against every SLO. Evidence
  is due two weeks before the panel.
* Portfolio Pathway (GMC Specialty Specific Guidance: Emergency Medicine,
  updated Feb 2025): six-year window; 36 WPBAs as 12 DOPS, 12 Mini-CEX and 12
  CBD; six ESLEs in the last three years, three in the last twelve months;
  fifty reflective cases a year for three years, at least 20 of them
  paediatric and 20 acute medicine; MSF in the last year.
  The 36 exclude assessments from anaesthetics, ICM, acute medicine and
  paediatric posts. Kaizen's SLO tags can't separate those from EM work (an
  EM resus CBD sits under SLO 3, a PEM one under SLO 5), so all are counted
  and the doctor is asked to check. Paediatric reflective cases are filed
  under SLO 5, so a reflective log tagged SLO 5 is counted as paediatric.
* Appraisal (GMC supporting information for revalidation): CPD, quality
  improvement, significant events, feedback from colleagues and patients, and
  complaints and compliments.

Nothing here claims more than the scan saw. What the index cannot see — the
specialty of an assessment, whether an ESLE was PEM, CPD hours, Form R,
patient feedback — is reported as something to check, never as a tick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

from health_models import EvidenceItem

# ── Shared rules ────────────────────────────────────────────────────────────

# Gold Guide: evidence must be in the portfolio two weeks before the panel.
ARCP_EVIDENCE_LEAD_DAYS = 14
ARCP_ESLE_TARGET = 3
# RCEM: MSF should be done in the first six months of the training year.
MSF_EARLY_WINDOW_DAYS = 183
# How close the evidence deadline must be before a missing ESR becomes an
# action; ESRs are normally written shortly before the panel.
ESR_ACTION_WITHIN_DAYS = 84

ALL_SLOS = tuple(range(1, 13))
# RCEM 2021 curriculum: SLOs 5 (paediatrics), 8 (leadership) and 12 (managing
# the department) start at Intermediate, so ACCS/core trainees are not judged
# on them.
CORE_EXCLUDED_SLOS = frozenset({5, 8, 12})
CORE_LEVELS = frozenset({"ACCS", "CORE"})

CESR_WINDOW_YEARS = 6
CESR_PER_TYPE_TARGET = 12
CESR_WPBA_TYPES = (("DOPS", "DOPS"), ("MINI_CEX", "Mini-CEX"), ("CBD", "CBD"))
CESR_ESLE_3Y_TARGET = 6
CESR_ESLE_12M_TARGET = 3
CESR_REFLECTIONS_PER_YEAR = 50
CESR_REFLECTION_YEARS = 3
CESR_PAEDS_REFLECTIONS_TARGET = 20
CESR_PAEDS_SLO = 5
# Warn about evidence that will fall out of the six-year window this soon.
CESR_EXPIRY_LOOKAHEAD_DAYS = 183

# GMC: colleague and patient feedback are needed once per five-year
# revalidation cycle, the rest are discussed at every appraisal.
REVALIDATION_CYCLE_DAYS = 365 * 5

SUPERVISOR_REPORT_FORMS = frozenset({"END_OF_PLACEMENT", "STR", "MCR"})
APPRAISAL_CPD_FORMS = frozenset({"EDU_ACT", "FORMAL_COURSE"})
APPRAISAL_QI_FORMS = frozenset({"QIAT", "AUDIT"})


def is_completed(item: EvidenceItem) -> bool:
    """Signed off in Kaizen. Pending and draft items do not count."""
    if item.workflow_state:
        return item.workflow_state.lower() == "complete"
    return item.status in {"reviewed", "accepted"}


def _is_draft(item: EvidenceItem) -> bool:
    if item.workflow_state:
        return item.workflow_state.lower() == "draft"
    return item.status == "drafted"


def _form(item: EvidenceItem) -> str:
    return (item.form_type or "").upper()


def _in(item: EvidenceItem, start: date, end: date) -> bool:
    return start <= item.event_date <= end


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def _count(items: Iterable[EvidenceItem], forms: frozenset[str], start: date, end: date) -> int:
    return sum(1 for i in items if _form(i) in forms and _in(i, start, end))


# ── ARCP ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ArcpChecklist:
    cycle_start: date
    cycle_end: date
    review_date: Optional[date]
    evidence_deadline: Optional[date]
    esr: int
    msf: int
    msf_first_date: Optional[date]
    msf_late: bool  # first MSF fell after the first six months
    msf_overdue: bool  # none yet and the first six months have passed
    supervisor_reports: int
    esles: int
    esle_target: int
    expected_slos: tuple[int, ...]
    slos_without_evidence: tuple[int, ...]
    actions: list[str] = field(default_factory=list)


def compute_arcp_checklist(
    items: list[EvidenceItem],
    *,
    today: date,
    review_date: Optional[date] = None,
    training_level: Optional[str] = None,
) -> ArcpChecklist:
    """The trainee's year, measured against the RCEM ARCP checklist.

    The cycle is the twelve months up to the review month, or the last twelve
    months when no review month is set.
    """
    if review_date:
        cycle_end = review_date
        deadline: Optional[date] = review_date - timedelta(days=ARCP_EVIDENCE_LEAD_DAYS)
    else:
        cycle_end = today
        deadline = None
    cycle_start = cycle_end - timedelta(days=365)
    done = [i for i in items if is_completed(i) and _in(i, cycle_start, cycle_end)]

    esr = _count(done, frozenset({"ESR"}), cycle_start, cycle_end)
    msf_dates = sorted(i.event_date for i in done if _form(i) == "MSF")
    early_cutoff = cycle_start + timedelta(days=MSF_EARLY_WINDOW_DAYS)
    msf_first = msf_dates[0] if msf_dates else None
    msf_late = bool(msf_first and msf_first > early_cutoff)
    msf_overdue = not msf_dates and today > early_cutoff
    reports = _count(done, SUPERVISOR_REPORT_FORMS, cycle_start, cycle_end)
    esles = _count(done, frozenset({"ESLE"}), cycle_start, cycle_end)

    level = (training_level or "").upper()
    expected = tuple(
        s for s in ALL_SLOS if not (level in CORE_LEVELS and s in CORE_EXCLUDED_SLOS)
    )
    # Reflections are not WPBAs (RCEM), so they do not evidence an SLO here.
    covered = {
        slo
        for i in done
        if i.evidence_type != "reflection_log"
        for slo in i.slo_numbers
    }
    missing_slos = tuple(s for s in expected if s not in covered)

    actions: list[str] = []
    if esles < ARCP_ESLE_TARGET:
        need = ARCP_ESLE_TARGET - esles
        actions.append(
            f"Book {need} more ESLE{'s' if need > 1 else ''}, one in PEM"
        )
    else:
        actions.append("Check at least one of your ESLEs this year was in PEM")
    if missing_slos:
        shown = ", ".join(str(s) for s in missing_slos[:3])
        actions.append(f"Get an assessment linked to SLO {shown}")
    if not msf_dates:
        actions.append(
            "Start your MSF now; it is due in the first six months"
            if msf_overdue
            else "Start your MSF (due in the first 6 months)"
        )
    if not reports:
        actions.append("Ask your clinical supervisor for your placement report")
    if not esr and deadline and (deadline - today).days <= ESR_ACTION_WITHIN_DAYS:
        actions.append(
            f"Ask your educational supervisor to complete the ESR before "
            f"{deadline.strftime('%-d %b')}"
        )

    return ArcpChecklist(
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        review_date=review_date,
        evidence_deadline=deadline,
        esr=esr,
        msf=len(msf_dates),
        msf_first_date=msf_first,
        msf_late=msf_late,
        msf_overdue=msf_overdue,
        supervisor_reports=reports,
        esles=esles,
        esle_target=ARCP_ESLE_TARGET,
        expected_slos=expected,
        slos_without_evidence=missing_slos,
        actions=actions,
    )


# ── Portfolio Pathway (CESR) ────────────────────────────────────────────────


@dataclass(frozen=True)
class CesrChecklist:
    window_start: date
    wpba_counts: dict[str, int]  # form code -> completed count, uncapped
    wpba_counted: int  # capped at 12 per type
    wpba_target: int
    esles_3y: int
    esles_12m: int
    reflections_by_year: tuple[int, ...]  # most recent year first
    reflections_counted: int  # capped at 50 per year
    reflections_target: int
    paeds_reflections: int  # reflective logs tagged SLO 5, last 3 years
    paeds_reflections_target: int
    msf_12m: int
    expiring_soon: int
    expiry_by: date
    actions: list[str] = field(default_factory=list)


def compute_cesr_checklist(items: list[EvidenceItem], *, today: date) -> CesrChecklist:
    """Signed-off evidence against the GMC Portfolio Pathway minimums."""
    window_start = _years_before(today, CESR_WINDOW_YEARS)
    done = [i for i in items if is_completed(i) and _in(i, window_start, today)]

    counts = {
        code: _count(done, frozenset({code}), window_start, today)
        for code, _ in CESR_WPBA_TYPES
    }
    counted = sum(min(n, CESR_PER_TYPE_TARGET) for n in counts.values())
    target = CESR_PER_TYPE_TARGET * len(CESR_WPBA_TYPES)

    esle = frozenset({"ESLE"})
    last_year = today - timedelta(days=365)
    esles_3y = _count(done, esle, _years_before(today, 3), today)
    esles_12m = _count(done, esle, last_year, today)

    reflections: list[int] = []
    for n in range(CESR_REFLECTION_YEARS):
        end = today - timedelta(days=365 * n)
        start = end - timedelta(days=365)
        reflections.append(
            sum(
                1
                for i in done
                if _form(i) == "REFLECT_LOG" and start < i.event_date <= end
            )
        )
    reflections_counted = sum(min(n, CESR_REFLECTIONS_PER_YEAR) for n in reflections)
    reflections_start = today - timedelta(days=365 * CESR_REFLECTION_YEARS)
    paeds = sum(
        1
        for i in done
        if _form(i) == "REFLECT_LOG"
        and CESR_PAEDS_SLO in i.slo_numbers
        and reflections_start < i.event_date <= today
    )

    msf_12m = _count(done, frozenset({"MSF"}), last_year, today)
    expiry_by = today + timedelta(days=CESR_EXPIRY_LOOKAHEAD_DAYS)
    expiry_cutoff = window_start + timedelta(days=CESR_EXPIRY_LOOKAHEAD_DAYS)
    expiring = sum(1 for i in done if i.event_date < expiry_cutoff)

    actions: list[str] = []
    gaps = sorted(
        (
            (CESR_PER_TYPE_TARGET - counts[code], label)
            for code, label in CESR_WPBA_TYPES
            if counts[code] < CESR_PER_TYPE_TARGET
        ),
        reverse=True,
    )
    if gaps:
        actions.append(
            "Add " + ", ".join(f"{need} {label}" for need, label in gaps)
            + " to reach 12 of each"
        )
    if esles_12m < CESR_ESLE_12M_TARGET or esles_3y < CESR_ESLE_3Y_TARGET:
        need = max(CESR_ESLE_12M_TARGET - esles_12m, CESR_ESLE_3Y_TARGET - esles_3y)
        actions.append(
            f"Book {need} ESLE{'s' if need > 1 else ''}; the assessors can "
            "also be your referees"
        )
    if reflections[0] < CESR_REFLECTIONS_PER_YEAR:
        actions.append(
            f"Log {CESR_REFLECTIONS_PER_YEAR - reflections[0]} more reflective "
            "cases this year, including acute medicine cases"
        )
    if paeds < CESR_PAEDS_REFLECTIONS_TARGET:
        actions.append(
            f"Log {CESR_PAEDS_REFLECTIONS_TARGET - paeds} more paediatric "
            "reflective cases, linked to SLO 5"
        )
    if not msf_12m:
        actions.append("Start an MSF; the GMC wants one from the last 12 months")

    return CesrChecklist(
        window_start=window_start,
        wpba_counts=counts,
        wpba_counted=counted,
        wpba_target=target,
        esles_3y=esles_3y,
        esles_12m=esles_12m,
        reflections_by_year=tuple(reflections),
        reflections_counted=reflections_counted,
        reflections_target=CESR_REFLECTIONS_PER_YEAR * CESR_REFLECTION_YEARS,
        paeds_reflections=paeds,
        paeds_reflections_target=CESR_PAEDS_REFLECTIONS_TARGET,
        msf_12m=msf_12m,
        expiring_soon=expiring,
        expiry_by=expiry_by,
        actions=actions,
    )


# ── Appraisal ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AppraisalChecklist:
    review_date: Optional[date]
    cpd: int
    qi: int
    significant_events: int
    complaints_compliments: int
    colleague_feedback_5y: int
    actions: list[str] = field(default_factory=list)


def compute_appraisal_checklist(
    items: list[EvidenceItem], *, today: date, review_date: Optional[date] = None
) -> AppraisalChecklist:
    """The GMC's supporting information, from the last twelve months.

    Drafts are left out; anything filed counts, because appraisal asks what
    you did and reflected on, not whether someone else signed it off.
    """
    start = today - timedelta(days=365)
    filed = [i for i in items if not _is_draft(i)]

    cpd = _count(filed, APPRAISAL_CPD_FORMS, start, today)
    qi = _count(filed, APPRAISAL_QI_FORMS, start, today)
    sig = _count(filed, frozenset({"SERIOUS_INCIDENT"}), start, today)
    complaints = _count(filed, frozenset({"COMPLAINT"}), start, today)
    msf = _count(
        filed,
        frozenset({"MSF"}),
        today - timedelta(days=REVALIDATION_CYCLE_DAYS),
        today,
    )

    actions: list[str] = []
    if not cpd:
        actions.append("Log this year's CPD with a short reflection")
    if not qi:
        actions.append("Add a quality improvement or audit entry for this year")
    if not msf:
        actions.append("Arrange colleague feedback; one is needed each revalidation cycle")

    return AppraisalChecklist(
        review_date=review_date,
        cpd=cpd,
        qi=qi,
        significant_events=sig,
        complaints_compliments=complaints,
        colleague_feedback_5y=msf,
        actions=actions,
    )
