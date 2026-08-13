"""Privacy guard for clinical portfolio text.

This module is intentionally deterministic and dependency-light. It is the
production-safe layer that mirrors the UK/NHS supplemental checks proven in the
Medic OpenMed smoke harness, without requiring the OpenMed model to load inside
the Telegram bot request path.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrivacyFinding:
    label: str
    start: int
    end: int
    risk: str = "high"


@dataclass(frozen=True)
class PrivacyRule:
    label: str
    pattern: re.Pattern[str]
    replacement: str
    group: int = 0
    risk: str = "high"


PRIVACY_RULES: tuple[PrivacyRule, ...] = (
    PrivacyRule(
        "NHS_NUMBER",
        re.compile(r"\b(?:NHS\s*(?:No|Number)?\s*:?\s*)?\d{3}\s*\d{3}\s*\d{4}\b", re.IGNORECASE),
        "[NHS number]",
    ),
    PrivacyRule(
        "MRN",
        re.compile(r"\b(?:MRN\s*:\s*)?MRN[-\s]*[A-Z0-9-]+\b", re.IGNORECASE),
        "[MRN]",
    ),
    PrivacyRule(
        "MRN",
        re.compile(r"\bwristband\s+[A-Z0-9-]+\b", re.IGNORECASE),
        "[wristband identifier]",
    ),
    PrivacyRule(
        "HOSPITAL_NUMBER",
        re.compile(r"\b(?:Hospital\s+(?:No|Number)\s*:?\s*|hosp#\s*)[A-Z0-9-]+\b", re.IGNORECASE),
        "[hospital number]",
    ),
    PrivacyRule(
        "EMAIL",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
        "[email]",
    ),
    PrivacyRule(
        "PHONE",
        re.compile(r"\b(?:\+44\s?|0)(?:\d[\s-]?){9,10}\b"),
        "[phone number]",
    ),
    PrivacyRule(
        "SPOKEN_PHONE",
        re.compile(
            r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine)"
            r"(?:\s+(?:zero|one|two|three|four|five|six|seven|eight|nine|\d{2,4})){4,}\b",
            re.IGNORECASE,
        ),
        "[phone number]",
    ),
    PrivacyRule(
        "DOB",
        re.compile(
            r"\b(?:DOB|date\s+of\s+birth)\s*:?\s*"
            r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+[A-Z][a-z]+\s+\d{4})\b",
            re.IGNORECASE,
        ),
        "[date of birth]",
    ),
    PrivacyRule(
        "POSTCODE",
        re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.IGNORECASE),
        "[postcode]",
    ),
    PrivacyRule(
        "CLINICIAN_NAME",
        re.compile(r"\bDr\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b"),
        "the doctor",
    ),
    PrivacyRule(
        "PATIENT_NAME",
        re.compile(r"\bPatient\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}\b"),
        "the patient",
    ),
    PrivacyRule(
        "PERSON_NAME",
        re.compile(r"\b(?:Mr|Mrs|Ms|Miss)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b"),
        "the patient",
    ),
    # Scanned reports print the patient in block capitals ("report of patient
    # (E R) MUNAWAR AHMED"), which the Title-Case PATIENT_NAME rule above
    # cannot see. Only the strong labels are accepted — a bare "patient"
    # followed by capitals would swallow clinical shorthand like
    # "patient ECG NORMAL".
    PrivacyRule(
        "PATIENT_NAME",
        re.compile(
            r"\b(?:report\s+of\s+patient|patient(?:'s)?\s+name|name\s+of\s+patient)\b"
            r"\s*[:\-]?\s*(?:\([A-Z][A-Z .]*\)\s*)?"
            r"([A-Z][A-Z'\-]{2,}(?:\s+[A-Z][A-Z'\-]{2,}){1,3})",
            re.IGNORECASE,
        ),
        "[patient name]",
        group=1,
    ),
    PrivacyRule(
        "TERTIARY_CENTRE",
        re.compile(
            r"\b(?:Royal Brompton|Great Ormond Street|St Thomas'?|Guy'?s|King'?s College)(?: Hospital)?\b",
            re.IGNORECASE,
        ),
        "a tertiary centre",
    ),
    PrivacyRule(
        "NAMED_HOSPITAL",
        re.compile(
            r"\b[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,4}\s+"
            r"(?:Hospital|Infirmary|ED)\b"
        ),
        "the hospital",
    ),
    PrivacyRule(
        "NAMED_WARD",
        re.compile(r"\b[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,3}\s+(?:Ward|Assessment Unit)\b"),
        "the ward",
    ),
    PrivacyRule(
        "NAMED_WARD",
        re.compile(r"\bWard\s+[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,3}\b"),
        "the ward",
    ),
    PrivacyRule(
        "CARE_HOME",
        re.compile(r"\b(?:[A-Z][A-Za-z'-]+\s+){0,4}Care Home\b"),
        "a care home",
    ),
    PrivacyRule(
        "ED_LOCATION",
        re.compile(r"\b(?:[A-Z][A-Za-z'-]+\s+){0,4}(?:Bay\s+\d+|Resus\s+\d+|relatives room)\b"),
        "a clinical area",
    ),
    PrivacyRule(
        "RARE_CASE_DETAIL",
        re.compile(r"\bonly\s+([a-z][A-Za-z-]+(?:\s+[a-z][A-Za-z-]+){2,8})\s+in\s+[A-Z]", re.IGNORECASE),
        "a rare identifying background detail",
        group=1,
    ),
    PrivacyRule(
        "RARE_CASE_DETAIL",
        re.compile(r"\bsuspected\s+imported\s+[A-Z][A-Za-z-]+(?:\s+[a-z][A-Za-z-]+)?\b"),
        "a rare identifying diagnosis/travel combination",
    ),
)


def deidentify_clinical_text(text: str) -> tuple[str, list[PrivacyFinding]]:
    """Return de-identified text plus structured findings.

    Findings carry labels and offsets only. Callers should not log matched
    values, because the whole point is to keep identifiers out of logs.
    """
    if not text or len(text) < 3:
        return text, []

    candidates: list[tuple[int, int, str, PrivacyFinding]] = []
    for rule in PRIVACY_RULES:
        for match in rule.pattern.finditer(text):
            start = match.start(rule.group)
            end = match.end(rule.group)
            if start < 0 or end <= start:
                continue
            finding = PrivacyFinding(rule.label, start, end, rule.risk)
            candidates.append((start, end, rule.replacement, finding))

    if not candidates:
        return text, []

    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, str]] = []
    selected_findings: list[PrivacyFinding] = []
    covered_until = -1
    for start, end, replacement, finding in candidates:
        if start < covered_until:
            continue
        selected.append((start, end, replacement))
        selected_findings.append(finding)
        covered_until = end

    pieces: list[str] = []
    cursor = 0
    for start, end, replacement in selected:
        pieces.append(text[cursor:start])
        pieces.append(replacement)
        cursor = end
    pieces.append(text[cursor:])
    redacted = re.sub(r" {2,}", " ", "".join(pieces)).strip()
    return redacted, selected_findings


def deidentify_draft_fields(fields: dict) -> tuple[dict, list[str]]:
    """De-identify every string value in a draft, whatever the field is called.

    The humanizer only reaches fields listed in ``_HUMANIZE_FIELDS`` and only
    when the value is over 20 characters, so short and non-narrative fields
    were never de-identified. A photo of a report produced the title
    "Echocardiogram Review - Munawar Ahmed" and it went through untouched,
    because ``reflection_title`` is not a narrative field.

    Every field a doctor can see is a field Kaizen will store, so the sweep is
    unconditional. Returns (fields, labels_found) with ``fields`` mutated in
    place; labels are rule names only, never matched values.
    """
    if not isinstance(fields, dict):
        return fields, []

    labels: list[str] = []

    def _clean(value: str) -> str:
        redacted, findings = deidentify_clinical_text(value)
        labels.extend(finding.label for finding in findings)
        return redacted

    for key, value in list(fields.items()):
        if isinstance(value, str) and value.strip():
            cleaned = _clean(value)
            if cleaned != value:
                fields[key] = cleaned
        elif isinstance(value, list):
            new_items = [
                _clean(item) if isinstance(item, str) and item.strip() else item
                for item in value
            ]
            if new_items != value:
                fields[key] = new_items

    return fields, sorted(set(labels))


# ── Optional model-based name detection (local sidecar) ──────────────────────
# The rules above cannot see an unlabelled name: "chatted to Sarah about it"
# has no label to anchor on, and guessing from capitalisation would delete
# clinical terms. A small NER model can, and runs in a separate local service
# (services/phi-name) so no ML dependency reaches this process.
#
# Off unless PG_ENABLE_MODEL_NAME_SCRUB is set, matching the opt-in pattern of
# PG_ENABLE_BROWSER_USE_FALLBACK in filer_router.py.

_NAME_SERVICE_URL = os.environ.get(
    "PG_NAME_SERVICE_URL", "http://127.0.0.1:18810/names"
)
_NAME_SERVICE_TIMEOUT = float(os.environ.get("PG_NAME_SERVICE_TIMEOUT", "1.5"))


def model_name_scrub_enabled() -> bool:
    return os.environ.get("PG_ENABLE_MODEL_NAME_SCRUB", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


_service_failures = 0


def service_failure_count() -> int:
    """How many sidecar calls have failed since this process started.

    Callers sample it either side of an extraction to learn whether cover was
    reduced for that draft. A counter rather than a ContextVar because
    ``asyncio.wait_for`` wraps the extractor in a Task, and a Task gets a *copy*
    of the context — anything set inside is invisible to the caller.

    A module-level counter is shared across users, which is correct here: the
    sidecar is up or down for everyone, so another request's failure is real
    evidence that this draft's cover was reduced too.
    """
    return _service_failures


def _note_service_failure() -> None:
    global _service_failures
    _service_failures += 1


def model_person_names(text: str) -> tuple[list[str], bool]:
    """Ask the local sidecar for person names. Returns (names, service_available).

    Blocking, by design: callers run it in an executor so the bot's event loop
    is never held. Any failure — service down, timeout, malformed reply — comes
    back as ``([], False)`` rather than an exception, because a privacy helper
    must never be the reason a doctor cannot file. The caller is responsible for
    telling the user that checking was degraded; failing quietly *and* silently
    would be the worst outcome.
    """
    if not model_name_scrub_enabled() or not str(text or "").strip():
        return [], True

    payload = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(
        _NAME_SERVICE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_NAME_SERVICE_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - every failure degrades the same way
        _note_service_failure()
        logger.warning("name service unavailable: %s", type(exc).__name__)
        return [], False

    names = body.get("names")
    if not isinstance(names, list):
        _note_service_failure()
        logger.warning("name service returned an unexpected payload shape")
        return [], False

    cleaned = [
        " ".join(str(name).split()).strip()
        for name in names
        if isinstance(name, str) and str(name).strip()
    ]
    return sorted(set(cleaned), key=len, reverse=True), True


def apply_model_names(text: str, names: Iterable[str]) -> tuple[str, list[str]]:
    """Replace model-detected names in `text`, mirroring the rules' replacement."""
    labels: list[str] = []
    out = text
    for name in names:
        pattern = re.compile(r"\b%s\b" % re.escape(name), re.IGNORECASE)
        if pattern.search(out):
            out = pattern.sub("the patient", out)
            labels.append("PATIENT_NAME")
    if not labels:
        return text, []
    out = re.sub(r" {2,}", " ", out)
    # Defence in depth for a split name the service did not merge: two adjacent
    # replacements would otherwise read "the patient the patient".
    out = re.sub(r"\b(the patient)(\s+the patient)+\b", r"\1", out, flags=re.IGNORECASE)
    return out.strip(), labels


def privacy_summary(texts: Iterable[str]) -> dict:
    """Return a PHI-safe summary for preflight gates."""
    all_findings: list[PrivacyFinding] = []
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            continue
        _redacted, findings = deidentify_clinical_text(text)
        all_findings.extend(findings)

    high_risk = [finding for finding in all_findings if finding.risk == "high"]
    return {
        "status": "blocked" if high_risk else "clear",
        "finding_count": len(all_findings),
        "high_risk_count": len(high_risk),
        "labels": dict(sorted(Counter(finding.label for finding in all_findings).items())),
    }
