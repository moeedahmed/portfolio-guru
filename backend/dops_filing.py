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
    if procedure_name and not procedural_skill:
        out["procedural_skill"] = procedure_name
    elif procedural_skill and not procedure_name:
        out["procedure_name"] = procedural_skill

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
    procedure = _str(fields.get("procedure_name")) or _str(fields.get("procedural_skill"))
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


# ─── Quality gate ────────────────────────────────────────────────────────────


DOPS_REQUIRED_LABELS = (
    ("Date occurred on", ("date_of_encounter",)),
    ("Stage of training", ("stage_of_training", "stage")),
    ("Procedural skill", ("procedural_skill", "procedure_name")),
    ("Case observed narrative", ("case_observed",)),
)


# Older drafts used section labels in case_observed. Keep the prefixes for
# backwards-compatible quality checks, but new filings should be natural prose.
_NARRATIVE_LABEL_PREFIXES = (
    "Procedure observed:",
    "Indication:",
    "Clinical reasoning:",
    "Trainee performance:",
)


def _strip_narrative_labels(text: str) -> str:
    out = text or ""
    for prefix in _NARRATIVE_LABEL_PREFIXES:
        out = out.replace(prefix, "")
    return " ".join(out.split())


def _section_body(case_observed: str, header: str) -> str:
    """Return the prose body of a `Header: ...` section, or '' if absent.

    `_build_case_observed_narrative` separates sections with a blank line, so
    the body runs until the next double newline or end of string.
    """
    if not case_observed:
        return ""
    needle = f"{header}:"
    idx = case_observed.find(needle)
    if idx < 0:
        return ""
    start = idx + len(needle)
    end = case_observed.find("\n\n", start)
    return case_observed[start:end if end >= 0 else None].strip()


def _is_thin_case_observed(case_observed: str) -> bool:
    """True if the DOM narrative slot has too little real prose to be useful."""
    cleaned = _strip_narrative_labels(case_observed)
    words = [w for w in cleaned.split() if any(c.isalpha() for c in w)]
    return len(cleaned) < 50 or len(words) < 8


_INCOHERENT_REFLECTION_MIN_CHARS = 20
_INCOHERENT_REFLECTION_MIN_WORDS = 4


def _is_incoherent_reflection(text: str) -> bool:
    """Heuristic for reflections too short or fragmentary to file. The user
    sees a clear ask instead of an embarrassing entry on Kaizen."""
    if not text:
        return False
    cleaned = text.strip()
    if len(cleaned) < _INCOHERENT_REFLECTION_MIN_CHARS:
        return True
    if len(cleaned) < 60 and cleaned == cleaned.upper() and any(c.isalpha() for c in cleaned):
        return True
    words = [w for w in cleaned.split() if any(c.isalpha() for c in w)]
    if len(words) < _INCOHERENT_REFLECTION_MIN_WORDS:
        return True
    return False


def dops_quality_gate(fields: dict) -> list[str]:
    """Return every required Kaizen DOM field that would be empty or thin on save.

    Run AFTER `normalise_dops_fields`. A non-empty result is the full list
    of quality issues — useful for warning the user before they tap Save.
    `dops_blocking_misses` then decides which of those issues are severe
    enough to refuse the save outright.

    Checks beyond bare presence:
      - `case_observed` is "thin" (label-only stub like
        "Procedure observed: DC cardioversion").
      - The indication / trainee-performance semantic blocks are missing
        both as explicit fields and as labelled sections in `case_observed`.
      - `reflection`, if present, is incoherent (too short or fragmented).
    """
    fields = fields or {}
    missing: list[str] = []
    for label, keys in DOPS_REQUIRED_LABELS:
        if not any(_has_value(fields.get(k)) for k in keys):
            missing.append(label)

    case_observed = _str(fields.get("case_observed"))
    if case_observed and "Case observed narrative" not in missing:
        if _is_thin_case_observed(case_observed):
            missing.append("Case observed narrative")

    indication_field = _str(fields.get("indication"))
    indication_body = _section_body(case_observed, "Indication")
    if not indication_field and len(indication_body) < 12:
        missing.append("Indication")

    trainee_field = _str(fields.get("trainee_performance"))
    trainee_body = _section_body(case_observed, "Trainee performance")
    if not trainee_field and len(trainee_body) < 12:
        missing.append("Trainee performance")

    reflection = _str(fields.get("reflection"))
    if reflection and _is_incoherent_reflection(reflection):
        missing.append("Reflection (needs clearer wording)")

    return missing


def dops_blocking_misses(
    fields: dict,
    gate_misses: list[str] | None = None,
) -> list[str]:
    """Return the subset of `dops_quality_gate` misses that must block save.

    The user has explicitly approved the draft. Only refuse the save when
    the resulting Kaizen draft would be genuinely unsafe to put in front of
    an assessor:

      - No procedure name at all — it isn't a DOPS without one.
      - The narrative slot has no clinical substance at all: case_observed
        is missing/thin AND both indication and trainee_performance are
        also empty.

    Everything else (missing date, missing stage, single missing semantic
    block, roughly worded reflection) is a warning, not a blocker — Kaizen
    accepts the partial draft and the user can polish it there.
    """
    misses = list(gate_misses) if gate_misses is not None else dops_quality_gate(fields)
    blocking: list[str] = []
    if "Procedural skill" in misses:
        blocking.append("Procedural skill")

    narrative_labels = ("Case observed narrative", "Indication", "Trainee performance")
    if all(label in misses for label in narrative_labels):
        for label in narrative_labels:
            blocking.append(label)
    return blocking


# ─── KC breadth supplementer ─────────────────────────────────────────────────


# Each trigger list is intentionally tight — only words that genuinely imply
# the KC. Generic words like "patient" or "ED" do not appear because they
# would fire for every case and inflate KC counts without evidence.
DOPS_KC_BREADTH_TRIGGERS: dict[str, tuple[str, ...]] = {
    "SLO3 KC2": (
        "unstable", "hypotens", "hypoperfus", "perfusion",
        "fluid resus", "vasopressor", "noradrenaline", "metaraminol",
        "circulatory support", "rvr", "rapid ventricular",
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
    ),
    "SLO6 KC2": (
        "sedation", "ketamine", "propofol", "midazolam",
        "cardioversion", "cardiovert", "dc cardioversion",
        "intubation", "rsi", "chest drain", "thoracotomy",
        "synchronised shock", "synchronised cardioversion",
    ),
}


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
