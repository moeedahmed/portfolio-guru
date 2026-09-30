"""Offline tests for the Portfolio Health assessment and its views.

These guard the failures found on a real 501-item portfolio on 2026-08-26,
where the old report said "Green — main evidence domains are covered" and
"Missing domains: None obvious" while 27 items sat unfinished, the oldest for
1112 days, and QI held 7 items against 250 clinical — and the safety rules the
independent review then required: no readiness colour, no unbacked ranking, no
overdue language, no curriculum claim, and pagination a doctor can trust.

No Kaizen, browser, network, or Telegram.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from health_assessment import IMBALANCE_MIN_ITEMS, compute_health_assessment
from health_models import EvidenceItem, HealthDomain
from health_report import (
    action_queue_page_count,
    actions_page_count,
    format_about,
    format_action_queue,
    format_actions,
    format_coverage,
    format_curriculum,
    format_priorities,
    format_scan_info,
    ordered_actions,
)

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




def _tagged(slos, **kwargs):
    item = _item(**kwargs)
    return item.model_copy(update={"slo_numbers": list(slos)})


def _assess(items):
    return compute_health_assessment(items, today=TODAY)


def _priorities(items, **kwargs):
    assessment = _assess(items)
    return assessment, format_priorities(
        assessment, month_label="August 2026", today=TODAY, **kwargs
    )


def _coverage(items):
    assessment = _assess(items)
    return assessment, format_coverage(assessment, today=TODAY)


def _curriculum(items):
    assessment = _assess(items)
    return assessment, format_curriculum(assessment)


BASIS = (
    "*Evidence basis*\n"
    "Scanned: Read-only Kaizen index: 12 visible evidence item(s)\n"
    "Refresh: 26 Aug 2026 09:00 — fresh within 24 hours\n"
    "Window: all indexed Kaizen evidence currently stored\n"
    "Pathway: Training (CCT)\n"
    "Scope: full indexed scan"
)


def _scan_info(items, **kwargs):
    assessment = _assess(items)
    return assessment, format_scan_info(
        assessment, basis=BASIS, today=TODAY, **kwargs
    )


def _about(_items, **kwargs):
    return format_about(basis=BASIS, **kwargs)


def _all_views(items):
    assessment = _assess(items)
    return "\n".join([
        format_priorities(assessment, month_label="August 2026", today=TODAY),
        format_actions(assessment),
        format_coverage(assessment, today=TODAY),
        format_curriculum(assessment),
        format_scan_info(assessment, basis=BASIS, today=TODAY),
        format_about(basis=BASIS),
    ])


# ── No readiness verdict ────────────────────────────────────────────────────


def test_no_view_shows_a_readiness_colour_or_verdict():
    """A traffic light is a readiness claim, and nothing here verifies
    readiness against any pathway's rules. The same evidence means different
    things to an ST4, a CESR applicant and an SAS doctor."""
    items = _balanced(40) + [_item(state="pending", days_ago=400, ident="p1")]
    text = _all_views(items)

    assert not any(colour in text for colour in ("🟢", "🟠", "🔴", "⚪", "🟡"))
    for verdict in ("Well covered", "Needs attention", "Not enough scanned yet", "on track"):
        assert verdict not in text
    assert not hasattr(_assess(items), "score")


def test_imbalance_is_computed_but_not_turned_into_a_largest_smallest_warning():
    """Relative thinness can remain available to assessment consumers, but
    Coverage must not turn it into a misleading largest/smallest comparison."""
    items = _balanced(40)
    items = [i for i in items if i.domain != HealthDomain.qi]
    items += [_item(domain=HealthDomain.qi, ident=f"qi-{n}") for n in range(3)]

    assessment, coverage = _coverage(items)

    assert any(stat.is_thin for stat in assessment.domains)
    assert "QI is your smallest area" not in coverage
    assert "at your largest" not in coverage


def test_stale_domain_is_reported_with_the_date_its_evidence_stops():
    items = _balanced(30)
    items = [i for i in items if i.domain != HealthDomain.teaching]
    items += [
        _item(domain=HealthDomain.teaching, days_ago=365 * 4, ident=f"t-{n}") for n in range(30)
    ]

    _, coverage = _coverage(items)

    assert "Teaching: 30 · 0 · latest Aug 2022" in coverage


def test_domain_comparison_is_suppressed_for_a_small_portfolio():
    """With a dozen items, "QI is thin" says more about the size of the scan
    than about the doctor. The minimum is one explicit number, and the view
    says which."""
    items = [_item(ident=f"c-{n}") for n in range(12)]
    items += [_item(domain=HealthDomain.qi, ident="qi-1")]

    assessment, coverage = _coverage(items)

    assert IMBALANCE_MIN_ITEMS == 20
    assert not assessment.balance_is_comparable
    assert not any(stat.is_thin for stat in assessment.domains)
    assert "*Balance*" not in coverage
    assert "smallest area" not in coverage


def test_empty_portfolio_says_nothing_was_scanned():
    assessment = compute_health_assessment([], today=TODAY)
    assert assessment.next_actions == ["No portfolio evidence has been scanned yet"]
    assert assessment.stuck_total == 0


# ── Stuck evidence ──────────────────────────────────────────────────────────


def test_pending_and_draft_are_reported_separately():
    """Chasing an assessor and finishing your own draft are different actions,
    so they must never be pooled into one number."""
    items = _balanced() + [
        _item(state="pending", days_ago=90, ident="await-1", form_type="MINI_CEX"),
        _item(state="draft", days_ago=400, ident="draft-1", form_type="JCF"),
    ]

    assessment = _assess(items)

    assert len(assessment.stuck_awaiting) == 1
    assert len(assessment.stuck_drafts) == 1
    assert assessment.stuck_awaiting[0].waits_on_others
    assert not assessment.stuck_drafts[0].waits_on_others


def test_recent_pending_item_is_normal_turnaround():
    items = _balanced() + [_item(state="pending", days_ago=3, ident="fresh")]
    assert _assess(items).stuck_total == 0


def test_completed_evidence_is_never_stuck():
    items = _balanced() + [_item(state="complete", days_ago=900, ident="done")]
    assert _assess(items).stuck_total == 0


# ── Findings ────────────────────────────────────────────────────────────────


def test_findings_come_from_the_portfolio_not_a_fixed_list():
    """The old report told a doctor with 250 clinical items to "File a CBD from
    a recent supervised case" because the suggestions were fallback strings."""
    items = _balanced() + [
        _item(state="pending", days_ago=120, ident="await-1", form_type="MINI_CEX")
    ]

    findings = _assess(items).next_actions

    assert findings[0] == "1 item with someone else — oldest a Mini-CEX dated 28 Apr 2026"
    assert not any("File a CBD from a recent supervised case" == f for f in findings)


