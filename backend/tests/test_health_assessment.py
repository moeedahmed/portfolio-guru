"""Offline tests for the Portfolio Health assessment and report.

These guard the failures found on a real 501-item portfolio on 2026-08-26,
where the old report said "Green — main evidence domains are covered" and
"Missing domains: None obvious" while 27 items sat unfinished, the oldest for
1112 days, and QI held 7 items against 250 clinical.

No Kaizen, browser, network, or Telegram.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from health_assessment import compute_health_assessment
from health_models import EvidenceItem, HealthDomain, HealthScore
from health_report import format_domain_detail, format_health_report, format_stuck_detail

TODAY = date(2026, 8, 26)


def _item(
    *,
    domain=HealthDomain.clinical,
    days_ago=30,
    state=None,
    form_type="CBD",
    ident="x",
    title="CBD - Case Based Discussion",
):
    now = datetime.now(timezone.utc)
    return EvidenceItem(
        id=ident,
        user_id="7",
        domain=domain,
        evidence_type="wpba",
        form_type=form_type,
        title=title,
        summary="Clinical narrative that must never reach a report",
        event_date=date.fromordinal(TODAY.toordinal() - days_ago),
        source="kaizen_filed",
        source_ref="https://kaizenep.com/events/view/x",
        status="filed",
        created_at=now,
        updated_at=now,
        workflow_state=state,
    )


def _balanced(per_domain=20):
    items = []
    for domain in HealthDomain:
        if domain == HealthDomain.unclassified:
            continue
        for index in range(per_domain):
            items.append(_item(domain=domain, ident=f"{domain.value}-{index}"))
    return items


# ── Scoring ─────────────────────────────────────────────────────────────────


def test_balanced_current_portfolio_is_green():
    assessment = compute_health_assessment(_balanced(), today=TODAY)
    assert assessment.score == HealthScore.green
    assert assessment.reasons  # never a colour on its own


def test_imbalance_alone_moves_the_score():
    """The old score was a presence check: every domain holding anything earned
    Green, so 250 clinical against 7 QI scored the same as a balanced
    portfolio. Imbalance must be able to move the light by itself."""
    items = _balanced(40)
    items = [i for i in items if i.domain != HealthDomain.qi]
    items += [_item(domain=HealthDomain.qi, ident=f"qi-{n}") for n in range(3)]

    assessment = compute_health_assessment(items, today=TODAY)

    assert assessment.score == HealthScore.amber
    assert any("QI is thin" in reason for reason in assessment.reasons)


def test_stale_domain_moves_the_score():
    items = _balanced(30)
    items = [i for i in items if i.domain != HealthDomain.teaching]
    items += [
        _item(domain=HealthDomain.teaching, days_ago=365 * 4, ident=f"t-{n}") for n in range(30)
    ]

    assessment = compute_health_assessment(items, today=TODAY)

    assert assessment.score == HealthScore.amber
    assert any("Teaching evidence stops at" in reason for reason in assessment.reasons)


def test_score_drops_only_one_step_however_many_concerns():
    """Stacking demotions would send every large portfolio to red and teach
    doctors to ignore the colour. The reasons carry the detail instead."""
    items = _balanced(40)
    items = [i for i in items if i.domain not in (HealthDomain.qi, HealthDomain.teaching)]
    items += [_item(domain=HealthDomain.qi, ident=f"qi-{n}") for n in range(2)]
    items += [_item(domain=HealthDomain.teaching, ident=f"t-{n}") for n in range(2)]
    items += [
        _item(state="pending", days_ago=900, ident=f"p-{n}", form_type="MINI_CEX")
        for n in range(15)
    ]

    assessment = compute_health_assessment(items, today=TODAY)

    assert assessment.score == HealthScore.amber
    assert len(assessment.reasons) >= 3


def test_empty_portfolio_is_grey_with_a_reason():
    assessment = compute_health_assessment([], today=TODAY)
    assert assessment.score == HealthScore.grey
    assert assessment.reasons and assessment.next_actions


# ── Stuck evidence ──────────────────────────────────────────────────────────


def test_pending_and_draft_are_reported_separately():
    """Chasing an assessor and finishing your own draft are different actions,
    so they must never be pooled into one number."""
    items = _balanced() + [
        _item(state="pending", days_ago=90, ident="await-1", form_type="MINI_CEX"),
        _item(state="draft", days_ago=400, ident="draft-1", form_type="JCF"),
    ]

    assessment = compute_health_assessment(items, today=TODAY)

    assert len(assessment.stuck_awaiting) == 1
    assert len(assessment.stuck_drafts) == 1
    assert assessment.stuck_awaiting[0].waits_on_others
    assert not assessment.stuck_drafts[0].waits_on_others


def test_recent_pending_item_is_normal_turnaround():
    items = _balanced() + [_item(state="pending", days_ago=3, ident="fresh")]
    assessment = compute_health_assessment(items, today=TODAY)
    assert assessment.stuck_total == 0
    assert assessment.score == HealthScore.green


def test_completed_evidence_is_never_stuck():
    items = _balanced() + [_item(state="complete", days_ago=900, ident="done")]
    assert compute_health_assessment(items, today=TODAY).stuck_total == 0


# ── Actions ─────────────────────────────────────────────────────────────────


def test_actions_come_from_the_portfolio_not_a_fixed_list():
    """The old report told a doctor with 250 clinical items to "File a CBD from
    a recent supervised case" because the suggestions were fallback strings."""
    items = _balanced() + [
        _item(state="pending", days_ago=120, ident="await-1", form_type="MINI_CEX")
    ]

    actions = compute_health_assessment(items, today=TODAY).next_actions

    assert any("Chase sign-off" in action and "120 days" in action for action in actions)
    assert not any("File a CBD from a recent supervised case" == action for action in actions)


