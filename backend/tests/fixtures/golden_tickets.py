"""
Golden ticket fixtures for high-value form types.

Each fixture is a minimal synthetic case that exercises the filer's field mapping,
stage-of-training handling, date formatting, and KC prefix structure. Patient
details are fully synthetic — no real patient data.

Derived from the shape of existing repo JSONs but anonymised/shortened.
"""

# ── CBD: conflict resolution discussion ──────────────────────────────────────

CBD_GOLDEN = {
    "form_type": "CBD",
    "fields": {
        "date_of_encounter": "15/4/2026",
        "end_date": "15/4/2026",
        "description": "CBD: synthetic conflict resolution scenario for testing.",
        "stage_of_training": "Higher/ST4-ST6",
        "clinical_reasoning": (
            "Title: Conflict resolution CBD — synthetic case.\n\n"
            "A structured discussion about managing an inter-specialty disagreement "
            "over patient disposition. Consultant-led case-based discussion covering "
            "de-escalation, shared decision-making, and system-level root cause."
        ),
        "reflection": (
            "Key learning: buying time before committing to a position under pressure. "
            "Protecting juniors who raise legitimate safety concerns. Root-cause thinking "
            "alongside the immediate clinical response."
        ),
    },
    "curriculum_links": [
        "Higher SLO7 Key Capability 1",
        "Higher SLO8 Key Capability 4",
        "Higher SLO11 Key Capability 2",
    ],
    "expected_filled_keys": [
        "date_of_encounter", "end_date", "stage_of_training",
        "clinical_reasoning", "reflection",
    ],
    "expected_stage_value": "Higher",
}

# ── DOPS: joint aspiration ───────────────────────────────────────────────────

DOPS_GOLDEN = {
    "form_type": "DOPS",
    "fields": {
        "date_of_encounter": "13/3/2026",
        "end_date": "13/3/2026",
        "description": "DOPS: synthetic large joint aspiration case for testing.",
        "placement": "Emergency Medicine",
        "date_of_event": "13/3/2026",
        "case_observed": (
            "Synthetic patient with suspected septic arthritis. Lateral approach "
            "aspiration performed under aseptic technique. Straw-coloured fluid "
            "aspirated and sent for analysis."
        ),
        "stage_of_training": "Higher/ST4-ST6",
        "procedural_skill": "Large joint aspiration",
        "reflection": (
            "Accurate landmarking on first attempt. Aspirate volume was sufficient "
            "for microscopy but short for culture — next time aspirate until dry."
        ),
    },
    "curriculum_links": [
        "Higher SLO6 Key Capability 1",
        "Higher SLO6 Key Capability 2",
        "Higher SLO7 Key Capability 3",
    ],
    "expected_filled_keys": [
        "date_of_encounter", "end_date", "placement", "date_of_event",
        "case_observed", "stage_of_training", "procedural_skill", "reflection",
    ],
    "expected_stage_value": "Higher",
}

# ── PROC_LOG: adult sedation ─────────────────────────────────────────────────

PROC_LOG_GOLDEN = {
    "form_type": "PROC_LOG",
    "fields": {
        "date_of_encounter": "9/3/2026",
        "date_of_activity": "9/3/2026",
        "end_date": "9/3/2026",
        "description": "Procedural log: synthetic adult sedation case for testing.",
        "stage_of_training": "Higher/ST4-ST6",
        "year_of_training": "ST5",
        "age_of_patient": "60s",
        "higher_procedural_skill": "Adult sedation",
        "reflective_comments": (
            "Synthetic procedural sedation case. Two key learning points: "
            "team discipline around procedure start, and knowing when to stop "
            "escalating sedation and redirect to theatre."
        ),
    },
    "curriculum_links": [
        "Higher SLO6 Key Capability 1",
        "Higher SLO6 Key Capability 2",
        "Higher SLO7 Key Capability 3",
    ],
    "expected_filled_keys": [
        "date_of_activity", "stage_of_training", "year_of_training",
        "age_of_patient", "higher_procedural_skill", "reflective_comments",
    ],
    "expected_stage_value": "Higher",
}

# ── TEACH: bedside teaching ──────────────────────────────────────────────────

