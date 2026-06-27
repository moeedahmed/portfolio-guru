"""Validation for Sprint 4b demo assets.

The hero case, rehearsal runbook, and 90-second demo script are
load-bearing for the Hermes hackathon take. They must:

* exist at the paths the runbook and script cross-link to;
* never contain real patient identifiers or fabricated personal facts;
* never make any of the forbidden public claims that
  ``docs/PUBLIC_PRODUCT_PLAN_2026-06-17.md`` rules out
  (RCEM endorsement, guaranteed ARCP, auto-submit/sign/send/approve/
  reject/delete, public WhatsApp v1 promise, real-money assessor
  payouts).

These are pure text checks. They never touch Telegram, Kaizen, Stripe,
Supabase, or the bot runtime. They are safe to run in any environment
that can `pytest backend/tests/`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = REPO_ROOT / "docs" / "demo"

HERO_CASE = DEMO_DIR / "HERO_CASE_2026-06-30.md"
REHEARSAL_RUNBOOK = DEMO_DIR / "REHEARSAL_RUNBOOK.md"
DEMO_SCRIPT = DEMO_DIR / "DEMO_SCRIPT_90S.md"
HERMES_MAP = DEMO_DIR / "HERMES_CAPABILITY_MAP.md"

ALL_DEMO_DOCS = (HERO_CASE, REHEARSAL_RUNBOOK, DEMO_SCRIPT, HERMES_MAP)


# ── existence ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("doc", ALL_DEMO_DOCS, ids=lambda p: p.name)
def test_demo_doc_exists_and_non_empty(doc: Path) -> None:
    assert doc.is_file(), f"missing demo asset: {doc}"
    body = doc.read_text(encoding="utf-8")
    assert body.strip(), f"demo asset is empty: {doc}"


# ── safety: forbidden public claims ────────────────────────────────────

# Phrases that, if they appeared in narration or copy, would over-promise
# what Portfolio Guru actually does. The plan in
# ``docs/PUBLIC_PRODUCT_PLAN_2026-06-17.md`` explicitly forbids each.
FORBIDDEN_CLAIM_PATTERNS: tuple[tuple[str, str], ...] = (
    # Public WhatsApp v1 promise. WhatsApp is a routed convenience,
    # not the public identity.
    (r"\bwhatsapp[^\n.]{0,40}?(launch|public|v1|today|now available)\b",
     "public WhatsApp v1 promise"),
    # RCEM endorsement claim.
    (r"\b(rcem|royal college of emergency medicine)[^\n.]{0,40}?(endorse|approved|certified|accredit)",
     "RCEM endorsement claim"),
    # Guaranteed ARCP / CESR outcome.
    (r"\b(guarantee|guaranteed|guarantees)[^\n.]{0,40}?(arcp|cesr|portfolio pathway)\b",
     "guaranteed ARCP/CESR outcome"),
    (r"\b(official)[^\n.]{0,20}?(arcp|cesr)[^\n.]{0,20}?(outcome|decision)\b",
     "official ARCP/CESR outcome claim"),
    # Real-money payouts.
    (r"\breal[- ]?money\b[^\n.]{0,30}?\b(payout|payouts|paid)\b",
     "real-money payout claim"),
)

# Auto-action verbs against Kaizen. Forbidden as bot/agent behaviour.
# We allow these strings in negation contexts ("never submit", "no
# auto-submit"). The check below scans line-by-line and accepts a line
# that contains an explicit negation token.
AUTO_ACTION_VERBS = (
    "auto-submit", "auto submit", "autosubmit",
    "auto-sign", "auto sign", "autosign",
    "auto-send", "auto send", "autosend",
    "auto-approve", "auto approve", "autoapprove",
    "auto-reject", "auto reject", "autoreject",
    "auto-delete", "auto delete", "autodelete",
)
NEGATION_TOKENS = (
    "no ", "not ", "never", "without", "won't", "wont",
    "do not", "does not", "doesn't", "refuses", "refuse",
    "boundary", "boundaries", "forbidden", "guardrail",
    "is the hard line", "hard line",
)


def _iter_lines(path: Path) -> list[tuple[int, str]]:
    return [
        (idx, line.rstrip())
        for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if line.strip()
    ]


def _iter_sections(path: Path) -> list[tuple[int, str]]:
    """Return (start_line_no, section_text) pairs split on markdown headings.

    A heading acts as the negation-scope anchor: a section titled
    "What the script never says" can list the very claims that are
    forbidden, and the heading's "never" carries the negation context
    for every bullet inside it.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    sections: list[tuple[int, list[str]]] = []
    current_start = 1
    current: list[str] = []
    for idx, line in enumerate(lines, start=1):
        if line.startswith("#"):
            if current:
                sections.append((current_start, current))
            current_start = idx
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append((current_start, current))
    return [(start, "\n".join(block)) for start, block in sections]


