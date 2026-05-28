"""Conservative vNext text → CaseFact extractor.

Extended in slice 5 to pull more clinically useful source-tied facts:
setting, presenting complaint, diagnosis, procedure, supervision, and
learning point — all matched verbatim from the doctor's own text.

Nothing here calls the network, an LLM, or any pre-existing
``extractor.py`` pipeline, and nothing here is invoked for
voice/image/document inputs — those stay stricter and unconfirmed.

Source-tied invariant
---------------------
Every emitted value either appears verbatim in the source text or is
a normalised form whose core word appears verbatim (sex: ``M``/``F``
from ``male``/``man``/``62M`` etc.). When a pattern has no match the
corresponding key is omitted. Empty input returns an empty tuple.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Demographics
# ---------------------------------------------------------------------------

_SHORTHAND_AGE_SEX_RE = re.compile(r"\b(\d{1,3})\s?([MmFf])\b")
_AGE_YEAR_OLD_RE = re.compile(r"\b(\d{1,3})[\s\-]year[\s\-]old\b", re.IGNORECASE)
_SEX_PHRASE_RE = re.compile(
    r"\b(male|female|man|woman|boy|girl|gentleman|lady)\b", re.IGNORECASE
)

_MALE_WORDS: frozenset[str] = frozenset({"male", "man", "boy", "gentleman"})
_FEMALE_WORDS: frozenset[str] = frozenset({"female", "woman", "girl", "lady"})

_MAX_AGE = 120

# ---------------------------------------------------------------------------
# Setting — case-sensitive for short uppercase acronyms, IGNORECASE for phrases
# ---------------------------------------------------------------------------

_SETTING_UPPER_RE = re.compile(r"\b(ED|ICU|ITU|HDU|CCU|AMU|SAU)\b")
_SETTING_PHRASE_RE = re.compile(
    r"\b(resus|emergency\s+department|theatres?|major\s+trauma|"
    r"trauma\s+bay|coronary\s+care|acute\s+medical|outpatient)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Presenting complaint — common EM chief complaints, matched verbatim
# ---------------------------------------------------------------------------

_COMPLAINT_RE = re.compile(
    r"\b("
    r"chest\s+pain|chest\s+tightness|chest\s+discomfort|"
    r"shortness\s+of\s+breath|dyspnoea|dyspnea|"
    r"abdominal\s+pain|epigastric\s+pain|"
    r"headache|head\s+injury|"
    r"altered\s+consciousness|decreased\s+GCS|"
    r"syncope|collapse|falls?|"
    r"palpitations|cardiac\s+arrest|"
    r"polytrauma|trauma|"
    r"seizure|"
    r"fever|pyrexia|"
    r"back\s+pain|"
    r"leg\s+pain|leg\s+swelling|"
    r"haemoptysis|haematemesis|"
    r"vomiting"
    r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Diagnosis — common EM diagnoses, matched verbatim
# ---------------------------------------------------------------------------

_DIAGNOSIS_RE = re.compile(
    r"\b("
    r"STEMI|NSTEMI|ACS|"
    r"PE|pulmonary\s+embolism|"
    r"sepsis|septic\s+shock|"
    r"pneumonia|pneumothorax|"
    r"stroke|TIA|"
    r"DKA|HHS|"
    r"meningitis|"
    r"appendicitis|"
    r"bowel\s+obstruction|"
    r"aortic\s+dissection|"
    r"anaphylaxis|"
    r"overdose|"
    r"hyperkalaemia|hypokalaemia|hyponatraemia|"
    r"atrial\s+fibrillation|"
    r"COPD|asthma|"
    r"heart\s+failure|"
    r"ectopic\s+pregnancy|"
    r"fracture|"
    r"haemorrhage"
    r")\b",
    re.IGNORECASE,
)

# Short case-sensitive acronyms for diagnoses (AF, VT, VF, MI) that would
# produce too many false positives with IGNORECASE.
_DIAGNOSIS_UPPER_RE = re.compile(r"\b(AF|VT|VF|MI)\b")

# ---------------------------------------------------------------------------
# Procedure / intervention — matched verbatim
# ---------------------------------------------------------------------------

_PROCEDURE_RE = re.compile(
    r"\b("
    r"RSI|"
    r"intubat(?:ion|ed)|"
    r"cath(?:eter)?\s+lab|PCI|angioplasty|"
    r"central\s+line|CVC|arterial\s+line|"
    r"chest\s+drain|"
    r"lumbar\s+puncture|"
    r"fascia\s+iliaca|femoral\s+nerve\s+block|nerve\s+block|"
    r"cardioversion|DC\s+cardioversion|"
    r"CPR|ALS|"
    r"POCUS|FAST\s+scan|"
    r"thrombolysis|"
    r"massive\s+transfusion|blood\s+transfusion|"
    r"procedural\s+sedation|"
    r"surgical\s+review|surgical\s+referral"
    r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Supervision / seniority — verbatim level word from source
# ---------------------------------------------------------------------------

_SUPERVISION_RE = re.compile(
    r"\b(consultant|registrar|independently|unsupervised|alone)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Learning point / reflection trigger
# ---------------------------------------------------------------------------

_LEARNING_RE = re.compile(
    r"("
    r"learn(?:ed|t|ing)\s+(?:to|that|about|how)\s+\w[\w\s,;\-]{3,50}|"
    r"key\s+learning[\s:;]+\w[\w\s,;\-]{3,40}|"
    r"learning\s+point[\s:;]+\w[\w\s,;\-]{3,40}|"
    r"reflect(?:ed|ion|ing)\s+on\s+\w[\w\s,;\-]{3,40}"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_text_facts(text: str) -> tuple[tuple[str, str], ...]:
    """Return source-tied ``(key, value)`` facts from ``text``.

    Every emitted value is verbatim (or case-normalised from a verbatim
    source token) — the function never infers, fills gaps, or calls any
    external service. Returns an empty tuple for empty or whitespace-only
    input.
    """
    if not text or not text.strip():
        return ()

    facts: list[tuple[str, str]] = []
    facts.extend(_extract_demographics(text))
    facts.extend(_extract_setting(text))
    facts.extend(_extract_presenting_complaint(text))
    facts.extend(_extract_diagnosis(text))
    facts.extend(_extract_procedure(text))
    facts.extend(_extract_supervision(text))
    facts.extend(_extract_learning_point(text))
    return tuple(facts)


# ---------------------------------------------------------------------------
# Internal extractors
# ---------------------------------------------------------------------------


def _extract_demographics(text: str) -> list[tuple[str, str]]:
    shorthand = _SHORTHAND_AGE_SEX_RE.search(text)
    if shorthand and _is_plausible_age(shorthand.group(1)):
        return [("age", shorthand.group(1)), ("sex", shorthand.group(2).upper())]

    age_match = _AGE_YEAR_OLD_RE.search(text)
    if not age_match or not _is_plausible_age(age_match.group(1)):
        return []

    facts: list[tuple[str, str]] = [("age", age_match.group(1))]
    tail = text[age_match.end(): age_match.end() + 40]
    sex_match = _SEX_PHRASE_RE.search(tail)
    if sex_match:
        normalised = _normalise_sex(sex_match.group(1))
        if normalised:
            facts.append(("sex", normalised))
    return facts


def _extract_setting(text: str) -> list[tuple[str, str]]:
    m = _SETTING_UPPER_RE.search(text)
    if m:
        return [("setting", m.group(1))]
    m = _SETTING_PHRASE_RE.search(text)
    if m:
        return [("setting", m.group(1))]
    return []


def _extract_presenting_complaint(text: str) -> list[tuple[str, str]]:
    m = _COMPLAINT_RE.search(text)
    if m:
        return [("presenting_complaint", m.group(1))]
    return []


def _extract_diagnosis(text: str) -> list[tuple[str, str]]:
    m = _DIAGNOSIS_UPPER_RE.search(text)
    if m:
        return [("diagnosis", m.group(1))]
    m = _DIAGNOSIS_RE.search(text)
    if m:
        return [("diagnosis", m.group(1))]
    return []


def _extract_procedure(text: str) -> list[tuple[str, str]]:
    m = _PROCEDURE_RE.search(text)
    if m:
        return [("procedure", m.group(1))]
    return []


def _extract_supervision(text: str) -> list[tuple[str, str]]:
    m = _SUPERVISION_RE.search(text)
    if m:
        return [("supervision", m.group(1))]
    return []


def _extract_learning_point(text: str) -> list[tuple[str, str]]:
    m = _LEARNING_RE.search(text)
    if not m:
        return []
    span = m.group(1).strip()
    for delim in (".", "\n", ";"):
        idx = span.find(delim)
        if 0 < idx < len(span):
            span = span[:idx].strip()
            break
    if len(span) < 5:
        return []
    return [("learning_point", span)]


def _is_plausible_age(value: str) -> bool:
    try:
        age = int(value)
    except ValueError:
        return False
    return 0 <= age <= _MAX_AGE


def _normalise_sex(word: str) -> str | None:
    lowered = word.lower()
    if lowered in _MALE_WORDS:
        return "M"
    if lowered in _FEMALE_WORDS:
        return "F"
    return None
