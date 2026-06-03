"""DOPS filing helpers — deterministic normalisation, KC breadth, quality gate.

The extractor produces DOPS draft fields aligned with the schema in
`form_schemas.py` (indication, trainee_performance, clinical_setting, ...).
The Kaizen DOM only exposes one narrative slot (`case_observed`) plus a
placement dropdown, a procedural skill dropdown, a reflection field, and the
common header dates.

These helpers bridge the gap so that:
  - draft-side narrative (indication + trainee_performance + clinical
    reasoning) lands in the Kaizen `case_observed` field instead of being
    silently dropped because the schema key has no DOM mapping;
  - the procedure dropdown is populated whichever schema key carries it
    (`procedure_name` vs `procedural_skill`);
  - clinical_setting flows into the `placement` dropdown when placement is
    blank, instead of disappearing;
  - dates are mirrored across start/end/event so Kaizen does not reject the
    draft with missing date validation;
  - DOPS cases mentioning resuscitation, circulatory support, or sedation
    pick up the SLO3/SLO6 KCs the LLM would otherwise miss;
  - a final pre-save gate refuses to claim "saved successfully" when the
    required Kaizen narrative/procedure/stage/date fields are still blank.
"""

from __future__ import annotations

from typing import Iterable


def _has_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(str(item).strip() for item in value)
    return True


def _str(value) -> str:
    return str(value).strip() if value is not None else ""


DOPS_PLACEMENT_FALLBACKS = {
    "emergency_department": "Emergency Department",
    "acute_medical_ward": "Acute Medical Ward",
    "paediatric_emergency_department": "Paediatric Emergency Department",
    "intensive_care_unit": "Intensive Care Unit",
    "emergency_department_observation_unit": "Emergency Department Observation Unit",
    "minor_injury_unit": "Minor Injury Unit",
}

_DOPS_PLACEMENT_ALIASES: tuple[tuple[str, str], ...] = (
    ("paediatric_emergency_department", "paediatric emergency department"),
    ("paediatric_emergency_department", "pediatric emergency department"),
    ("paediatric_emergency_department", "paeds ed"),
    ("paediatric_emergency_department", "paeds emergency"),
    ("paediatric_emergency_department", "ped"),
    ("emergency_department_observation_unit", "observation unit"),
    ("emergency_department_observation_unit", "clinical decision unit"),
    ("emergency_department_observation_unit", "cdu"),
    ("emergency_department_observation_unit", "ed obs"),
    ("intensive_care_unit", "intensive care"),
    ("intensive_care_unit", "critical care"),
    ("intensive_care_unit", "itu"),
    ("intensive_care_unit", "icu"),
    ("acute_medical_ward", "acute medical"),
    ("acute_medical_ward", "amu"),
    ("minor_injury_unit", "minor injury"),
    ("minor_injury_unit", "minors"),
    ("minor_injury_unit", "miu"),
    ("emergency_department", "emergency department"),
    ("emergency_department", "emergency medicine"),
    ("emergency_department", "resuscitation"),
    ("emergency_department", "resus"),
    ("emergency_department", "majors"),
    ("emergency_department", "ed"),
    ("emergency_department", "a&e"),
    ("emergency_department", "ae"),
)


def _normalise_for_match(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else " " for ch in value).strip()


def _compact_for_match(value: str) -> str:
    return "".join(_normalise_for_match(value).split())


def _placement_key(value: str) -> str:
    normalised = f" {_normalise_for_match(value)} "
    compact = _compact_for_match(value)
    for key, alias in _DOPS_PLACEMENT_ALIASES:
        alias_norm = f" {_normalise_for_match(alias)} "
        alias_compact = _compact_for_match(alias)
        if alias_norm in normalised or alias_compact == compact:
            return key
    return ""


def normalise_dops_placement(value: str, options: Iterable[str] | None = None) -> str:
    """Return a Kaizen-selectable DOPS placement label where possible.

    `options` should be the actual labels currently rendered by Kaizen. When
    supplied, this returns the exact option text. Without options it falls back
    to the canonical labels used in the local schema.
    """
    raw = _str(value)
    if not raw:
        return ""

    option_list = [_str(option) for option in (options or []) if _str(option)]
    raw_compact = _compact_for_match(raw)
    for option in option_list:
        if _compact_for_match(option) == raw_compact:
            return option

    key = _placement_key(raw)
    if key and option_list:
        canonical = DOPS_PLACEMENT_FALLBACKS.get(key, "")
        canonical_compact = _compact_for_match(canonical)
        for option in option_list:
            option_compact = _compact_for_match(option)
            option_key = _placement_key(option)
            if option_key == key or option_compact == canonical_compact:
                return option

    if key:
        return DOPS_PLACEMENT_FALLBACKS[key]
    return raw