def test_action_first_landing_puts_doctor_controlled_drafts_before_awaiting_signoff():
    items = _balanced() + [
        _item(state="pending", days_ago=900, ident="await-1", form_type="MINI_CEX"),
        _item(state="draft", days_ago=100, ident="draft-1", form_type="JCF"),
    ]

    _, text = _priorities(items)

    assert text.index("*Review older drafts — 1*") < text.index("*Older items awaiting sign-off — 1*")
    assert "Decide whether each is still worth completing" in text
    assert "Review only if follow-up is still needed" in text


def test_findings_state_dates_and_never_instruct_a_chase():
    """Nothing scanned says a deadline exists, who has already been asked, or
    whether a 2023 draft is still worth finishing."""
    items = _balanced() + [
        _item(state="pending", days_ago=1112, ident="a", form_type="TEACH_OBS"),
        _item(state="draft", days_ago=900, ident="b", form_type="JCF"),
    ]

    _, text = _priorities(items)

    assert "10 Aug 2023" not in text
    for word in ("Chase", "chase", "overdue", "stale", "neglected", "Finish or delete"):
        assert word not in text


def test_clean_portfolio_is_not_given_an_invented_gap():
    assert _assess(_balanced()).next_actions == ["Nothing in this scan is unfinished"]


# ── Action-first landing ────────────────────────────────────────────────────


