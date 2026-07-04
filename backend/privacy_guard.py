"""Privacy guard for clinical portfolio text.

This module is intentionally deterministic and dependency-light. It is the
production-safe layer that mirrors the UK/NHS supplemental checks proven in the
Medic OpenMed smoke harness, without requiring the OpenMed model to load inside
the Telegram bot request path.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable


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