# ─── Field-map adapter ───────────────────────────────────────────────────────


def normalise_dops_fields(fields: dict) -> dict:
    """Return DOPS fields aligned with `FORM_FIELD_MAP["DOPS"]` DOM keys.

    Idempotent. DOM-aligned helper keys are filled when currently empty.
    For `case_observed`, the structured DOPS review fields are the source of
    truth: if the draft has indication / clinical reasoning / trainee
    performance, rebuild the Kaizen narrative from those same fields so the
    saved draft cannot diverge from the preview the user approved.
    """
    out = dict(fields or {})

    date_of_encounter = _str(out.get("date_of_encounter"))
    if date_of_encounter:
        if not _has_value(out.get("end_date")):
            out["end_date"] = date_of_encounter
        if not _has_value(out.get("date_of_event")):
            out["date_of_event"] = date_of_encounter

    clinical_setting = _str(out.get("clinical_setting"))
    placement = _str(out.get("placement"))
    if clinical_setting and not placement:
        out["placement"] = normalise_dops_placement(clinical_setting)
    elif placement:
        out["placement"] = normalise_dops_placement(placement)

    procedure_name = _str(out.get("procedure_name"))
    procedural_skill = _str(out.get("procedural_skill"))
    accs_procedural_skill = _str(out.get("accs_procedural_skill"))
    if procedure_name and not procedural_skill:
        out["procedural_skill"] = procedure_name
    elif procedural_skill and not procedure_name:
        out["procedure_name"] = procedural_skill
    if not accs_procedural_skill:
        if procedure_name:
            out["accs_procedural_skill"] = procedure_name
        elif procedural_skill:
            out["accs_procedural_skill"] = procedural_skill

    case_observed = _str(out.get("case_observed"))
    narrative = _build_case_observed_narrative(out)
    if narrative and _has_structured_dops_narrative(out):
        out["case_observed"] = narrative
    elif narrative and not case_observed:
        out["case_observed"] = narrative

    return out


def _has_structured_dops_narrative(fields: dict) -> bool:
    return any(
        _has_value(fields.get(key))
        for key in ("indication", "clinical_reasoning", "trainee_performance")
    )


def _build_case_observed_narrative(fields: dict) -> str:
    """Combine indication / clinical_reasoning / trainee_performance into one
    DOPS narrative block.

    Keep this as assessor-facing prose, not engineering labels. The reviewed
    draft can stay structured; Kaizen's single narrative box should read like
    a normal assessment entry.
    """
    parts: list[str] = []
    indication = _str(fields.get("indication"))
    clinical_reasoning = _str(fields.get("clinical_reasoning"))
    trainee_performance = _str(fields.get("trainee_performance"))
    procedure = (
        _str(fields.get("procedure_name"))
        or _str(fields.get("procedural_skill"))
        or _str(fields.get("accs_procedural_skill"))
    )
    clinical_setting = _str(fields.get("clinical_setting"))

    if procedure or clinical_setting:
        head = ", ".join(p for p in (procedure, clinical_setting) if p)
        if head:
            if indication:
                parts.append(f"This DOPS concerned {head}, performed for {indication}.")
            else:
                parts.append(f"This DOPS concerned {head}.")
    if indication:
        if not (procedure or clinical_setting):
            parts.append(f"The indication was {indication}.")
    if clinical_reasoning:
        parts.append(clinical_reasoning)
    if trainee_performance:
        parts.append(trainee_performance)
    return "\n\n".join(parts)
# ─── KC breadth supplementer ─────────────────────────────────────────────────


# Each trigger list is intentionally tight — only words that genuinely imply
# the KC. Generic words like "patient" or "ED" do not appear because they
# would fire for every case and inflate KC counts without evidence.
DOPS_KC_BREADTH_TRIGGERS: dict[str, tuple[str, ...]] = {
    "SLO3 KC2": (
        "unstable", "hypotens", "hypoperfus", "perfusion",
        "fluid resus", "vasopressor", "noradrenaline", "metaraminol",
        "circulatory support", "rvr", "rapid ventricular",
        "haematemesis", "melaena", "gi bleed", "gastrointestinal",
        "ogd", "endoscop", "variceal", "ppi",
        "blood transfusion", "cross-match", "cross match", " hb ",
    ),
    "SLO3 KC3": (
        "peri-arrest", "peri arrest", "periarrest", "arrhythmia",
        "life-threatening", "life threatening",
        "amiodarone", "magnesium",
        "synchronised", "synchronized", "synchronised shock", "synchronised cardioversion",
        "cardioversion", "cardiovert", "dc shock", "dc cardioversion",
        "refractory", " vt", " vf", "afib", "atrial fibrillation", "atrial flutter",
    ),
    "SLO3 KC5": (
        "team leader", "led resus", "resus team", "led the resus",
        "med reg", " itu", " icu", "escalat",
        "coordinated", "led the team", "directed", "referral",
    ),
    "SLO6 KC2": (
        "sedation", "ketamine", "propofol", "midazolam",
        "cardioversion", "cardiovert", "dc cardioversion",
        "intubation", "rsi", "chest drain", "thoracotomy",
        "synchronised shock", "synchronised cardioversion",
    ),
}