def test_default_health_text_is_the_two_action_groups_and_trust_line_only():
    items = _balanced() + [_item(state="pending", days_ago=300, ident="a")]
    _, text = _priorities(items)

    assert text.startswith("*What to do next*")
    assert "*No older drafts to review*" in text
    assert "No drafts have been waiting long enough" in text
    assert "*Older items awaiting sign-off — 1*" in text
    assert "Read-only planning aid, not a formal training or appraisal judgement" in text
    for removed in ("Coverage", "Curriculum", "Scan info", "Review month", "More"):
        assert removed not in text


def test_recent_unfinished_items_are_not_misreported_as_absent():
    items = _balanced() + [
        _item(state="draft", days_ago=3, ident="recent-draft"),
        _item(state="pending", days_ago=3, ident="recent-awaiting"),
    ]
    assessment, text = _priorities(items)

    assert assessment.stuck_total == 0
    assert "*No older unfinished items to review*" in text
    assert "waiting long enough to be highlighted here" in text
    assert "Nothing unfinished" not in text
    assert "No drafts or items awaiting sign-off were visible" not in text


def test_partial_scan_keeps_one_explicit_limitation_on_the_landing():
    items = _balanced() + [_item(state="pending", days_ago=300, ident="a")]
    _, text = _priorities(items, limited_view=True)

    assert "Partial scan: Portfolio Guru filings only" in text
    assert "1. " not in text


def test_partial_index_scan_keeps_an_explicit_limitation_on_the_landing():
    _, text = _priorities(_balanced(), partial_scan=True)

    assert "Partial scan: some Kaizen evidence may be missing" in text


def test_unconfirmed_scan_freshness_is_explicit_on_the_landing():
    items = _balanced() + [_item(state="pending", days_ago=300, ident="a")]
    _, text = _priorities(items, scan_is_fresh=False)

    assert "Scan freshness unconfirmed" in text
    assert "1. " not in text


def test_action_first_landing_stays_on_one_phone_screen():
    """Two screens of prose is where the previous report lost the doctor."""
    items = _balanced(40)
    items = [i for i in items if i.domain not in (HealthDomain.qi, HealthDomain.teaching)]
    items += [
        _item(state="pending", days_ago=900 + n, ident=f"p-{n}", form_type="MINI_CEX")
        for n in range(15)
    ]
    _, text = _priorities(items)

    assert len(text.splitlines()) <= 11
    assert len(text) < 550


def test_action_first_landing_uses_counts_not_item_detail():
    items = _balanced() + [
        _item(state="pending", days_ago=300, ident="a", form_type="MINI_CEX"),
        _item(state="draft", days_ago=900, ident="b", form_type="JCF"),
    ]
    _, text = _priorities(items)

    assert "*Review older drafts — 1*" in text
    assert "*Older items awaiting sign-off — 1*" in text
    assert "kaizenep.com" not in text


def test_action_first_landing_never_leaks_clinical_narrative():
    items = _balanced() + [_item(state="pending", days_ago=200, ident="a")]
    _, text = _priorities(items)
    assert "Clinical narrative" not in text


def test_action_first_landing_carries_one_concise_trust_line():
    _, text = _priorities(_balanced())
    assert text.count("Read-only planning aid, not a formal training or appraisal judgement") == 1


def test_system_analysis_and_review_settings_do_not_appear_on_the_landing():
    readiness = {
        "pathway": "cesr_portfolio",
        "wpba_count": 4,
        "wpba_target": 36,
        "wpba_breakdown": {"dops": 2, "mini_cex": 1, "cbd": 1},
    }
    _, text = _priorities(
        _balanced(),
        review_date=date(2026, 10, 1),
        pathway_readiness=readiness,
    )

    assert "Next review" not in text
    assert "Review month" not in text
    assert "Portfolio Pathway requirement" not in text
    assert "WPBAs counted" not in text


def test_scan_info_points_at_the_command_because_it_has_no_button():
    """Naming a button that is not on this view sends a doctor hunting."""
    _, text = _scan_info(_balanced())
    assert "No review month set — set it with /arcp" in text
    assert "📅 Review month" not in text


# ── Actions ─────────────────────────────────────────────────────────────────


