"""Offline filing reliability matrix for Portfolio Guru.

This is the local gate for the cloud-migration decision: it proves that the
priority form set is wired to deterministic draft-only Kaizen filing, without
touching live Telegram, credentials, Chrome, or Kaizen.

It deliberately does not mark cloud migration ready. Live draft-save evidence
is a separate gate because offline routing proof cannot prove Kaizen's current
DOM, session, or account-specific behaviour.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from extractor import schema_form_type
from filer_router import PLATFORM_REGISTRY
from form_display import public_form_name
from form_schemas import FORM_SCHEMAS
from kaizen_form_filer import FORM_FIELD_MAP, FORM_UUIDS, canonical_form_type


PRIORITY_FORMS: tuple[str, ...] = (
    "CBD",
    "DOPS",
    "MINI_CEX",
    "ACAT",
    "QIAT",
    "TEACH",
    "REFLECT_LOG",
    "PROC_LOG",
)

FORBIDDEN_FORM_CODES: frozenset[str] = frozenset({"CEX", "CDD", "ALP"})

REQUIRED_LIVE_GATES: tuple[str, ...] = (
    "controlled_draft_save_visible_in_kaizen",
    "draft_deleted_or_cleaned_up",
    "failure_copy_checked",
    "same_pack_green_on_cloud_headless_chrome",
)


@dataclass(frozen=True)
class MatrixCase:
    form_type: str
    canonical_form_type: str
    schema_form_type: str
    public_name: str
    form_uuid: str | None
    has_schema: bool
    has_dom_map: bool
    supported_by_router: bool
    required_field_keys: tuple[str, ...]
    mapped_required_field_keys: tuple[str, ...]
    generated_fields: dict[str, Any]
    route_ready: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class MatrixReport:
    priority_forms: tuple[str, ...]
    route_ready_count: int
    route_total: int
    offline_route_ready: bool
    cloud_migration_ready: bool
    blocked_reason: str
    cases: tuple[MatrixCase, ...]


def _kaizen_supported_forms() -> set[str]:
    return set(PLATFORM_REGISTRY["kaizen"]["supported_forms"])


def required_fields_for(form_type: str) -> tuple[dict[str, Any], ...]:
    """Return non-KC required schema fields for a form."""
    schema = FORM_SCHEMAS.get(schema_form_type(form_type), {})
    fields = []
    for field in schema.get("fields", []):
        if field.get("type") == "kc_tick" or field.get("key") == "key_capabilities":
            continue
        if field.get("required"):
            fields.append(field)
    return tuple(fields)


def sample_value_for_field(form_type: str, field: dict[str, Any]) -> Any:
    """Generate a safe synthetic value for an offline draft-save ticket."""
    key = str(field.get("key") or "")
    field_type = str(field.get("type") or "text")
    options = field.get("options") or []

    if field_type == "date" or key.startswith("date_"):
        return "15/4/2026"
    if options:
        return options[0]
    if field_type in {"number", "integer"}:
        return 1
    if field_type in {"checkbox", "boolean"}:
        return True
    if key == "stage_of_training":
        return "Higher/ST4-ST6"
    if key in {"curriculum_links", "key_capabilities"}:
        return []
    return f"Synthetic {public_form_name(form_type)} {key.replace('_', ' ')}"


def build_ticket_fields(form_type: str) -> dict[str, Any]:
    """Build a representative draft-save field payload for offline routing."""
    fields: dict[str, Any] = {
        "date_of_encounter": "15/4/2026",
        "end_date": "15/4/2026",
        "description": f"Synthetic {public_form_name(form_type)} reliability case",
    }
    for field in required_fields_for(form_type):
        fields[field["key"]] = sample_value_for_field(form_type, field)
    return fields


def build_case(form_type: str) -> MatrixCase:
    canonical = canonical_form_type(form_type)
    schema_key = schema_form_type(form_type)
    required = required_fields_for(form_type)
    required_keys = tuple(field["key"] for field in required)
    field_map = FORM_FIELD_MAP.get(canonical, {})
    mapped_required = tuple(key for key in required_keys if key in field_map)
    supported = canonical in _kaizen_supported_forms() or form_type in _kaizen_supported_forms()
    blockers: list[str] = []

    if form_type in FORBIDDEN_FORM_CODES or canonical in FORBIDDEN_FORM_CODES:
        blockers.append("forbidden_non_canonical_form_code")
    if canonical not in FORM_UUIDS:
        blockers.append("missing_kaizen_uuid")
    if schema_key not in FORM_SCHEMAS:
        blockers.append("missing_form_schema")
    if not field_map:
        blockers.append("missing_dom_field_map")
    if not supported:
        blockers.append("not_supported_by_filer_router")
    if not required_keys:
        blockers.append("no_required_fields_to_validate")
    if "_" in public_form_name(form_type):
        blockers.append("public_name_leaks_internal_code")

    return MatrixCase(
        form_type=form_type,
        canonical_form_type=canonical,
        schema_form_type=schema_key,
        public_name=public_form_name(form_type),
        form_uuid=FORM_UUIDS.get(canonical),
        has_schema=schema_key in FORM_SCHEMAS,
        has_dom_map=bool(field_map),
        supported_by_router=supported,
        required_field_keys=required_keys,
        mapped_required_field_keys=mapped_required,
        generated_fields=build_ticket_fields(form_type),
        route_ready=not blockers,
        blockers=tuple(blockers),
    )


def build_matrix_report(forms: tuple[str, ...] = PRIORITY_FORMS) -> MatrixReport:
    cases = tuple(build_case(form_type) for form_type in forms)
    ready_count = sum(1 for case in cases if case.route_ready)
    offline_ready = ready_count == len(cases) and not (set(forms) & FORBIDDEN_FORM_CODES)
    return MatrixReport(
        priority_forms=forms,
        route_ready_count=ready_count,
        route_total=len(cases),
        offline_route_ready=offline_ready,
        cloud_migration_ready=False,
        blocked_reason=(
            "cloud migration remains blocked until controlled live draft-save "
            "evidence passes on local Mac Mini and cloud/headless Chrome"
        ),
        cases=cases,
    )


def report_as_dict(report: MatrixReport) -> dict[str, Any]:
    return asdict(report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the offline filing reliability matrix.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a readable summary.")
    args = parser.parse_args()

    report = build_matrix_report()
    if args.json:
        print(json.dumps(report_as_dict(report), indent=2, sort_keys=True))
        return

    print("Portfolio Guru filing reliability matrix")
    print(f"Offline route ready: {report.offline_route_ready}")
    print(f"Cloud migration ready: {report.cloud_migration_ready}")
    print(f"Ready forms: {report.route_ready_count}/{report.route_total}")
    print(f"Blocked reason: {report.blocked_reason}")
    for case in report.cases:
        marker = "PASS" if case.route_ready else "BLOCKED"
        print(f"- {marker} {case.form_type}: {case.public_name}")
        if case.blockers:
            print(f"  blockers: {', '.join(case.blockers)}")


if __name__ == "__main__":
    main()