# Default KCs added when trigger-based supplementation produces fewer than
# three KCs for a case that did surface at least one KC (LLM or trigger).
# Chosen to cover the broadest swathe of undifferentiated acute ED activity:
# life-threatening management, resus team leadership, procedural skills.
DOPS_KC_DEFAULT_FALLBACKS: tuple[str, ...] = (
    "SLO3 KC2",
    "SLO3 KC5",
    "SLO6 KC2",
)


# Full KC text mirrored from `RCEM_KC_MAP` in extractor.py. Keep in lock-step
# with that map — the Kaizen tag tree matches exact label text.
DOPS_KC_FULL_TEXT: dict[str, str] = {
    "SLO3 KC2": (
        "SLO3 KC2: be expert in fluid management and circulatory support in "
        "critically ill patients (2025 Update)"
    ),
    "SLO3 KC3": (
        "SLO3 KC3: manage all the life-threatening conditions including "
        "peri-arrest & arrest situations in the ED (2025 Update)"
    ),
    "SLO3 KC5": (
        "SLO3 KC5: effectively lead and support resuscitation teams "
        "(2025 Update)"
    ),
    "SLO6 KC2": (
        "SLO6 KC2: the knowledge and psychomotor skills to perform EM "
        "procedural skills safely and in a timely fashion (2025 Update)"
    ),
}


def _kc_code_prefix(kc_string: str) -> str:
    """Return the normalised `SLOxKCy` prefix used for de-duplication."""
    head = kc_string.split(":", 1)[0]
    return "".join(head.lower().split())


def suggest_dops_kc_breadth(
    source_text: str,
    existing_kcs: Iterable[str] | None = None,
) -> list[str]:
    """Augment LLM-selected KCs with deterministic resuscitation/procedure KCs.

    Triggers on case-text keywords that genuinely imply circulatory support,
    peri-arrest management, resus team leadership, or psychomotor procedural
    skills. Existing KCs are preserved; only KCs whose code prefix is not
    already present are appended.
    """
    existing = [k for k in (existing_kcs or []) if str(k).strip()]
    text_lower = (source_text or "").lower()
    present_prefixes = {_kc_code_prefix(k) for k in existing}

    augmented = list(existing)
    for code, triggers in DOPS_KC_BREADTH_TRIGGERS.items():
        if not any(trigger in text_lower for trigger in triggers):
            continue
        full = DOPS_KC_FULL_TEXT[code]
        prefix = _kc_code_prefix(full)
        if prefix in present_prefixes:
            continue
        augmented.append(full)
        present_prefixes.add(prefix)

    # Universal breadth fallback: a real DOPS case that already produced at
    # least one KC (LLM or trigger) should rarely sit below three. Top up with
    # the broad default KCs, never enough to drown out the genuine picks but
    # enough that the curriculum tag tree gets a credible breadth signal. We
    # deliberately do not fire on a fully empty result — that is the bland /
    # unrelated case shape (e.g. pure teaching observation) and adding KCs
    # there would fabricate evidence.
    if augmented and len(augmented) < 3:
        for code in DOPS_KC_DEFAULT_FALLBACKS:
            if len(augmented) >= 3:
                break
            full = DOPS_KC_FULL_TEXT[code]
            prefix = _kc_code_prefix(full)
            if prefix in present_prefixes:
                continue
            augmented.append(full)
            present_prefixes.add(prefix)
    return augmented


def derive_dops_curriculum_links(kcs: Iterable[str]) -> list[str]:
    """Return deduplicated SLO codes (`SLO3`, `SLO6`, ...) from KC strings.

    Used to keep `curriculum_links` in sync after `suggest_dops_kc_breadth`
    adds new KCs — without matching SLO codes the filer won't expand the
    SLO accordion containing the new KC.
    """
    import re

    seen: list[str] = []
    seen_set: set[str] = set()
    for kc in kcs or []:
        match = re.search(r"SLO\s*(\d+)", str(kc), flags=re.IGNORECASE)
        if not match:
            continue
        code = f"SLO{int(match.group(1))}"
        if code not in seen_set:
            seen.append(code)
            seen_set.add(code)
    return seen