def _many_stuck(count=17, *, drafts=None, awaiting=10):
    items = _balanced()
    items += [
        _item(state="pending", days_ago=1000 - n, ident=f"a-{n:02d}", form_type="MINI_CEX")
        for n in range(awaiting)
    ]
    items += [
        _item(state="draft", days_ago=900 - n, ident=f"d-{n:02d}", form_type="JCF")
        for n in range(drafts if drafts is not None else count - 10)
    ]
    return items


def test_actions_shows_the_visible_range_of_a_bounded_page():
    assessment = _assess(_many_stuck(awaiting=30))
    first = format_action_queue(assessment, "awaiting", page=0)
    second = format_action_queue(assessment, "awaiting", page=1)
    assert action_queue_page_count(assessment, "awaiting") == 2
    assert "*Waiting on an assessor · 30*" in first
    assert "Page 1 of 2." in first and "Page 2 of 2." in second
    assert "Showing" not in first
    assert first.count("\n• ") == 25
    assert second.count("\n• ") == 5


def test_actions_pages_partition_the_items_with_no_gap_or_repeat():
    assessment = _assess(_many_stuck())
    pages = [
        format_action_queue(assessment, queue, page=page)
        for queue in ("draft", "awaiting")
        for page in range(action_queue_page_count(assessment, queue))
    ]

    listed = [line for page in pages for line in page.splitlines() if line.startswith("• ")]

    assert len(listed) == 17
    assert len(set(listed)) == 17


def test_actions_order_is_stable_whatever_order_the_evidence_arrives_in():
    """Items filed on the same day must not swap places between renders, or a
    doctor paging through Actions sees page 2 repeat page 1."""
    same_day = [
        _item(state="pending", days_ago=300, ident=f"s-{n}", form_type="CBD")
        for n in range(6)
    ]
    forward = ordered_actions(_assess(_balanced() + same_day))
    backward = ordered_actions(_assess(_balanced() + list(reversed(same_day))))

    assert [item.id for _group, item in forward] == [item.id for _group, item in backward]


def test_actions_separates_awaiting_from_your_own_drafts():
    assessment = _assess(_many_stuck())

    landing = format_actions(assessment)
    drafts = format_action_queue(assessment, "draft", page=0)
    awaiting = format_action_queue(assessment, "awaiting", page=0)

    assert landing.index("*Older drafts — 7*") < landing.index("*Awaiting sign-off — 10*")
    assert landing.count("\n• ") == 6  # Up to three direct-linked examples per queue.
    assert "*To finish and send · 7*" in drafts
    assert "Started by you, not yet sent to an assessor." in drafts
    assert "*Waiting on an assessor · 10*" in awaiting
    assert "You sent these. An assessor still needs to complete them." in awaiting
    # The read-only boundary lives on the main screen and About, not on
    # every queue page.
    for page in (drafts, awaiting):
        assert "Nothing is chased" not in page
        assert "planning aid" not in page


def test_actions_names_items_by_form_and_exact_date_without_deadline_language():
    items = _balanced() + [
        _item(state="pending", days_ago=1112, ident="a", form_type="MINI_CEX"),
        _item(state="draft", days_ago=900, ident="b", form_type="JCF"),
    ]
    assessment = _assess(items)
    text = "\n".join(
        [
            format_actions(assessment),
            format_action_queue(assessment, "draft"),
            format_action_queue(assessment, "awaiting"),
        ]
    )

    # Kaizen's internal codes mean nothing to a doctor, wherever they appear.
    assert "Mini-CEX — 10 Aug 2023" in text and "MINI_CEX" not in text
    assert "Journal Club" in text and "JCF" not in text
    for word in ("overdue", "days waiting", "Chase", "neglected"):
        assert word not in text
    assert "over a year old: check they're still needed" in text
    # The only mention of chasing is the boundary: Portfolio Guru does not.
    boundary = "_Nothing is chased, submitted, edited or deleted for you._"
    assert "chas" not in text.replace(boundary, "")


def test_actions_link_every_item_to_kaizen():
    """Naming a form from 2023 and leaving a doctor to find it is half a
    feature. The URL is indexed for every item."""
    items = _balanced() + [_item(state="pending", days_ago=300, ident="a")]
    assert "](https://kaizenep.com/events/view/x)" in format_actions(_assess(items))


