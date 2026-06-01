from datetime import UTC, date, datetime

from health_models import EvidenceItem, HealthDomain, HealthProfile, HealthScore, HealthSnapshot, Pathway


RECENT_DAYS = 365
AGEING_DAYS = 365 * 3
STALE_DAYS = 365 * 5


def compute_health_score(items: list[EvidenceItem]) -> HealthScore:
    """Compute a pathway-agnostic health score from domain coverage and evidence age."""
    if not items:
        return HealthScore.grey

    covered_domains = {item.domain for item in items}
    score = _score_from_domain_count(len(covered_domains))

    stale_count = sum(1 for item in items if _age_days(item) > STALE_DAYS)
    if stale_count > len(items) / 2:
        score = _drop_score(score)

    return score


def compute_domain_coverage(items: list[EvidenceItem]) -> dict[HealthDomain, int]:
    """Return evidence counts for every universal health domain."""
    coverage = {domain: 0 for domain in HealthDomain}
    for item in items:
        coverage[item.domain] += 1
    return coverage


def compute_gap_summary(items: list[EvidenceItem]) -> list[str]:
    """Return ordered, human-readable portfolio evidence gaps."""
    if not items:
        return ["No evidence entered yet"]

    gaps: list[str] = []
    coverage = compute_domain_coverage(items)
    for domain in HealthDomain:
        if coverage[domain] == 0:
            gaps.append(f"No {_domain_label(domain)} evidence")

    for domain in HealthDomain:
        domain_items = [item for item in items if item.domain == domain]
        if domain_items and all(_age_days(item) > AGEING_DAYS for item in domain_items):
            gaps.append(f"{_domain_label(domain)} evidence is over 3 years old")

    return gaps


def compute_next_actions(items: list[EvidenceItem], pathway: Pathway) -> list[str]:
    """Suggest concrete next filing actions for the selected pathway."""
    coverage = compute_domain_coverage(items)
    actions: list[str] = []

    missing_domains = [domain for domain in HealthDomain if coverage[domain] == 0]
    for domain in missing_domains[:3]:
        actions.append(_domain_action(domain))

    if pathway == Pathway.training_arcp:
        actions.append("File a CBD from a recent supervised case")
        actions.append("Add evidence before your next ARCP review")
    else:
        actions.append("Add recent DOPS, Mini-CEX, and CBD evidence toward the 36-WPBA CESR target")
        actions.append("Upload recent CPD or consultant report evidence for CESR review")

    return _dedupe(actions)[:5]


def compute_snapshot(profile: HealthProfile, items: list[EvidenceItem]) -> HealthSnapshot:
    """Build a pure in-memory Portfolio Health snapshot for a profile."""
    domain_counts = compute_domain_coverage(items)
    pathway_readiness = _compute_pathway_readiness(items, profile.pathway)

    return HealthSnapshot(
        user_id=profile.user_id,
        computed_at=datetime.now(UTC),
        pathway=profile.pathway,
        health_score=compute_health_score(items),
        domain_counts=domain_counts,
        pathway_readiness=pathway_readiness,
        gap_summary=compute_gap_summary(items),
        next_actions=compute_next_actions(items, profile.pathway),
    )


def _score_from_domain_count(domain_count: int) -> HealthScore:
    if domain_count >= 5:
        return HealthScore.green
    if domain_count >= 3:
        return HealthScore.amber
    if domain_count >= 1:
        return HealthScore.red
    return HealthScore.grey


def _drop_score(score: HealthScore) -> HealthScore:
    if score == HealthScore.green:
        return HealthScore.amber
    if score == HealthScore.amber:
        return HealthScore.red
    if score == HealthScore.red:
        return HealthScore.grey
    return HealthScore.grey


def _age_days(item: EvidenceItem) -> int:
    return (date.today() - item.event_date).days


def _domain_label(domain: HealthDomain) -> str:
    labels = {
        HealthDomain.clinical: "clinical",
        HealthDomain.cpd: "CPD",
        HealthDomain.qi: "QI",
        HealthDomain.teaching: "teaching",
        HealthDomain.leadership: "leadership",
        HealthDomain.reflection: "reflection",
    }
    return labels[domain]


def _domain_action(domain: HealthDomain) -> str:
    actions = {
        HealthDomain.clinical: "File a CBD from a recent case",
        HealthDomain.cpd: "Add a recent CPD course or learning event",
        HealthDomain.qi: "Add an audit or QI project",
        HealthDomain.teaching: "Add a teaching session",
        HealthDomain.leadership: "Add leadership or management evidence",
        HealthDomain.reflection: "Add a reflection log",
    }
    return actions[domain]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


def _compute_pathway_readiness(items: list[EvidenceItem], pathway: Pathway) -> dict[str, object]:
    filed_or_better = {"filed", "reviewed", "accepted"}
    wpba_items = [item for item in items if item.evidence_type == "wpba"]
    recent_items = [item for item in items if _age_days(item) < RECENT_DAYS]

    if pathway == Pathway.training_arcp:
        return {
            "pathway": pathway.value,
            "filed_evidence_count": sum(1 for item in items if item.status in filed_or_better),
            "recent_evidence_count": len(recent_items),
        }

    return {
        "pathway": pathway.value,
        "wpba_count": len(wpba_items),
        "wpba_target": 36,
        "recent_evidence_count": len(recent_items),
    }
