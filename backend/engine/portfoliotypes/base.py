"""Portfolio type detection and configuration.

Auto-detects the portfolio level (HST, ACCS, Intermediate, Assessor)
from the Kaizen dashboard upon credential verification.
"""

import json
from pathlib import Path

DOMAIN_SKILL_DIR = Path(__file__).parent.parent / "providers" / "kaizen" / "domain_skill"


def load_selectors() -> dict:
    """Load the Kaizen domain skill selectors."""
    path = DOMAIN_SKILL_DIR / "selectors.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def load_2025_uuids() -> dict:
    """Load 2025 form UUID map."""
    path = DOMAIN_SKILL_DIR / "2025-uuids.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def detect_portfolio_type(dashboard_title: str, page_text: str) -> str:
    """Detect portfolio type from Kaizen dashboard.

    Examines the page title and visible text to determine role.
    No manual user selection needed.

    Returns one of: 'hst', 'accs', 'intermediate', 'assessor', 'unknown'
    """
    title_lower = dashboard_title.lower()

    if "clinical supervisor" in title_lower:
        return "assessor"
    if "higher trainee" in title_lower:
        return "hst"
    if "accs" in page_text.lower() or "accs trainee" in title_lower:
        # Check if also intermediate
        if "intermediate" in page_text.lower():
            return "accs_intermediate"  # Multi-curriculum
        return "accs"
    if "intermediate" in page_text.lower():
        return "intermediate"
    
    return "unknown"


def get_form_types_for_role(portfolio_type: str) -> dict:
    """Get available form types for the detected portfolio role."""
    selectors = load_selectors()
    types = selectors.get("form_types_by_role", {})
    
    if portfolio_type == "assessor":
        return types.get("assessor", {"note": "Assessors do not create forms"})
    
    # Trainee roles share the base form set
    base_forms = selectors.get("form_types_by_role", {}).get("hst", {})
    role_specific = types.get(portfolio_type, {})
    
    if role_specific:
        merged = {**base_forms, **role_specific}
        return merged
    return base_forms


def get_role_config(portfolio_type: str) -> dict:
    """Get full configuration for a portfolio type."""
    configs = {
        "hst": {
            "display_name": "Higher Specialist Trainee",
            "dashboard_label": "Higher Trainee",
            "form_types": get_form_types_for_role("hst"),
        },
        "accs": {
            "display_name": "ACCS Trainee",
            "dashboard_label": "ACCS Trainee",
            "form_types": get_form_types_for_role("accs"),
        },
        "accs_intermediate": {
            "display_name": "ACCS + Intermediate Trainee",
            "dashboard_label": "ACCS Trainee",
            "form_types": get_form_types_for_role("accs"),
        },
        "assessor": {
            "display_name": "Clinical Supervisor / Assessor",
            "dashboard_label": "Clinical Supervisor",
            "form_types": get_form_types_for_role("assessor"),
        },
    }
    return configs.get(portfolio_type, configs["hst"])