def test_clean_portfolio_gets_a_confirmatory_action_not_an_invented_gap():
    actions = compute_health_assessment(_balanced(), today=TODAY).next_actions
    assert actions == ["Nothing is outstanding — keep filing as you go"]


# ── Report rendering ────────────────────────────────────────────────────────


def _render(items, **kwargs):
    assessment = compute_health_assessment(items, today=TODAY)
    return assessment, format_health_report(
        assessment,
        pathway_label="Training (CCT)",
        month_label="August 2026",
        scanned_count=len(items),
        **kwargs,
    )


def test_report_never_shows_a_verdict_without_reasons():
    _, text = _render(_balanced())
    assert "Well covered" in text
    verdict_line = next(i for i, l in enumerate(text.splitlines()) if "Well covered" in l)
    assert text.splitlines()[verdict_line + 1].startswith("•")


def test_report_leads_with_stuck_counts_and_points_at_the_list():
    items = _balanced() + [
        _item(state="pending", days_ago=200, ident="a", form_type="MINI_CEX"),
        _item(state="draft", days_ago=900, ident="b", form_type="JCF"),
    ]
    assessment, text = _render(items)

    # Counts and ages lead; the items themselves live in the pane.
    assert "1 item waiting on someone else" in text
    assert "1 draft of your own unfinished" in text
    assert "for all 2" in text

    # Kaizen's internal codes mean nothing to a doctor, wherever they appear.
    pane = format_stuck_detail(assessment)
    assert "Mini-CEX" in pane and "MINI_CEX" not in pane
    assert "Journal Club" in pane and "JCF" not in pane


def test_report_never_leaks_clinical_narrative():
    items = _balanced() + [_item(state="pending", days_ago=200, ident="a")]
    _, text = _render(items)
    assert "Clinical narrative" not in text


def test_report_activity_describes_the_portfolio_not_product_usage():
    """The old snapshot counted Portfolio Guru filings and rendered as all
    zeros for a doctor who had filed 191 items in the same year."""
    items = _balanced(10)
    _, text = _render(items)
    assert "60 items in the last 12 months" in text


def test_limited_scan_is_disclosed_and_confidence_stated():
    _, text = _render(_balanced(), limited_view=True)
    assert "Limited view" in text
    assert "Confidence: low" in text


def test_assumed_pathway_is_disclosed():
    _, text = _render(_balanced(), pathway_assumed=True, pathway_name="Training (CCT)")
    assert "Assumed pathway: Training (CCT)" in text


def test_cesr_pathway_block_only_appears_for_cesr():
    readiness = {
        "pathway": "cesr_portfolio",
        "wpba_count": 4,
        "wpba_target": 36,
        "wpba_breakdown": {"dops": 2, "mini_cex": 1, "cbd": 1},
    }
    _, cesr = _render(_balanced(), pathway_readiness=readiness)
    _, training = _render(_balanced(), pathway_readiness={"pathway": "training_arcp"})

    assert "WPBA progress toward 36" in cesr and "4/36" in cesr
    assert "DOPS 2/12" in cesr
    assert "WPBA progress" not in training


def test_domain_detail_shows_recency_not_just_counts():
    items = _balanced(30)
    items = [i for i in items if i.domain != HealthDomain.qi]
    items += [_item(domain=HealthDomain.qi, days_ago=365 * 4, ident=f"q-{n}") for n in range(2)]
    assessment = compute_health_assessment(items, today=TODAY)

    detail = format_domain_detail(assessment, today=TODAY)

    assert "latest" in detail
    assert "thin" in detail and "not current" in detail


def test_stuck_detail_lists_every_item_not_just_the_preview():
    items = _balanced() + [
        _item(state="pending", days_ago=100 + n, ident=f"p{n}", form_type="CBD") for n in range(9)
    ]
    assessment = compute_health_assessment(items, today=TODAY)

    detail = format_stuck_detail(assessment)

    assert detail.count("• ") == 9