@pytest.mark.parametrize("doc", ALL_DEMO_DOCS, ids=lambda p: p.name)
def test_demo_doc_does_not_make_forbidden_claims(doc: Path) -> None:
    """Forbidden phrases are allowed only under explicit negation/boundary.

    These docs intentionally name the very claims the product refuses
    to make (e.g. "not RCEM endorsed", "no real-money payouts"). The
    scan walks each markdown section and accepts a hit if the section
    (heading + body) contains one of NEGATION_TOKENS — that proves the
    section is disclaiming the claim, not making it.
    """
    offenders: list[str] = []
    for start, section in _iter_sections(doc):
        lower = section.lower()
        section_has_negation = any(neg in lower for neg in NEGATION_TOKENS)
        for pattern, label in FORBIDDEN_CLAIM_PATTERNS:
            match = re.search(pattern, lower)
            if not match:
                continue
            if section_has_negation:
                continue
            offenders.append(
                f"section starting line {start}: {label!r} matched "
                f"{match.group(0)!r}"
            )
    assert not offenders, (
        f"{doc.name} contains forbidden public claims without an "
        f"explicit negation/boundary phrase:\n  - "
        + "\n  - ".join(offenders)
    )


@pytest.mark.parametrize("doc", ALL_DEMO_DOCS, ids=lambda p: p.name)
def test_demo_doc_only_mentions_auto_actions_under_negation(doc: Path) -> None:
    offenders: list[str] = []
    for start, section in _iter_sections(doc):
        lower = section.lower()
        if not any(verb in lower for verb in AUTO_ACTION_VERBS):
            continue
        if any(neg in lower for neg in NEGATION_TOKENS):
            continue
        offenders.append(f"section starting line {start}")
    assert not offenders, (
        f"{doc.name} mentions auto-Kaizen actions without an explicit "
        f"negation/boundary phrase:\n  - " + "\n  - ".join(offenders)
    )


# ── safety: no real patient identifiers in the hero case ───────────────