def test_actions_page_beyond_the_end_falls_back_to_the_last_real_page():
    """A stale button on an old message must land on evidence, not an error."""
    assessment = _assess(_many_stuck())
    assert format_action_queue(assessment, "draft", page=99) == format_action_queue(
        assessment, "draft", page=1
    )
    assert format_action_queue(assessment, "awaiting", page=-4) == format_action_queue(
        assessment, "awaiting", page=0
    )


def test_actions_does_not_misstate_recent_unfinished_items_as_complete():
    text = format_actions(_assess(_balanced()))
    assert "No older unfinished items were highlighted" in text
    assert "every item has completed" not in text
    assert actions_page_count(_assess(_balanced())) == 1


def test_action_queues_paginate_independently_at_25_per_page():
    assessment = _assess(_many_stuck(drafts=27, awaiting=30))
    assert action_queue_page_count(assessment, "draft") == 2
    assert action_queue_page_count(assessment, "awaiting") == 2
    drafts_2 = format_action_queue(assessment, "draft", page=1)
    awaiting_2 = format_action_queue(assessment, "awaiting", page=1)
    assert "*To finish and send · 27*" in drafts_2 and "Page 2 of 2." in drafts_2
    assert drafts_2.count("\n• ") == 2
    assert "*Waiting on an assessor · 30*" in awaiting_2 and "Page 2 of 2." in awaiting_2
    assert awaiting_2.count("\n• ") == 5


# ── Coverage ────────────────────────────────────────────────────────────────


def test_coverage_separates_a_live_domain_from_a_historical_one():
    """250 items built years ago is not the same portfolio as 250 with most of
    them this year, and a total alone cannot tell them apart."""
    items = [_item(days_ago=30, ident=f"new-{n}") for n in range(5)]
    items += [_item(days_ago=365 * 2, ident=f"old-{n}") for n in range(20)]
    items += [
        _item(domain=HealthDomain.teaching, days_ago=365 * 2, ident=f"t-{n}")
        for n in range(2)
    ]

    assessment, coverage = _coverage(items)

    clinical = next(s for s in assessment.domains if s.domain == HealthDomain.clinical)
    assert clinical.count == 25 and clinical.recent_count == 5
    assert "Clinical: 25 · 5" in coverage
    assert "Teaching: 2 · 0 · latest Aug 2024" in coverage
    assert "QI: none scanned" in coverage


def test_coverage_states_six_category_denominator_and_outside_count():
    items = _balanced(2) + [
        _item(domain=HealthDomain.unclassified, ident=f"outside-{index}")
        for index in range(3)
    ]

    assessment, coverage = _coverage(items)

    assert assessment.outside_core_items == 3
    assert "3 of 15 scanned items sit outside these six core categories" in coverage
    assert "Clinical: 2 · 2" in coverage


def test_coverage_recent_counts_describe_the_portfolio_not_product_usage():
    """Recency is derived from the scanned portfolio, not bot usage."""
    _, coverage = _coverage(_balanced(10))
    assert "Clinical: 10 · 10" in coverage


def test_coverage_cannot_be_mistaken_for_a_curriculum_minimum():
    _, coverage = _coverage(_balanced(40))
    assert "Nothing here is a curriculum requirement or a minimum" in coverage


def test_coverage_removes_largest_versus_smallest_domain_warning():
    items = _balanced(40)
    items = [item for item in items if item.domain != HealthDomain.qi]
    items += [_item(domain=HealthDomain.qi, ident=f"qi-{n}") for n in range(3)]

    _, coverage = _coverage(items)

    assert "smallest area" not in coverage
    assert "at your largest" not in coverage
    assert "Compared with your own portfolio" not in coverage


def test_curriculum_spread_reports_counts_over_tagged_items_only():
    """"12/12 SLOs covered" is true of a portfolio holding 138 items against
    one outcome and 13 against another. The count is the finding."""
    items = [_tagged([6], ident=f"a-{n}") for n in range(40)]
    items += [_tagged([10], ident=f"b-{n}") for n in range(3)]

    assessment, curriculum = _curriculum(items)

    assert assessment.slo_counts == {6: 40, 10: 3}
    assert assessment.tagged_items == 43
    assert "Tagged evidence per SLO, from 43 tagged items." in curriculum
    rows = curriculum.split("```")[1].splitlines()
    assert any(row.startswith(" 6 Procedures") and row.endswith(" 40") for row in rows)
    assert any(row.startswith("10 Research") and row.endswith("  3") for row in rows)
    assert any(row.startswith(" 1 Adult patients") and row.endswith("  0") for row in rows)
    assert "item(s)" not in curriculum