def test_stuck_detail_is_reassuring_when_nothing_is_waiting():
    assessment = compute_health_assessment(_balanced(), today=TODAY)
    assert "Nothing waiting" in format_stuck_detail(assessment)


# ── Patterns, links, countdown ──────────────────────────────────────────────


def test_a_form_that_never_completes_is_flagged_as_a_pattern():
    """Three stuck Teaching Observations out of three filed is not three
    incidents; it says that form never gets signed off."""
    items = _balanced(40)
    items += [
        _item(state="pending", days_ago=800 + n, ident=f"to-{n}", form_type="TEACH_OBS")
        for n in range(3)
    ]

    patterns = compute_health_assessment(items, today=TODAY).patterns

    assert any("Teaching Observation: 3 of your 3" in p for p in patterns)


def test_a_common_form_stuck_at_the_normal_rate_is_not_a_pattern():
    """Six unfinished CBDs among hundreds is proportionate, not a finding.
    Flagging it would bury the real signal under noise."""
    items = [_item(ident=f"cbd-{n}", form_type="CBD") for n in range(200)]
    items += [
        _item(state="pending", days_ago=100, ident=f"p-{n}", form_type="CBD") for n in range(6)
    ]

    patterns = compute_health_assessment(items, today=TODAY).patterns

    assert not any("CBD" in p for p in patterns)


def test_a_thin_domain_blocked_by_its_own_unfinished_items_is_called_out():
    """Changes the action from "file more" to "chase what you filed"."""
    items = _balanced(40)
    items = [i for i in items if i.domain != HealthDomain.qi]
    items += [
        _item(domain=HealthDomain.qi, state="pending", days_ago=100, ident=f"q-{n}", form_type="QIAT")
        for n in range(3)
    ]

    patterns = compute_health_assessment(items, today=TODAY).patterns

    assert any("QI looks thin partly because" in p for p in patterns)


def test_main_report_does_not_repeat_the_item_lists():
    """The same item used to appear three times: in the reasons, in a section,
    and again as an action. The lists belong in the drill-down pane."""
    items = _balanced() + [
        _item(state="pending", days_ago=300, ident="a", form_type="MINI_CEX"),
        _item(state="draft", days_ago=900, ident="b", form_type="JCF"),
    ]
    _, text = _render(items)

    assert "Waiting on someone else — " not in text
    assert "Your own unfinished drafts — " not in text
    assert "Everything unfinished" in text


def test_stuck_items_link_to_kaizen():
    """Telling a doctor to chase a form from 2023 and leaving them to find it
    is half a feature. The URL is indexed for every item."""
    items = _balanced() + [_item(state="pending", days_ago=300, ident="a")]
    assessment = compute_health_assessment(items, today=TODAY)

    detail = format_stuck_detail(assessment)

    assert "](https://kaizenep.com/events/view/x)" in detail


def test_review_countdown_is_shown_when_a_date_is_set():
    _, text = _render(_balanced(), review_date=date(2026, 10, 1), today=TODAY)
    assert "weeks to your review (October 2026)" in text


def test_missing_review_date_offers_the_command_that_sets_it():
    """The old report told doctors to add their ARCP month while offering no
    way to do it."""
    _, text = _render(_balanced())
    assert "/arcp" in text


def test_passed_review_date_asks_for_the_next_one():
    _, text = _render(_balanced(), review_date=date(2026, 5, 1), today=TODAY)
    assert "has passed" in text and "/arcp" in text


# ── Curriculum spread ───────────────────────────────────────────────────────


def _tagged(slos, **kwargs):
    item = _item(**kwargs)
    return item.model_copy(update={"slo_numbers": list(slos)})


def test_curriculum_spread_reports_counts_not_a_tick():
    """"12/12 SLOs covered" is true of a portfolio holding 138 items against
    one outcome and 13 against another. The count is the finding."""
    items = [_tagged([6], ident=f"a-{n}") for n in range(40)]
    items += [_tagged([10], ident=f"b-{n}") for n in range(3)]

    assessment = compute_health_assessment(items, today=TODAY)
    detail = format_domain_detail(assessment, today=TODAY)

    assert assessment.slo_counts == {6: 40, 10: 3}
    assert "Strongest SLO6 (40)" in detail
    assert "SLO10 (3)" in detail


def test_untagged_items_are_disclosed_not_silently_dropped():
    """Over half a real portfolio carried no curriculum tag. Without saying so,
    a thin SLO reads as a gap when the evidence may simply be untagged."""
    items = [_tagged([6], ident="a")] + [_item(ident=f"u-{n}") for n in range(5)]

    assessment = compute_health_assessment(items, today=TODAY)
    detail = format_domain_detail(assessment, today=TODAY)

    assert assessment.untagged_items == 5
    assert "5 items carry no curriculum tag" in detail


def test_curriculum_block_is_absent_when_nothing_is_tagged():
    detail = format_domain_detail(compute_health_assessment(_balanced(), today=TODAY), today=TODAY)
    assert "Curriculum spread" not in detail