TEACH_GOLDEN = {
    "form_type": "TEACH",
    "fields": {
        "date_of_encounter": "13/3/2026",
        "date_of_teaching_activity": "13/3/2026",
        "end_date": "13/3/2026",
        "description": "Synthetic bedside teaching session for testing.",
        "title_of_session": "Bedside teaching of a procedural skill during a live case",
        "recognised_courses": "- n/a -",
        "accs_procedural_skill": "- n/a -",
        "intermediate_procedural_skill": "- n/a -",
        "higher_procedural_skill": "Large joint aspiration",
        "learning_outcomes": (
            "Synthetic teaching case covering indication, approach options, "
            "landmarking, ultrasound alternative, and sampling discipline."
        ),
    },
    "curriculum_links": [
        "Higher SLO9 Key Capability 1",
        "Higher SLO6 Key Capability 1",
        "Higher SLO6 Key Capability 2",
    ],
    "expected_filled_keys": [
        "date_of_teaching_activity", "title_of_session", "recognised_courses",
        "accs_procedural_skill", "intermediate_procedural_skill",
        "higher_procedural_skill", "learning_outcomes",
    ],
    "expected_stage_value": None,  # TEACH has no stage_of_training field
}

# ── US_CASE: focused echo ────────────────────────────────────────────────────

US_CASE_GOLDEN = {
    "form_type": "US_CASE",
    "fields": {
        "date_of_encounter": "24/10/2025",
        "date_of_case": "24/10/2025",
        "end_date": "24/10/2025",
        "description": "Ultrasound case reflection: synthetic echo practice for testing.",
        "case_reflection_title": "Focused echo practice on volunteers — four cardiac windows",
        "location": "Synthetic Hospital",
        "patient_gender": "- n/a -",
        "equipment_used": "ED POCUS machine, phased array probe",
        "clinical_scenario": (
            "Supervised hands-on ultrasound session practising focused "
            "transthoracic echocardiography on normal adult volunteers."
        ),
        "how_used": (
            "Acquired four standard cardiac windows: parasternal long axis, "
            "parasternal short axis, apical four-chamber, and subxiphoid."
        ),
        "usable_images": "Yes. Diagnostic-quality views obtained.",
        "interpret_images": "Normal anatomy on volunteers.",
        "changed_management": "Training context, no clinical decision.",
        "learning_points": "Probe manoeuvring and positional adjustment techniques.",
        "accs_procedural_skill": "- n/a -",
        "intermediate_procedural_skill": "- n/a -",
        "higher_procedural_skill": "- n/a -",
    },
    "curriculum_links": [
        "Higher SLO3 Key Capability 2",
        "Higher SLO6 Key Capability 1",
        "Higher SLO6 Key Capability 2",
    ],
    "expected_filled_keys": [
        "case_reflection_title", "date_of_case", "location", "patient_gender",
        "equipment_used", "clinical_scenario", "how_used", "usable_images",
        "interpret_images", "changed_management", "learning_points",
        "accs_procedural_skill", "intermediate_procedural_skill",
        "higher_procedural_skill",
    ],
    "expected_stage_value": None,
}

# ── REFLECT_LOG: sedation reflection ─────────────────────────────────────────

REFLECT_LOG_GOLDEN = {
    "form_type": "REFLECT_LOG",
    "fields": {
        "date_of_encounter": "17/10/2025",
        "date_of_event": "17/10/2025",
        "end_date": "17/10/2025",
        "description": "Reflective log: synthetic over-sedation case for testing.",
        "reflection_title": "Synthetic sedation reflection for testing",
        "event_type": "ED patient",
        "reflection": "Synthetic reflection text covering the clinical scenario.",
        "replay_differently": "Three synthetic learning points about handover and guidelines.",
        "why": "Task focus and unfamiliarity with local guidelines.",
        "different_outcome": "Observation rather than dose escalation.",
        "focussing_on": "Active guideline awareness and recognising task focus.",
        "learned": "Guidelines encode accumulated experience better than single-episode judgment.",
    },
    "curriculum_links": [
        "Higher SLO1 Key Capability 1",
        "Higher SLO6 Key Capability 1",
        "Higher SLO8 Key Capability 4",
    ],
    "expected_filled_keys": [
        "date_of_encounter", "reflection_title", "date_of_event", "event_type",
        "reflection", "replay_differently", "why", "different_outcome",
        "focussing_on", "learned",
    ],
    "expected_stage_value": None,
}


# ── All golden tickets as a list for parametrised tests ──────────────────────

ALL_GOLDEN_TICKETS = [
    CBD_GOLDEN,
    DOPS_GOLDEN,
    PROC_LOG_GOLDEN,
    TEACH_GOLDEN,
    US_CASE_GOLDEN,
    REFLECT_LOG_GOLDEN,
]
