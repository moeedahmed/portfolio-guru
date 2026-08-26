"""Human names for Kaizen form-type codes.

Single source of truth: the weekly nudge, the health report and anything else
that shows a form type to a doctor read from here. "TEACH_OBS" is an internal
code; "Teaching Observation" is what a doctor recognises.
"""

from __future__ import annotations

FORM_LABELS = {
    "CBD": "CBD", "DOPS": "DOPS", "MINI_CEX": "Mini-CEX", "ACAT": "ACAT",
    "LAT": "LAT", "ACAF": "ACAF", "STAT": "STAT", "MSF": "MSF",
    "QIAT": "QIAT", "JCF": "Journal Club", "ESLE_ASSESS": "ESLE",
    "ESLE": "ESLE", "ESLE_PART1_2": "ESLE Part 1 & 2", "MINI_CEX": "Mini-CEX", "AUDIT": "Audit",
    "REFLECT_LOG": "Reflective Log", "COMPLAINT": "Complaint",
    "SERIOUS_INC": "Serious Incident", "CRIT_INCIDENT": "Critical Incident",
    "PDP": "PDP", "APPRAISAL": "Appraisal", "TEACH": "Teaching",
    "TEACH_OBS": "Teaching Observation", "TEACH_CONFID": "Confidentiality",
    "SDL": "SDL", "EDU_ACT": "Educational Activity", "EDU_MEETING": "ES Meeting",
    "EDU_MEETING_SUPP": "ES Meeting (Supp)", "FORMAL_COURSE": "Formal Course",
    "PROC_LOG": "Procedure Log", "US_CASE": "Ultrasound Case",
    "RESEARCH": "Research", "CLIN_GOV": "Clinical Governance",
    "COST_IMPROVE": "Cost Improvement", "EQUIP_SERVICE": "Equipment/Service",
    "BUSINESS_CASE": "Business Case",
    "MGMT_ROTA": "Rota Management", "MGMT_RISK": "Risk Management",
    "MGMT_RISK_PROC": "Risk Procedure", "MGMT_INFO": "Information Management",
    "MGMT_EXPERIENCE": "Management Experience", "MGMT_REPORT": "Management Report",
    "MGMT_COMPLAINT": "Management Complaint", "MGMT_GUIDELINE": "Guideline Development",
    "MGMT_INDUCTION": "Induction", "MGMT_PROJECT": "Management Project",
    "MGMT_RECRUIT": "Recruitment", "MGMT_TRAINING_EVT": "Training Event",
    "OOP": "Out of Programme", "ABSENCE": "Absence", "CCT": "CCT Application",
    "HIGHER_PROG": "Higher Programme", "FILE_UPLOAD": "File Upload",
}


def form_label(form_type: str | None, fallback: str | None = None) -> str:
    """Return the human name for a form code, else a sensible fallback."""
    if not form_type:
        return fallback or "Portfolio evidence"
    key = form_type.replace("_2021", "").strip().upper()
    label = FORM_LABELS.get(key)
    if label:
        return label
    if fallback:
        return fallback
    # Unknown code: make it readable rather than shouting an internal token.
    return key.replace("_", " ").title()
