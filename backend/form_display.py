"""Display-safe labels for portfolio form types.

Internal form keys are useful for routing and Kaizen UUID lookup, but they
should not leak into Telegram copy.
"""

from __future__ import annotations

import re

from form_schemas import FORM_SCHEMAS


PUBLIC_FORM_NAME_OVERRIDES = {
    "CBD": "Case-Based Discussion",
    "DOPS": "Direct Observation of Procedural Skills",
    "MINI_CEX": "Mini-Clinical Evaluation Exercise",
    "PROC_LOG": "Procedural Log",
    "REFLECT_LOG": "Reflective Practice Log",
    "US_CASE": "Ultrasound Case Reflection",
    "ESLE_ASSESS": "ESLE",
    "SERIOUS_INC": "Serious Incident Reflection",
    "EDU_ACT": "Educational Activity",
    "FORMAL_COURSE": "Formal Course",
    "TEACH_OBS": "Teaching Observation",
    "TEACH_CONFID": "Teaching Confidentiality",
    "CLIN_GOV": "Clinical Governance",
    "CRIT_INCIDENT": "Critical Incident",
    "COST_IMPROVE": "Cost Improvement",
    "EQUIP_SERVICE": "Equipment or Service Introduction",
    "EDU_MEETING": "Educational Meeting",
    "EDU_MEETING_SUPP": "Educational Meeting Supplementary",
    "MGMT_RISK_PROC": "Management: Procedure to Reduce Risk",
    "MGMT_TRAINING_EVT": "Management: Organising a Training Event",
}


def base_form_type(form_type: str) -> str:
    key = str(form_type or "").strip()
    if key.endswith("_2021"):
        return key[:-5]
    return key


def public_form_name(form_type: str) -> str:
    key = base_form_type(form_type)
    if not key:
        return ""
    if key in PUBLIC_FORM_NAME_OVERRIDES:
        return PUBLIC_FORM_NAME_OVERRIDES[key]
    schema = FORM_SCHEMAS.get(key) or FORM_SCHEMAS.get(str(form_type or "").strip()) or {}
    name = schema.get("name")
    if name:
        return str(name)
    return key.replace("_", " ").title()


def sanitize_internal_form_codes(text: str) -> str:
    """Replace internal form keys in user-visible text with public names."""
    clean = str(text or "")
    public_codes: dict[str, str] = {}
    for code in sorted(FORM_SCHEMAS, key=len, reverse=True):
        name = public_form_name(code)
        if not name:
            continue
        public_codes[code] = name
        public_codes[f"{code}_2021"] = public_form_name(f"{code}_2021")
    for code, name in sorted(PUBLIC_FORM_NAME_OVERRIDES.items(), key=lambda item: len(item[0]), reverse=True):
        public_codes[code] = name
        public_codes[f"{code}_2021"] = public_form_name(f"{code}_2021")
    for code, name in sorted(public_codes.items(), key=lambda item: len(item[0]), reverse=True):
        if not name:
            continue
        clean = re.sub(rf"\b{re.escape(code)}\b", name, clean)
    return clean
