"""
Filing reliability coverage report — machine-readable + human-readable.

Generates a JSON report and prints a summary table showing:
- Which forms have DOM mappings (deterministic filer)
- Which forms have UUIDs (can navigate to form)
- Which forms have golden ticket test fixtures
- Which forms use tag-based vs inline curriculum
- KC verification status per form

Usage:
    python filing_reliability_report.py          # Print summary
    python filing_reliability_report.py --json   # Output JSON
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from kaizen_form_filer import (
    FORM_FIELD_MAP,
    FORM_UUIDS,
    FORMS_USING_TAG_BASED_CURRICULUM,
    SLO_NODE_IDS,
    COMMON_HEADER_FIELDS,
)
from form_schemas import FORM_SCHEMAS


# Forms with golden ticket test fixtures
GOLDEN_TICKET_FORMS = {"CBD", "DOPS", "PROC_LOG", "TEACH", "US_CASE", "REFLECT_LOG"}

# Forms that only need universal headers (no form-specific fields)
UNIVERSAL_HEADER_ONLY = {"CCT", "ABSENCE", "OOP", "FILE_UPLOAD"}


def generate_report() -> dict:
    """Generate the machine-readable reliability report."""
    all_form_types = sorted(set(list(FORM_UUIDS.keys()) + list(FORM_SCHEMAS.keys())))

    forms = {}
    for ft in all_form_types:
        has_uuid = ft in FORM_UUIDS
        has_field_map = ft in FORM_FIELD_MAP
        has_schema = ft in FORM_SCHEMAS
        in_golden = ft in GOLDEN_TICKET_FORMS

        # KC/curriculum path
        schema = FORM_SCHEMAS.get(ft, {})
        has_kc_fields = any(f.get("type") == "kc_tick" for f in schema.get("fields", []))
        uses_tag_curriculum = ft in FORMS_USING_TAG_BASED_CURRICULUM
        curriculum_path = "none"
        if has_kc_fields:
            curriculum_path = "tag-modal" if uses_tag_curriculum else "inline-tree"

        # Field coverage
        field_map = FORM_FIELD_MAP.get(ft, {})
        schema_fields = [f["key"] for f in schema.get("fields", []) if f.get("type") != "kc_tick"]
        mapped_fields = set(field_map.keys())
        header_keys = set(COMMON_HEADER_FIELDS.keys())

        covered = [f for f in schema_fields if f in mapped_fields or f in header_keys]
        unmapped = [f for f in schema_fields if f not in mapped_fields and f not in header_keys]

        # Reliability tier
        if has_uuid and has_field_map and in_golden:
            tier = "gold"  # Full mapping + golden ticket test
        elif has_uuid and has_field_map:
            tier = "silver"  # Full mapping, no golden test
        elif has_uuid:
            tier = "bronze"  # UUID only, browser-use fallback
        else:
            tier = "unmapped"  # No UUID

        forms[ft] = {
            "has_uuid": has_uuid,
            "has_field_map": has_field_map,
            "has_schema": has_schema,
            "golden_ticket": in_golden,
            "curriculum_path": curriculum_path,
            "tier": tier,
            "schema_fields": len(schema_fields),
            "mapped_fields": len(covered),
            "unmapped_fields": unmapped,
            "field_coverage_pct": round(len(covered) / max(len(schema_fields), 1) * 100, 1),
        }

    # Summary stats
    tiers = {"gold": 0, "silver": 0, "bronze": 0, "unmapped": 0}
    for f in forms.values():
        tiers[f["tier"]] += 1

    return {
        "generated": "2026-04-25",
        "total_forms": len(forms),
        "tiers": tiers,
        "slo_coverage": len(SLO_NODE_IDS) - 1,  # minus "header"
        "forms": forms,
    }


def print_summary(report: dict) -> None:
    """Print a human-readable summary table."""
    print("=" * 80)
    print("PORTFOLIO GURU — FILING RELIABILITY REPORT")
    print("=" * 80)
    print()

    tiers = report["tiers"]
    total = report["total_forms"]
    print(f"Total forms: {total}")
    print(f"  Gold   (mapped + tested):  {tiers['gold']}")
    print(f"  Silver (mapped):           {tiers['silver']}")
    print(f"  Bronze (UUID only):        {tiers['bronze']}")
    print(f"  Unmapped:                  {tiers['unmapped']}")
    print(f"  SLO coverage:              {report['slo_coverage']}/12")
    print()

    # Table
    header = f"{'Form':<22} {'Tier':<8} {'UUID':<5} {'Map':<5} {'Test':<5} {'KC Path':<12} {'Fields':<10}"
    print(header)
    print("-" * len(header))

    for ft, data in sorted(report["forms"].items()):
        uuid = "Y" if data["has_uuid"] else "-"
        fmap = "Y" if data["has_field_map"] else "-"
        test = "Y" if data["golden_ticket"] else "-"
        kc = data["curriculum_path"]
        fields = f"{data['mapped_fields']}/{data['schema_fields']}"
        tier = data["tier"]
        print(f"{ft:<22} {tier:<8} {uuid:<5} {fmap:<5} {test:<5} {kc:<12} {fields:<10}")

    print()
    # Unmapped fields per form (for forms with gaps)
    gaps = [(ft, d["unmapped_fields"]) for ft, d in report["forms"].items() if d["unmapped_fields"]]
    if gaps:
        print("UNMAPPED FIELDS (need DOM inspection or browser-use):")
        for ft, fields in sorted(gaps):
            print(f"  {ft}: {', '.join(fields)}")
    else:
        print("All schema fields are mapped — no gaps detected.")


if __name__ == "__main__":
    report = generate_report()
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
    else:
        print_summary(report)
