from datetime import UTC, date, datetime
from typing import Any

from health_models import (
    CORE_DOMAINS,
    EvidenceItem,
    HealthDomain,
    HealthProfile,
    HealthScore,
    HealthSnapshot,
    Pathway,
)


RECENT_DAYS = 365
AGEING_DAYS = 365 * 3
STALE_DAYS = 365 * 5

FORM_TYPE_TO_DOMAIN = {
    "CBD": HealthDomain.clinical,
    "DOPS": HealthDomain.clinical,
    "MINI_CEX": HealthDomain.clinical,
    "LAT": HealthDomain.clinical,
    "ACAT": HealthDomain.clinical,
    "ACAF": HealthDomain.clinical,
    "PROC_LOG": HealthDomain.clinical,
    "US_CASE": HealthDomain.clinical,
    "ESLE": HealthDomain.clinical,
    "ESLE_ASSESS": HealthDomain.clinical,
    "ESLE_PART1_2": HealthDomain.clinical,
    "MSF": HealthDomain.clinical,
    "QIAT": HealthDomain.qi,
    "AUDIT": HealthDomain.qi,
    "STAT": HealthDomain.teaching,
    "TEACH_OBS": HealthDomain.teaching,
    "TEACH": HealthDomain.teaching,
    "TEACH_CONFID": HealthDomain.teaching,
    "EDU_ACT": HealthDomain.cpd,
    "FORMAL_COURSE": HealthDomain.cpd,
    "SDL": HealthDomain.reflection,
    "REFLECT_LOG": HealthDomain.reflection,
    "PDP": HealthDomain.reflection,
    "JCF": HealthDomain.reflection,
    "COMPLAINT": HealthDomain.leadership,
    "SERIOUS_INC": HealthDomain.leadership,
    "SERIOUS_INCIDENT": HealthDomain.leadership,
    "CLIN_GOV": HealthDomain.leadership,
    "APPRAISAL": HealthDomain.leadership,
}

WPBA_FORM_TYPES = {"CBD", "DOPS", "MINI_CEX", "LAT", "ACAT", "ACAF", "PROC_LOG", "US_CASE", "ESLE", "ESLE_ASSESS", "ESLE_PART1_2"}


def compute_health_score(items: list[EvidenceItem]) -> HealthScore:
    """Compute a pathway-agnostic health score from domain coverage and evidence age."""
    if not items:
        return HealthScore.grey

    # Only core domains count toward coverage; unclassified evidence must never
    # lift the score, otherwise unknown/file rows would fake portfolio breadth.
    covered_domains = {item.domain for item in items if item.domain in CORE_DOMAINS}
    score = _score_from_domain_count(len(covered_domains))

    stale_count = sum(1 for item in items if _age_days(item) > STALE_DAYS)
    if stale_count > len(items) / 2:
        score = _drop_score(score)

    return score


def case_history_to_evidence_items(case_history: list[dict]) -> list[EvidenceItem]:
    """Convert existing case_history records to EvidenceItems for the health engine."""
    items: list[EvidenceItem] = []
    now = datetime.now(UTC)

    for index, record in enumerate(case_history):
        form_type = _normalise_form_type(record.get("form_type"))
        status = str(record.get("status") or "").strip().lower()
        event_date = _parse_case_date(record.get("filed_at") or record.get("event_date"))
        evidence_status = _evidence_status_from_case_status(status)
        source = "kaizen_filed" if evidence_status in {"filed", "reviewed", "accepted"} else "pg_draft"
        source_ref = record.get("id") or record.get("source_ref")

        items.append(
            EvidenceItem(
                id=str(source_ref or f"case-{index + 1}-{form_type}-{event_date.isoformat()}"),
                user_id=str(record.get("user_id") or record.get("telegram_user_id") or "unknown"),
                domain=FORM_TYPE_TO_DOMAIN.get(form_type, HealthDomain.unclassified),
                evidence_type=_evidence_type_for_form(form_type),
                form_type=form_type,
                title=str(record.get("title") or f"{form_type} evidence"),
                summary=str(record.get("summary") or f"{form_type} portfolio evidence"),
                event_date=event_date,
                source=source,
                source_ref=str(source_ref) if source_ref is not None else None,
                status=evidence_status,
                created_at=now,
                updated_at=now,
            )
        )

    return items