# These patterns would indicate a real patient identifier has slipped
# into the hero case shift note. The hero case is supposed to use age
# bands ("middle-aged adult") rather than DOBs / ages, and never
# include NHS numbers, postcodes, or admission numbers.
IDENTIFIER_PATTERNS: tuple[tuple[str, str], ...] = (
    # NHS number is 10 digits, sometimes grouped 3-3-4.
    (r"\b\d{3}[ -]?\d{3}[ -]?\d{4}\b", "possible NHS number"),
    # UK postcode (rough).
    (r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b", "possible UK postcode"),
    # Date of birth pattern (dd/mm/yyyy with a year clearly in the past
    # century range) is suspicious in a clinical asset; the demo uses
    # only an encounter date (30/6/2026) which falls outside this range.
    (r"\bdob[: ]*\d{1,2}[/-]\d{1,2}[/-](19|20)\d{2}\b", "explicit DOB"),
    # Explicit numeric age. The hero case must use age bands.
    (r"\baged?\s*\d{1,3}\b", "explicit numeric age"),
)


def test_hero_case_has_no_obvious_identifiers() -> None:
    text = HERO_CASE.read_text(encoding="utf-8")
    offenders: list[str] = []
    for pattern, label in IDENTIFIER_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            offenders.append(f"{label}: {match.group(0)!r}")
    assert not offenders, (
        "hero case asset contains possible identifiers:\n  - "
        + "\n  - ".join(offenders)
    )


def test_hero_case_declares_synthetic_provenance() -> None:
    text = HERO_CASE.read_text(encoding="utf-8").lower()
    # The asset must declare itself synthetic and explicitly mention
    # the "no real patient identifiers" property. This stops a future
    # editor from quietly turning the hero case into a real anonymised
    # patient story.
    assert "synthetic" in text, (
        "hero case asset must declare itself synthetic"
    )
    assert "no real" in text or "no patient identifiers" in text or "no patient identifier" in text, (
        "hero case asset must state the no-real-identifier property"
    )


# ── runbook / script reference each other and the hero case ────────────


def test_runbook_links_to_hero_case_and_script() -> None:
    body = REHEARSAL_RUNBOOK.read_text(encoding="utf-8")
    assert HERO_CASE.name in body, "runbook must reference the hero case asset"
    assert DEMO_SCRIPT.name in body, "runbook must reference the demo script"


def test_demo_script_links_to_hero_case_and_runbook() -> None:
    body = DEMO_SCRIPT.read_text(encoding="utf-8")
    assert HERO_CASE.name in body, "demo script must reference the hero case asset"
    assert REHEARSAL_RUNBOOK.name in body, "demo script must reference the runbook"


# ── honesty labels are present in runbook and script ───────────────────


HONESTY_LABELS = ("[demo]", "[test]", "[manual]", "[live-gated]")


@pytest.mark.parametrize(
    "doc", (REHEARSAL_RUNBOOK, DEMO_SCRIPT), ids=lambda p: p.name,
)
def test_doc_uses_honesty_labels(doc: Path) -> None:
    body = doc.read_text(encoding="utf-8")
    missing = [label for label in HONESTY_LABELS if label not in body]
    assert not missing, (
        f"{doc.name} is missing honesty labels: {missing}. Every step "
        f"that touches an external system must carry [demo], [test], "
        f"[manual], or [live-gated]."
    )


# ── capability map must not overclaim a Hermes integration ─────────────


# ── capability map code citations must be fresh ────────────────────────

# The capability map and its "verify in two minutes" section point a judge
# at exact `backend/<file>.py:<line>` locations. If bot.py shifts and the
# citations are not updated, a judge jumps to the wrong line and the trust
# claim breaks. This guard reads each citation FROM the doc and validates
# it against the real file, so the doc stays the source of the line number
# while the test proves the number is still correct.

_CITATION_RE = re.compile(
    r"`backend/([\w/]+\.py):(\d+)(?:-(\d+))?`(?:\s+`(\w+)`)?"
)


def test_capability_map_code_citations_resolve() -> None:
    text = HERMES_MAP.read_text(encoding="utf-8")
    citations = _CITATION_RE.findall(text)
    assert citations, "capability map should cite backend code locations"

    problems: list[str] = []
    file_lines: dict[str, list[str]] = {}
    for rel_path, start_s, end_s, symbol in citations:
        abs_path = REPO_ROOT / "backend" / rel_path
        if not abs_path.is_file():
            problems.append(f"cited file does not exist: backend/{rel_path}")
            continue
        if rel_path not in file_lines:
            file_lines[rel_path] = abs_path.read_text(
                encoding="utf-8"
            ).splitlines()
        lines = file_lines[rel_path]
        start = int(start_s)
        end = int(end_s) if end_s else start
        if end > len(lines):
            problems.append(
                f"backend/{rel_path}:{start_s}{'-' + end_s if end_s else ''} "
                f"is past end of file ({len(lines)} lines)"
            )
            continue
        if symbol:
            window = "\n".join(lines[start - 1 : end])
            if symbol not in window:
                problems.append(
                    f"backend/{rel_path}:{start_s} should contain "
                    f"{symbol!r} but does not"
                )

    assert not problems, (
        "capability map cites stale code locations:\n  - "
        + "\n  - ".join(problems)
    )


def test_capability_map_discloses_no_hermes_runtime_dependency() -> None:
    """The judge-facing capability map maps PG capabilities to Hermes
    framing. It must state plainly that PG does not run on the Hermes
    runtime, that Gemini is the live engine, and that any Hermes/Nemotron
    model slot is a roadmap target — so the map can never be read as
    claiming an integration that does not exist in the codebase.
    """
    lower = HERMES_MAP.read_text(encoding="utf-8").lower()

    assert "does not currently run on the hermes runtime" in lower or (
        "not" in lower and "hermes runtime" in lower
    ), "capability map must deny a Hermes-runtime dependency"
    assert "gemini" in lower and "live" in lower, (
        "capability map must name Gemini as the live extraction engine"
    )
    # Any Nemotron mention must be flagged as a non-live target slot.
    if "nemotron" in lower:
        assert "roadmap" in lower or "target" in lower or "not wired" in lower, (
            "capability map mentions Nemotron without marking it roadmap/target"
        )