def test_twelve_of_twelve_slos_does_not_claim_curriculum_adequacy():
    items = [_tagged([slo], ident=f"slo-{slo}") for slo in range(1, 13)]

    _, coverage = _coverage(items)
    _, curriculum = _curriculum(items)

    assert "12/12 SLOs represented" in coverage
    assert "presence does not assess adequacy" in coverage.lower()
    assert "from 12 tagged items" in curriculum
    assert "Counts tags only, not whether evidence is enough" in curriculum
    assert "None yet" not in curriculum
    for text in (coverage, curriculum):
        assert "12/12 SLOs covered" not in text


def test_untagged_items_are_disclosed_not_silently_dropped():
    """Without saying so, a small SLO reads as a gap in the doctor's evidence
    when it may only be a gap in their tagging."""
    items = [_tagged([6], ident="a")] + [_item(ident=f"u-{n}") for n in range(5)]

    assessment, curriculum = _curriculum(items)

    # All six are CBDs and one is tagged, so the other five are a real gap.
    assert assessment.untagged_items == 5
    assert "*Untagged · 5 items*, not counted above" in curriculum
    assert "Add SLO tags in Kaizen so they count." in curriculum


def test_untagged_count_is_stated_even_when_it_is_zero():
    items = [_tagged([6], ident=f"a-{n}") for n in range(4)]
    _, curriculum = _curriculum(items)
    assert "*Untagged:* none" in curriculum


def test_untagged_count_excludes_forms_that_never_carry_tags():
    """MSF, e-learning, exams and uploads cannot be KC-tagged. Counting them as
    untagged turned a structural fact into an alarming number — 247 rather than
    the 158 that actually represent a gap."""
    items = [
        _tagged([3], ident="ref-tagged", form_type="REFLECT_LOG"),
        _item(ident="ref-untagged", form_type="REFLECT_LOG"),
        _item(ident="msf-1", form_type="MSF"),
        _item(ident="msf-2", form_type="MSF"),
    ]

    assessment = _assess(items)

    # Only the untagged reflection counts: MSF is never tagged in this portfolio.
    assert assessment.untagged_items == 1


def test_untagged_disclosure_names_the_forms_to_go_and_fix():
    """A count says there is a problem; the forms say where to start."""
    items = [_tagged([3], ident="a", form_type="REFLECT_LOG")]
    items += [_item(ident=f"r-{n}", form_type="REFLECT_LOG") for n in range(4)]
    items += [_tagged([3], ident="p", form_type="PROC_LOG")]
    items += [_item(ident=f"p-{n}", form_type="PROC_LOG") for n in range(2)]

    assessment, curriculum = _curriculum(items)

    assert assessment.untagged_by_form == {"REFLECT_LOG": 4, "PROC_LOG": 2}
    assert "Reflective Log 4" in curriculum
    assert "Procedure Log 2" in curriculum


def test_curriculum_block_is_absent_when_nothing_is_tagged():
    _, coverage = _coverage(_balanced())
    assert "Curriculum spread" not in coverage


def test_a_form_that_never_completes_remains_an_assessment_pattern_not_landing_copy():
    """Three unfinished Teaching Observations out of three filed is not three
    incidents; it says that form never gets signed off."""
    items = _balanced(40)
    items += [
        _item(state="pending", days_ago=800 + n, ident=f"to-{n}", form_type="TEACH_OBS")
        for n in range(3)
    ]

    assessment, priorities = _priorities(items)

    assert any("Teaching Observation: 3 of your 3 are unfinished" in p for p in assessment.patterns)
    assert "Teaching Observation: 3 of your 3 are unfinished" not in priorities


