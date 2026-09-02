"""Offline tests for the Portfolio Health assessment and its four views.

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


def _all_views(items):
    assessment = _assess(items)
    return "\n".join([
        format_priorities(assessment, month_label="August 2026", today=TODAY),
        format_actions(assessment),
        format_coverage(assessment, today=TODAY),
        format_curriculum(assessment),
        format_scan_info(assessment, basis=BASIS, today=TODAY),
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


def test_priorities_puts_doctor_controlled_drafts_before_awaiting_signoff():
    items = _balanced() + [
        _item(state="pending", days_ago=900, ident="await-1", form_type="MINI_CEX"),
        _item(state="draft", days_ago=100, ident="draft-1", form_type="JCF"),
    ]

    _, text = _priorities(items)

    assert text.index("draft of your own unfinished") < text.index("item with someone else")
    assert text.count("\n1. ") + text.count("\n2. ") + text.count("\n3. ") <= 3
    assert "not by training or curriculum importance" in text


def test_findings_state_dates_and_never_instruct_a_chase():
    """Nothing scanned says a deadline exists, who has already been asked, or
    whether a 2023 draft is still worth finishing."""
    items = _balanced() + [
        _item(state="pending", days_ago=1112, ident="a", form_type="TEACH_OBS"),
        _item(state="draft", days_ago=900, ident="b", form_type="JCF"),
    ]

    _, text = _priorities(items)

    assert "dated 10 Aug 2023" in text
    for word in ("Chase", "chase", "overdue", "stale", "neglected", "Finish or delete"):
        assert word not in text


def test_clean_portfolio_is_not_given_an_invented_gap():
    assert _assess(_balanced()).next_actions == ["Nothing in this scan is unfinished"]


# ── Priorities ──────────────────────────────────────────────────────────────


def test_priorities_keeps_its_title_and_names_what_the_order_means():
    """"Priorities" without a basis reads as clinical or curriculum importance.
    The order is workflow state and dates, and it says so."""
    items = _balanced() + [_item(state="pending", days_ago=300, ident="a")]
    _, text = _priorities(items)

    assert text.startswith("📍 *Portfolio priorities*")
    assert "not by training or curriculum importance" in text
    assert text.splitlines()[3].startswith("1. ")


def test_partial_scan_shows_one_factual_notice_and_stops_ranking():
    """An incomplete scan cannot rank anything: the item that would have come
    first may not have been read at all."""
    items = _balanced() + [_item(state="pending", days_ago=300, ident="a")]
    _, text = _priorities(items, limited_view=True)

    assert "Partial scan: Portfolio Guru filings only" in text
    assert "Listed, not ranked" in text
    assert "1. " not in text
    assert "not by training or curriculum importance" not in text


def test_unconfirmed_scan_freshness_is_stated_and_also_stops_ranking():
    items = _balanced() + [_item(state="pending", days_ago=300, ident="a")]
    _, text = _priorities(items, scan_is_fresh=False)

    assert "Scan freshness unconfirmed" in text
    assert "1. " not in text


def test_priorities_stays_on_one_phone_screen():
    """Two screens of prose is where the previous report lost the doctor."""
    items = _balanced(40)
    items = [i for i in items if i.domain not in (HealthDomain.qi, HealthDomain.teaching)]
    items += [
        _item(state="pending", days_ago=900 + n, ident=f"p-{n}", form_type="MINI_CEX")
        for n in range(15)
    ]
    _, text = _priorities(items)

    assert len(text.splitlines()) <= 14
    assert len(text) < 700
    assert text.count("\n1. ") + text.count("\n2. ") + text.count("\n3. ") == 3
    assert "\n4. " not in text


def test_priorities_points_at_the_actions_view_rather_than_listing_items():
    """The same Teaching Observation used to appear as a reason, a section and
    an action in one message."""
    items = _balanced() + [
        _item(state="pending", days_ago=300, ident="a", form_type="MINI_CEX"),
        _item(state="draft", days_ago=900, ident="b", form_type="JCF"),
    ]
    _, text = _priorities(items)

    assert "Tap 📌 Actions for all 2" in text
    assert "kaizenep.com" not in text


def test_priorities_never_leaks_clinical_narrative():
    items = _balanced() + [_item(state="pending", days_ago=200, ident="a")]
    _, text = _priorities(items)
    assert "Clinical narrative" not in text


def test_priorities_carries_one_safety_line():
    _, text = _priorities(_balanced())
    assert text.count("A planning aid, not a formal training") == 1


def test_review_countdown_is_shown_when_a_month_is_set():
    _, text = _priorities(_balanced(), review_date=date(2026, 10, 1))
    assert "Next review: October 2026 — 5 weeks away." in text


def test_missing_review_month_points_to_more_navigation():
    _, text = _priorities(_balanced())
    assert (
        "No review month set — open ☰ More and choose Review month" in text
    )


def test_passed_review_month_asks_for_the_next_one():
    _, text = _priorities(_balanced(), review_date=date(2026, 5, 1))
    assert (
        "Review month May 2026 has passed — open ☰ More and choose Review month"
        in text
    )


def test_scan_info_points_at_the_command_because_it_has_no_button():
    """Naming a button that is not on this view sends a doctor hunting."""
    _, text = _scan_info(_balanced())
    assert "No review month set — set it with /arcp" in text
    assert "📅 Review month" not in text


def test_pathway_requirement_counter_appears_only_with_a_verified_overlay():
    """With no overlay the counter renders nothing at all. An empty counter
    would read as an unmet requirement to a doctor whose pathway has no such
    rule."""
    readiness = {
        "pathway": "cesr_portfolio",
        "wpba_count": 4,
        "wpba_target": 36,
        "wpba_breakdown": {"dops": 2, "mini_cex": 1, "cbd": 1},
    }
    _, cesr = _priorities(_balanced(), pathway_readiness=readiness)
    _, training = _priorities(_balanced(), pathway_readiness={"pathway": "training_arcp"})
    _, none_at_all = _priorities(_balanced())

    assert "*Portfolio Pathway requirement*" in cesr
    assert "4/36 WPBAs counted in this scan" in cesr
    assert "DOPS 2/12" in cesr
    assert "requirement" not in training
    assert "requirement" not in none_at_all


# ── Actions ─────────────────────────────────────────────────────────────────


def _many_stuck(count=17):
    items = _balanced()
    items += [
        _item(state="pending", days_ago=1000 - n, ident=f"a-{n:02d}", form_type="MINI_CEX")
        for n in range(10)
    ]
    items += [
        _item(state="draft", days_ago=900 - n, ident=f"d-{n:02d}", form_type="JCF")
        for n in range(count - 10)
    ]
    return items


def test_actions_shows_the_visible_range_of_a_bounded_page():
    assessment = _assess(_many_stuck())

    first = format_action_queue(assessment, "awaiting", page=0)
    second = format_action_queue(assessment, "awaiting", page=1)

    assert action_queue_page_count(assessment, "awaiting") == 2
    assert "Showing 1–5 of 10 awaiting sign-off" in first
    assert "Showing 6–10 of 10 awaiting sign-off" in second
    assert first.count("\n• ") == 5


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

    assert landing.index("*Your drafts — 7*") < landing.index("*Awaiting sign-off — 10*")
    assert landing.count("\n• ") == 6  # Up to three direct-linked examples per queue.
    assert "*Your drafts — 7*" in drafts
    assert "Started by you and not completed." in drafts
    assert "*Awaiting sign-off — 10*" in awaiting
    assert "Submitted and waiting for someone else." in awaiting


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
    assert "worth reviewing before acting" in text
    # The only mention of chasing is the boundary: Portfolio Guru does not.
    boundary = "_Nothing is chased, submitted, edited or deleted for you._"
    assert boundary in text and "chas" not in text.replace(boundary, "")


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


def test_actions_is_reassuring_when_nothing_is_unfinished():
    text = format_actions(_assess(_balanced()))
    assert "Nothing scanned is unfinished" in text
    assert actions_page_count(_assess(_balanced())) == 1


def test_action_queues_paginate_independently_at_five_per_page():
    assessment = _assess(_many_stuck())

    assert action_queue_page_count(assessment, "draft") == 2
    assert action_queue_page_count(assessment, "awaiting") == 2
    assert "Showing 6–7 of 7 drafts" in format_action_queue(
        assessment, "draft", page=1
    )
    assert "Showing 6–10 of 10 awaiting sign-off" in format_action_queue(
        assessment, "awaiting", page=1
    )


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
    assert "2/12 SLOs represented across 43 tagged item(s)" in curriculum
    assert "Largest SLO6 (40)" in curriculum
    assert "SLO10 (3)" in curriculum


def test_twelve_of_twelve_slos_does_not_claim_curriculum_adequacy():
    items = [_tagged([slo], ident=f"slo-{slo}") for slo in range(1, 13)]

    _, coverage = _coverage(items)
    _, curriculum = _curriculum(items)

    for text in (coverage, curriculum):
        assert "12/12 SLOs represented" in text
        assert "presence does not assess adequacy" in text.lower()
        assert "12/12 SLOs covered" not in text


def test_untagged_items_are_disclosed_not_silently_dropped():
    """Without saying so, a small SLO reads as a gap in the doctor's evidence
    when it may only be a gap in their tagging."""
    items = [_tagged([6], ident="a")] + [_item(ident=f"u-{n}") for n in range(5)]

    assessment, curriculum = _curriculum(items)

    # All six are CBDs and one is tagged, so the other five are a real gap.
    assert assessment.untagged_items == 5
    assert "Untagged: 5 item(s)" in curriculum
    assert "may not count toward curriculum coverage" in curriculum


def test_untagged_count_is_stated_even_when_it_is_zero():
    items = [_tagged([6], ident=f"a-{n}") for n in range(4)]
    _, curriculum = _curriculum(items)
    assert "Untagged: 0 items" in curriculum


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


def test_a_form_that_never_completes_is_shown_as_a_pattern_in_coverage():
    """Three unfinished Teaching Observations out of three filed is not three
    incidents; it says that form never gets signed off."""
    items = _balanced(40)
    items += [
        _item(state="pending", days_ago=800 + n, ident=f"to-{n}", form_type="TEACH_OBS")
        for n in range(3)
    ]

    assessment, priorities = _priorities(items)

    assert any("Teaching Observation: 3 of your 3 are unfinished" in p for p in assessment.patterns)
    assert "Teaching Observation: 3 of your 3 are unfinished" in priorities


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
    assert "no item here is described as overdue" in text
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
    assert "5-year evidence window" in cesr
    assert "ESLEs" not in training