def compute_domain_coverage(items: list[EvidenceItem]) -> dict[HealthDomain, int]:
    """Return evidence counts for every universal health domain."""
    coverage = {domain: 0 for domain in HealthDomain}
    for item in items:
        coverage[item.domain] += 1
    return coverage


def _normalise_form_type(value: Any) -> str:
    return str(value or "UNKNOWN").strip().upper().replace("-", "_").replace(" ", "_")


def _parse_case_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if raw:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(raw[:19] if "T" in fmt or "%H" in fmt else raw[:10], fmt).date()
                except ValueError:
                    continue
    return date.today()


def _evidence_status_from_case_status(status: str) -> str:
    if status in {"filed", "success", "reviewed", "accepted"}:
        return "filed"
    if status == "failed":
        return "needs_work"
    return "drafted"


def _evidence_type_for_form(form_type: str) -> str:
    if form_type in WPBA_FORM_TYPES:
        return "wpba"
    if form_type in {"QIAT", "AUDIT"}:
        return "audit"
    if form_type in {"STAT", "TEACH_OBS", "TEACH", "TEACH_CONFID"}:
        return "teaching_session"
    if form_type in {"EDU_ACT", "FORMAL_COURSE"}:
        return "course"
    if form_type in {"SDL", "REFLECT_LOG", "PDP", "JCF"}:
        return "reflection_log"
    return "other"


def compute_gap_summary(items: list[EvidenceItem]) -> list[str]:
    """Return ordered, human-readable portfolio evidence gaps."""
    if not items:
        return ["No evidence entered yet"]

    gaps: list[str] = []
    coverage = compute_domain_coverage(items)
    for domain in CORE_DOMAINS:
        if coverage[domain] == 0:
            gaps.append(f"No {_domain_label(domain)} evidence")

    for domain in CORE_DOMAINS:
        domain_items = [item for item in items if item.domain == domain]
        if domain_items and all(_age_days(item) > AGEING_DAYS for item in domain_items):
            gaps.append(f"{_domain_label(domain)} evidence is over 3 years old")

    return gaps


def compute_next_actions(items: list[EvidenceItem], pathway: Pathway) -> list[str]:
    """Suggest concrete next filing actions for the selected pathway."""
    coverage = compute_domain_coverage(items)
    actions: list[str] = []

    missing_domains = [domain for domain in CORE_DOMAINS if coverage[domain] == 0]
    for domain in missing_domains[:3]:
        actions.append(_domain_action(domain))

    if pathway == Pathway.training_arcp:
        actions.append("File a CBD from a recent supervised case")
        actions.append("Add evidence before your next ARCP review")
    else:
        actions.append("This year: build toward 12 DOPS, 12 Mini-CEX, and 12 CBD entries for CESR")
        actions.append("Over the next 3–12 months: add structured consultant reports and CPD for CESR")
        actions.append("Plan teaching, audit, and reflection evidence across the year to balance CESR domains")

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

    dops_count = sum(1 for item in wpba_items if item.form_type == "DOPS")
    mini_cex_count = sum(1 for item in wpba_items if item.form_type == "MINI_CEX")
    cbd_count = sum(1 for item in wpba_items if item.form_type == "CBD")

    return {
        "pathway": pathway.value,
        "wpba_count": len(wpba_items),
        "wpba_target": 36,
        "wpba_breakdown": {
            "dops": dops_count,
            "mini_cex": mini_cex_count,
            "cbd": cbd_count,
        },
        "recent_evidence_count": len(recent_items),
    }