def test_a_common_form_stuck_at_the_normal_rate_is_not_a_pattern():
    """Six unfinished CBDs among hundreds is proportionate, not a finding.
    Flagging it would bury the real signal under noise."""
    items = [_item(ident=f"cbd-{n}", form_type="CBD") for n in range(200)]
    items += [
        _item(state="pending", days_ago=100, ident=f"p-{n}", form_type="CBD") for n in range(6)
    ]

    assert not any("CBD" in p for p in _assess(items).patterns)


def test_a_small_domain_held_back_by_unfinished_items_is_called_out_neutrally():
    """Changes the reading from "you have no QI" to "your QI is unfinished"
    without instructing a chase."""
    items = _balanced(40)
    items = [i for i in items if i.domain != HealthDomain.qi]
    items += [
        _item(domain=HealthDomain.qi, state="pending", days_ago=100, ident=f"q-{n}", form_type="QIAT")
        for n in range(3)
    ]

    patterns = _assess(items).patterns

    assert any(
        "QI looks small partly because 3 of its items are unfinished" in p for p in patterns
    )
    assert not any("chase" in p.lower() for p in patterns)


# ── Scan info ───────────────────────────────────────────────────────────────


def test_scan_info_carries_the_basis_review_timing_and_limits():
    _, text = _scan_info(_balanced(), review_date=date(2026, 10, 1))

    assert text.startswith("🔎 *Scan info*")
    assert "Confidence:" not in text
    assert "Scanned: Read-only Kaizen index: 12 visible evidence item(s)" in text
    assert "Refresh: 26 Aug 2026 09:00 — fresh within 24 hours" in text
    assert "Next review: October 2026" in text
    assert "*What this cannot see*" in text
    assert "Open lists use the Kaizen workflow states visible to this scan" in text
    assert "overdue" not in text.lower()
    assert "category and SLO counts are inventory, not a requirement" in text
    assert "classification is not certified" in text.lower()
    assert "curriculum adequacy is not certified" in text.lower()


def test_scan_info_discloses_a_limited_view():
    _, text = _scan_info(_balanced(), limited_view=True)
    assert "Limited view: based on Portfolio Guru filings only" in text


def test_scan_info_holds_the_fuller_pathway_expectations():
    readiness = {"pathway": "cesr_portfolio", "wpba_count": 4, "wpba_target": 36}
    _, cesr = _scan_info(_balanced(), pathway_readiness=readiness)
    _, training = _scan_info(_balanced())

    assert "ESLEs across core specialties" in cesr
    assert "6-year evidence window" in cesr
    assert "ESLEs" not in training


# ── About ───────────────────────────────────────────────────────────────────


def test_about_contains_only_the_information_needed_to_trust_health():
    text = _about(_balanced())

    assert text.startswith("ℹ️ *About this report*")
    assert "Read 12 items from your Kaizen." in text
    assert "Last refresh: 26 Aug 2026 09:00 — fresh within 24 hours" in text
    assert "Can misread an item" in text
    assert "Never edits, sends or deletes" in text
    # Said once, not twice.
    assert text.count("not an ARCP or appraisal judgement") == 1
    # A full scan's scope is the default and is jargon to a doctor.
    for removed in (
        "Domains — total", "Curriculum tags", "Review timing", "WPBAs",
        "Read-only Kaizen index", "Scope:", "item(s)",
    ):
        assert removed not in text


def test_about_keeps_partial_and_unconfirmed_freshness_limits_explicit():
    partial_basis = (
        "*Evidence basis*\n"
        "Scanned: Portfolio Guru filing history only: 3 visible evidence item(s)\n"
        "Refresh: no Kaizen refresh available; this is a partial local view\n"
        "Scope: partial — the Kaizen index was unavailable"
    )
    partial = format_about(
        basis=partial_basis,
        limited_view=True,
        scan_is_fresh=False,
    )
    stale = format_about(basis=BASIS, scan_is_fresh=False)

    assert "partial" in partial.lower()
    assert "Partial scan: the Kaizen index was unavailable." in partial
    assert "Read 3 items filed through Portfolio Guru only." in partial
    assert "Freshness unconfirmed: recent Kaizen activity may be missing" in stale


