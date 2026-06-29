from datetime import UTC, date, datetime, timedelta

from health_engine import (
    case_history_to_evidence_items,
    compute_domain_coverage,
    compute_gap_summary,
    compute_health_score,
    compute_next_actions,
    compute_snapshot,
)
from health_models import EvidenceItem, HealthDomain, HealthProfile, HealthScore, HealthSnapshot, Pathway


def _item(domain: HealthDomain, days_old: int = 30, evidence_id: str | None = None) -> EvidenceItem:
    now = datetime.now(UTC)
    return EvidenceItem(
        id=evidence_id or f"evidence-{domain.value}-{days_old}",
        user_id="user-1",
        domain=domain,
        evidence_type="wpba" if domain == HealthDomain.clinical else "other",
        form_type="CBD" if domain == HealthDomain.clinical else None,
        title=f"{domain.value} evidence",
        summary="A concise evidence summary",
        event_date=date.today() - timedelta(days=days_old),
        source="manual_entry",
        source_ref=None,
        status="filed",
        created_at=now,
        updated_at=now,
    )


def _profile(pathway: Pathway = Pathway.training_arcp) -> HealthProfile:
    now = datetime.now(UTC)
    return HealthProfile(
        user_id="user-1",
        pathway=pathway,
        pathway_config={"training_stage": "ST4"} if pathway == Pathway.training_arcp else {},
        created_at=now,
        updated_at=now,
    )


def test_empty_items_are_grey_with_zero_domains_and_no_evidence_gap():
    assert compute_health_score([]) == HealthScore.grey
    assert compute_domain_coverage([]) == {domain: 0 for domain in HealthDomain}
    assert "No evidence entered yet" in compute_gap_summary([])


def test_items_in_six_domains_are_green():
    items = [_item(domain) for domain in HealthDomain]

    assert compute_health_score(items) == HealthScore.green


def test_items_in_three_domains_are_amber():
    items = [
        _item(HealthDomain.clinical),
        _item(HealthDomain.cpd),
        _item(HealthDomain.qi),
    ]

    assert compute_health_score(items) == HealthScore.amber


def test_items_in_one_domain_are_red():
    assert compute_health_score([_item(HealthDomain.clinical)]) == HealthScore.red


def test_stale_items_pull_score_down():
    stale_items = [_item(domain, days_old=365 * 6) for domain in HealthDomain]

    assert compute_health_score(stale_items) == HealthScore.amber


def test_domain_coverage_counts_are_correct():
    items = [
        _item(HealthDomain.clinical, evidence_id="clinical-1"),
        _item(HealthDomain.clinical, evidence_id="clinical-2"),
        _item(HealthDomain.teaching),
    ]

    coverage = compute_domain_coverage(items)

    assert coverage[HealthDomain.clinical] == 2
    assert coverage[HealthDomain.teaching] == 1
    assert coverage[HealthDomain.qi] == 0


def test_gap_summary_flags_empty_domains_and_stale_evidence():
    items = [
        _item(HealthDomain.clinical),
        _item(HealthDomain.qi, days_old=365 * 4),
    ]

    gaps = compute_gap_summary(items)

    assert "No teaching evidence" in gaps
    assert "No CPD evidence" in gaps
    assert "QI evidence is over 3 years old" in gaps


def test_next_actions_differ_between_training_and_cesr_pathways():
    items = [_item(HealthDomain.clinical)]

    arcp_actions = compute_next_actions(items, Pathway.training_arcp)
    cesr_actions = compute_next_actions(items, Pathway.cesr_portfolio)

    assert arcp_actions != cesr_actions
    assert any("ARCP" in action for action in arcp_actions)
    assert any("CESR" in action for action in cesr_actions)


def test_compute_snapshot_produces_valid_health_snapshot():
    items = [
        _item(HealthDomain.clinical),
        _item(HealthDomain.cpd),
        _item(HealthDomain.teaching),
    ]

    snapshot = compute_snapshot(_profile(), items)

    assert isinstance(snapshot, HealthSnapshot)
    assert snapshot.user_id == "user-1"
    assert snapshot.pathway == Pathway.training_arcp
    assert snapshot.health_score == HealthScore.amber
    assert snapshot.domain_counts[HealthDomain.clinical] == 1
    assert snapshot.pathway_readiness["pathway"] == Pathway.training_arcp.value
    assert snapshot.gap_summary
    assert 3 <= len(snapshot.next_actions) <= 5


def test_unclassified_items_do_not_improve_health_score():
    # One real clinical domain plus several unclassified rows must stay red:
    # unclassified evidence cannot fake additional domain coverage.
    items = [
        _item(HealthDomain.clinical),
        _item(HealthDomain.unclassified, evidence_id="unc-1"),
        _item(HealthDomain.unclassified, evidence_id="unc-2"),
        _item(HealthDomain.unclassified, evidence_id="unc-3"),
    ]

    assert compute_health_score(items) == HealthScore.red


def test_unclassified_only_evidence_stays_grey():
    items = [_item(HealthDomain.unclassified, evidence_id=f"unc-{i}") for i in range(5)]

    # No core domains covered → grey, exactly as if there were no evidence at all.
    # Unclassified rows must never push the score above the floor.
    assert compute_health_score(items) == HealthScore.grey


def test_unclassified_is_not_reported_as_a_missing_domain():
    items = [_item(HealthDomain.clinical), _item(HealthDomain.unclassified, evidence_id="unc-1")]

    gaps = compute_gap_summary(items)

    assert not any("unclassified" in gap.lower() for gap in gaps)


def test_unclassified_appears_in_domain_coverage_counts():
    items = [
        _item(HealthDomain.unclassified, evidence_id="unc-1"),
        _item(HealthDomain.unclassified, evidence_id="unc-2"),
    ]

    coverage = compute_domain_coverage(items)

    assert coverage[HealthDomain.unclassified] == 2


def test_unclassified_is_not_offered_as_a_next_action():
    items = [_item(HealthDomain.clinical), _item(HealthDomain.unclassified, evidence_id="unc-1")]

    actions = compute_next_actions(items, Pathway.training_arcp)

    assert not any("unclassified" in action.lower() for action in actions)


def test_case_history_to_evidence_items_maps_form_domains_statuses_and_sources():
    history = [
        {"form_type": "CBD", "filed_at": "2026-05-20 10:15:00", "status": "filed", "telegram_user_id": 123},
        {"form_type": "QIAT", "filed_at": "2026-05-19", "status": "failed", "telegram_user_id": 123},
        {"form_type": "TEACH_OBS", "filed_at": "2026-05-18", "status": "draft", "telegram_user_id": 123},
        {"form_type": "EDU_ACT", "filed_at": "2026-05-18", "status": "filed", "telegram_user_id": 123},
        {"form_type": "UNKNOWN_FORM", "filed_at": "2026-05-17", "status": "filed", "telegram_user_id": 123},
    ]

    items = case_history_to_evidence_items(history)

    assert items[0].domain == HealthDomain.clinical
    assert items[0].evidence_type == "wpba"
    assert items[0].status == "filed"
    assert items[0].source == "kaizen_filed"
    assert items[1].domain == HealthDomain.qi
    assert items[1].evidence_type == "audit"
    assert items[1].status == "needs_work"
    assert items[1].source == "pg_draft"
    assert items[2].domain == HealthDomain.teaching
    assert items[2].source == "pg_draft"
    assert items[3].domain == HealthDomain.cpd
    assert items[3].evidence_type == "course"
    assert items[4].domain == HealthDomain.unclassified
