"""Offline tests for the ARCP, Portfolio Pathway and appraisal checklists.

Rules come from the RCEM Higher ARCP guide, the Gold Guide, the GMC Specialty
Specific Guidance for Emergency Medicine (Feb 2025) and GMC revalidation
guidance. Synthetic portfolios only: no Kaizen, network or Telegram.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from health_assessment import compute_health_assessment
from health_engine import _compute_pathway_readiness
from health_models import EvidenceItem, HealthDomain, Pathway
from health_report import format_arcp_landing, format_portfolio_landing
from pathway_checklist import (
    compute_appraisal_checklist,
    compute_arcp_checklist,
    compute_cesr_checklist,
)

TODAY = date(2026, 9, 27)


def _item(
    form_type,
    *,
    days_ago=30,
    state="complete",
    slos=(),
    evidence_type="wpba",
    ident=None,
    on=None,
):
    now = datetime.now(timezone.utc)
    return EvidenceItem(
        id=ident or f"{form_type}-{days_ago}-{state}-{id(slos)}",
        user_id="7",
        domain=HealthDomain.clinical,
        evidence_type=evidence_type,
        form_type=form_type,
        title=form_type,
        summary="never shown",
        event_date=on or TODAY - timedelta(days=days_ago),
        source="kaizen_filed",
        status="filed",
        created_at=now,
        updated_at=now,
        workflow_state=state,
        slo_numbers=list(slos),
    )


def _many(form_type, count, *, days_ago=30, **kw):
    return [
        _item(form_type, days_ago=days_ago, ident=f"{form_type}-{days_ago}-{n}", **kw)
        for n in range(count)
    ]


# ── ARCP ────────────────────────────────────────────────────────────────────


def test_arcp_deadline_is_two_weeks_before_the_panel():
    check = compute_arcp_checklist([], today=TODAY, review_date=date(2027, 5, 1))
    assert check.evidence_deadline == date(2027, 4, 17)
    assert check.cycle_end == date(2027, 5, 1)


def test_arcp_counts_only_signed_off_evidence_in_the_cycle():
    items = [
        _item("ESLE"),
        _item("ESLE", state="pending"),
        _item("ESLE", state="draft"),
        _item("ESLE", days_ago=400),  # last cycle
    ]
    check = compute_arcp_checklist(items, today=TODAY)
    assert check.esles == 1
    assert check.actions[0] == "Book 2 more ESLEs, one in PEM"


def test_reflections_do_not_evidence_an_slo():
    items = [
        _item("REFLECT_LOG", slos=[5], evidence_type="reflection_log"),
        _item("CBD", slos=[1, 2]),
    ]
    check = compute_arcp_checklist(items, today=TODAY, training_level="HIGHER")
    assert 5 in check.slos_without_evidence
    assert 1 not in check.slos_without_evidence


def test_core_trainees_are_not_judged_on_slos_5_8_and_12():
    check = compute_arcp_checklist([], today=TODAY, training_level="ACCS")
    assert not {5, 8, 12} & set(check.slos_without_evidence)
    higher = compute_arcp_checklist([], today=TODAY, training_level="HIGHER")
    assert {5, 8, 12} <= set(higher.slos_without_evidence)


def test_msf_after_month_six_is_flagged():
    review = date(2027, 5, 1)
    late = _item("MSF", on=date(2027, 1, 10))
    check = compute_arcp_checklist([late], today=TODAY, review_date=review)
    assert check.msf == 1 and check.msf_late

    early = _item("MSF", on=date(2026, 7, 1))
    assert not compute_arcp_checklist([early], today=TODAY, review_date=review).msf_late


def test_missing_esr_only_becomes_an_action_near_the_deadline():
    far = compute_arcp_checklist([], today=TODAY, review_date=date(2027, 6, 1))
    assert not any("ESR" in a for a in far.actions)
    near = compute_arcp_checklist([], today=TODAY, review_date=date(2026, 11, 1))
    assert any("ESR" in a for a in near.actions)


def test_arcp_landing_is_phone_sized_and_leaves_scan_limits_to_about():
    items = _many("ESLE", 2) + [_item("MSF"), _item("CBD", slos=list(range(1, 13)))]
    assessment = compute_health_assessment(items, today=TODAY)
    text = format_arcp_landing(
        assessment,
        compute_arcp_checklist(items, today=TODAY, review_date=date(2027, 5, 1)),
        today=TODAY,
    )
    assert "May 2027 panel · evidence due 17 Apr" in text
    assert "ESLEs 2 of 3" in text and "PEM" in text
    assert "Form R" not in text
    assert text.count("\n") <= 22
    do_next = text.split("*Do next*")[1]
    assert do_next.count("\n1. ") + do_next.count("\n2. ") + do_next.count("\n3. ") <= 3
    assert "\n4. " not in do_next


# ── Portfolio Pathway ───────────────────────────────────────────────────────


def test_cesr_counts_only_signed_off_dops_minicex_cbd_capped_at_12():
    items = (
        _many("DOPS", 15)
        + _many("MINI_CEX", 5)
        + _many("MINI_CEX", 3, state="pending")
        + _many("ACAT", 10)
        + _many("ESLE", 10)
        + _many("CBD", 4, days_ago=365 * 7)  # outside the 6-year window
    )
    check = compute_cesr_checklist(items, today=TODAY)
    assert check.wpba_counts == {"DOPS": 15, "MINI_CEX": 5, "CBD": 0}
    assert check.wpba_counted == 17
    assert check.wpba_target == 36


def test_engine_readiness_uses_the_corrected_count():
    items = _many("DOPS", 15) + _many("ACAT", 10) + _many("CBD", 2, state="draft")
    readiness = _compute_pathway_readiness(items, Pathway.cesr_portfolio)
    assert readiness["wpba_count"] == 12
    assert readiness["wpba_breakdown"] == {"dops": 15, "mini_cex": 0, "cbd": 0}


def test_cesr_esle_windows_and_reflections_by_year():
    items = (
        _many("ESLE", 2, days_ago=100)
        + _many("ESLE", 3, days_ago=800)
        + _many("REFLECT_LOG", 60, days_ago=50, evidence_type="reflection_log")
        + _many("REFLECT_LOG", 20, days_ago=500, evidence_type="reflection_log")
    )
    check = compute_cesr_checklist(items, today=TODAY)
    assert (check.esles_3y, check.esles_12m) == (5, 2)
    assert check.reflections_by_year == (60, 20, 0)
    assert check.reflections_counted == 70


def test_cesr_counts_paediatric_reflections_by_slo_5_tag():
    reflect = dict(evidence_type="reflection_log")
    items = (
        _many("REFLECT_LOG", 8, days_ago=50, slos=(5,), **reflect)
        + _many("REFLECT_LOG", 4, days_ago=700, slos=(1, 5), **reflect)
        + _many("REFLECT_LOG", 5, days_ago=50, slos=(1,), **reflect)  # not paeds
        + _many("REFLECT_LOG", 3, days_ago=50, slos=(5,), state="pending", **reflect)
        + _many("REFLECT_LOG", 2, days_ago=365 * 3 + 30, slos=(5,), **reflect)
        + _many("CBD", 6, slos=(5,))  # PEM WPBAs are not reflective cases
    )
    check = compute_cesr_checklist(items, today=TODAY)
    assert (check.paeds_reflections, check.paeds_reflections_target) == (12, 20)
    assert "Log 8 more paediatric reflective cases, linked to SLO 5" in check.actions

    items += _many("REFLECT_LOG", 8, days_ago=10, slos=(5,), **reflect)
    check = compute_cesr_checklist(items, today=TODAY)
    assert check.paeds_reflections == 20
    assert not any("paediatric" in a for a in check.actions)


def test_cesr_wpba_tags_never_exclude_assessments():
    # SLO 3/4/5 tags can't tell an anaesthetics, ICM or paediatrics post from
    # EM resus or PEM work, so tagged assessments still count.
    items = _many("CBD", 4, slos=(3,)) + _many("DOPS", 4, slos=(4,)) + _many("MINI_CEX", 4, slos=(5,))
    check = compute_cesr_checklist(items, today=TODAY)
    assert check.wpba_counted == 12


def test_cesr_warns_about_evidence_leaving_the_window():
    items = _many("CBD", 3, days_ago=365 * 6 - 30) + _many("CBD", 2, days_ago=100)
    assert compute_cesr_checklist(items, today=TODAY).expiring_soon == 3


# ── Appraisal ───────────────────────────────────────────────────────────────


def test_appraisal_counts_filed_items_and_colleague_feedback_over_five_years():
    items = [
        _item("FORMAL_COURSE", state="pending"),
        _item("QIAT", state="draft"),
        _item("MSF", days_ago=365 * 3),
    ]
    check = compute_appraisal_checklist(items, today=TODAY)
    assert check.cpd == 1
    assert check.qi == 0
    assert check.colleague_feedback_5y == 1
    assert check.actions == ["Add a quality improvement or audit entry for this year"]


def test_portfolio_landing_shows_appraisal_then_pathway():
    items = _many("DOPS", 5) + _many("CBD", 13) + [_item("EDU_ACT")]
    assessment = compute_health_assessment(items, today=TODAY)
    text = format_portfolio_landing(
        assessment,
        compute_appraisal_checklist(items, today=TODAY, review_date=date(2027, 3, 1)),
        compute_cesr_checklist(items, today=TODAY),
        today=TODAY,
    )
    assert text.index("Before your appraisal") < text.index("*Portfolio Pathway (")
    assert "WPBAs 17/36" in text
    assert "CBD 12/12 ✅" in text
    assert "Patient feedback" in text
    assert "Add 12 Mini-CEX, 7 DOPS" in text
    assert "⬜ Paediatric cases 0/20 (linked to SLO 5)" in text
    assert "anaesthetics, ICM, acute medicine or paediatric posts" in text
    assert "20+ acute medicine cases" in text
    assert "✅ Paediatric" not in text and "Acute medicine cases" not in text


def test_appraisal_only_landing_leaves_out_the_portfolio_pathway():
    items = _many("DOPS", 5) + [_item("EDU_ACT")]
    assessment = compute_health_assessment(items, today=TODAY)
    text = format_portfolio_landing(
        assessment,
        compute_appraisal_checklist(items, today=TODAY, review_date=date(2027, 3, 1)),
        None,
        today=TODAY,
    )
    assert text.startswith("📊 *Appraisal readiness*")
    assert "Before your appraisal" in text
    assert "Portfolio Pathway" not in text and "WPBAs" not in text
    assert "Add a quality improvement or audit entry" in text


def test_landing_counts_all_open_items_and_all_scanned_categories():
    from dataclasses import replace
    items = [_item('ESLE')] + _many('CBD', 9, days_ago=1, state='draft') + [
        _item('TEACH_OBS', on=date(2023, 8, 1), state='pending'),
        _item('CBD', days_ago=1, state='pending'),
    ]
    assessment = replace(compute_health_assessment(items, today=TODAY), scanned_items=507, total_items=460)
    check = compute_arcp_checklist(items, today=TODAY, review_date=date(2027, 5, 1))
    text = format_arcp_landing(assessment, check, today=TODAY)
    assert 'May 2027 panel · evidence due 17 Apr (28 weeks)' in text
    assert '*This year, signed off*' in text
    assert '⬜ MSF   ⬜ Supervisor report   ⬜ ESR' in text
    assert 'ESLEs 1 of 3 (one in PEM)' in text
    assert '*Still open in Kaizen*\n📝 9 to finish and send (drafts)\n⏳ 2 sent, waiting on an assessor' in text
    assert 'Ask your assessor about your Teaching Observation from Aug 2023' in text
    assert 'Form R' not in text and 'Chase' not in text
    assert '_From 507 Kaizen items. Read-only, not an ARCP judgement._' in text
    assert 'Clinical narrative' not in text
    portfolio = format_portfolio_landing(assessment, compute_appraisal_checklist(items, today=TODAY), None, today=TODAY)
    assert 'From 507 Kaizen items. Read-only, not an appraisal judgement.' in portfolio