def test_open_queues_include_recent_items_and_keep_stuck_threshold_and_total_order():
    items = [
        _item(state=state, days_ago=age, ident=f'{state}-{age}-{n}')
        for state in ('draft', 'pending')
        for age in (0, 20, 21, 365, 366)
        for n in (1, 0)
    ] + [_item(domain=HealthDomain.unclassified, ident='outside')]
    assessment = _assess(items)
    assert assessment.scanned_items == 21
    assert assessment.total_items == 20
    for opened, stuck in ((assessment.open_drafts, assessment.stuck_drafts),
                          (assessment.open_awaiting, assessment.stuck_awaiting)):
        assert len(opened) == 10
        assert len(stuck) == 6
        assert stuck == [item for item in opened if item.days_waiting >= 21]
        assert opened == sorted(opened, key=lambda s: (-s.days_waiting, s.form_type or '', s.id))


def test_queue_25_item_pages_group_by_age_with_global_band_counts():
    items = [
        _item(state='draft', days_ago=age, ident=f'{age}-{n:02}')
        for age, count in ((366, 10), (365, 10), (20, 10))
        for n in range(count)
    ]
    assessment = _assess(items)
    assert action_queue_page_count(assessment, 'draft') == 2
    first = format_action_queue(assessment, 'draft')
    second = format_action_queue(assessment, 'draft', page=1)
    assert first.count('• [') == 25
    assert second.count('• [') == 5
    assert '*To finish and send · 30*' in first
    assert 'Page 1 of 2.' in first and 'Page 2 of 2.' in second
    for band in ('Over a year old', 'Last 12 months', 'Last 3 weeks'):
        assert f'*{band} · 10*' in first
    assert '*Last 3 weeks · 10*' in second
    assert '*Last 12 months' not in second and '*Over a year old' not in second
    assert 'CBD · ' in second
    assert 'Clinical narrative' not in first + second
    assert len(first) < 4096
    single = format_action_queue(_assess(items[-1:]), 'draft')
    assert 'Page ' not in single
    no_url = items[-1].model_copy(update={'source_ref': None})
    assert '• CBD · ' in format_action_queue(_assess([no_url]), 'draft')
    awaiting = format_action_queue(_assess([_item(state='pending', days_ago=1)]), 'awaiting')
    assert '*Waiting on an assessor · 1*' in awaiting
    assert "Kaizen doesn't show if an assessor has opened it" in awaiting


def test_slo_map_shows_all_twelve_scaled_bars_and_remaining_untagged_forms():
    from health_assessment import HealthAssessment
    assessment = HealthAssessment(
        slo_counts={1: 8, 2: 4, 3: 1}, tagged_items=10,
        untagged_items=20, untagged_by_form={'EDU_ACT': 8, 'REFLECT_LOG': 5, 'PROC_LOG': 4, 'CBD': 3},
    )
    text = format_curriculum(assessment)
    rows = text.split('```')[1].strip('\n').splitlines()
    assert len(rows) == 12
    assert [int(row.split()[0]) for row in rows] == list(range(1, 13))
    assert '████████' in rows[0]
    assert '████░░░░' in rows[1]
    assert '█░░░░░░░' in rows[2]
    assert rows[3].endswith('  0') and '░░░░░░░░' in rows[3]
    assert not any(char in text.split('```')[1] for char in '*_')
    assert 'from 10 tagged items' in text
    assert '*Untagged · 20 items*, not counted above' in text
    assert 'other 3' in text
    assert len(text) < 4096
    empty = format_curriculum(HealthAssessment(untagged_items=3, untagged_by_form={'CBD': 3}))
    assert 'No tagged evidence yet' in empty and 'Untagged · 3 items' in empty
    assert len(empty.split('```')[1].strip('\n').splitlines()) == 12


def test_about_discloses_outside_core_items_and_trainee_only_limits():
    text = format_about(basis=BASIS, scanned_items=507, core_items=460, trainee=True)
    assert '*About this report*' in text
    assert '460 sit in the six main categories' in text
    assert 'other 47 are counted but not categorised' in text
    assert 'Form R or SLO 6 procedure sign-offs' in text
    assert 'Form R' not in format_about(basis=BASIS, scanned_items=12, core_items=12)
    assert 'other 0' not in format_about(basis=BASIS, scanned_items=12, core_items=12)
