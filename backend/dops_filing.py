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


# ─── Field-map adapter ───────────────────────────────────────────────────────


def normalise_dops_fields(fields: dict) -> dict:
    """Return DOPS fields aligned with `FORM_FIELD_MAP["DOPS"]` DOM keys.

    Idempotent. Only fills DOM-aligned keys when they are currently empty —
    user-supplied values (or values from an earlier normalisation pass) are
    preserved exactly.
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
        out["placement"] = clinical_setting

    procedure_name = _str(out.get("procedure_name"))
    procedural_skill = _str(out.get("procedural_skill"))
    if procedure_name and not procedural_skill:
        out["procedural_skill"] = procedure_name
    elif procedural_skill and not procedure_name:
        out["procedure_name"] = procedural_skill

    case_observed = _str(out.get("case_observed"))
    if not case_observed:
        narrative = _build_case_observed_narrative(out)
        if narrative:
            out["case_observed"] = narrative

    return out


def _build_case_observed_narrative(fields: dict) -> str:
    """Combine indication / clinical_reasoning / trainee_performance into one
    DOPS narrative block.

    Labelled sections survive the round-trip into Kaizen so the assessor still
    sees the original structure even though the form only has one text slot.
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
            parts.append(f"Procedure observed: {head}")
    if indication:
        parts.append(f"Indication: {indication}")
    if clinical_reasoning:
        parts.append(f"Clinical reasoning: {clinical_reasoning}")
    if trainee_performance:
        parts.append(f"Trainee performance: {trainee_performance}")
    return "\n\n".join(parts)


# ─── Quality gate ────────────────────────────────────────────────────────────


DOPS_REQUIRED_LABELS = (
    ("Date occurred on", ("date_of_encounter",)),
    ("Stage of training", ("stage_of_training", "stage")),
    ("Procedural skill", ("procedural_skill", "procedure_name")),
    ("Case observed narrative", ("case_observed",)),
)


# Section headers `_build_case_observed_narrative` writes into case_observed.
# Stripping them gives a rough measure of how much real prose the draft
# contains versus label scaffolding.
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
    """True if the DOM narrative slot, with section labels stripped, has
    fewer than 30 characters of real prose — i.e. a label-only stub like
    'Procedure observed: DC cardioversion'."""
    return len(_strip_narrative_labels(case_observed)) < 30


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
    """Return required Kaizen DOM fields that would be empty or thin on save.

    Run AFTER `normalise_dops_fields`. An empty result means the DOPS draft
    is safe to save. A non-empty result means the filer must refuse to claim
    success — saving anyway would create a near-blank or incoherent Kaizen
    draft.

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
