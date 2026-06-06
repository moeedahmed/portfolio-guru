"""Portfolio type detection and configuration.

Auto-detects the portfolio level (HST, ACCS, Intermediate, Assessor, and
the explicit Non-Trainee variants) from the Kaizen dashboard upon
credential verification.

The detection runs along two independent dimensions:

* **Category** — ``training`` (a CCT trainee with a personal portfolio),
  ``non_training`` (SAS / CESR / Portfolio Pathway / Non-Trainee), or
  ``assessor`` (a Clinical Supervisor with no personal portfolio).
* **Stage** — ``accs`` / ``intermediate`` / ``higher`` for trainees, plus
  the explicit Non-Trainee Higher surface. When the page does not expose
  a stage signal for a non-training shape, the stage stays ``unknown``
  rather than silently defaulting to Higher.

Both dimensions are reported back with the *evidence* (the matched
substrings) so the settings UI can show the user *why* their portfolio
was classified the way it was.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

DOMAIN_SKILL_DIR = Path(__file__).parent.parent / "providers" / "kaizen" / "domain_skill"

Category = Literal["training", "non_training", "assessor", "unknown"]
Stage = Literal["accs", "intermediate", "higher", "unknown"]


@dataclass(frozen=True)
class PortfolioProfile:
    """Structured detection result.

    ``portfolio_type`` is the legacy string surface kept for back-compat
    with the existing setup/login bucket map and form-catalogue picker.
    ``evidence`` lists the verbatim substrings that drove the classification
    so the settings UI can render a transparent ``why`` line.
    """

    category: Category
    stage: Stage
    portfolio_type: str
    evidence: tuple[str, ...] = field(default_factory=tuple)


# Substrings that mark a non-training (SAS / CESR / Portfolio Pathway)
# portfolio. ``non-trainee`` is the Kaizen-visible label on the
# Non-Trainee Higher surface; ``cesr`` / ``portfolio pathway`` cover the
# CESR landing; ``sas`` / ``specialty doctor`` / ``associate specialist``
# cover the SAS labels.
_NON_TRAINING_SIGNALS: tuple[str, ...] = (
    "non-trainee",
    "non-training",
    "non trainee",
    "cesr",
    "portfolio pathway",
    "sas doctor",
    "specialty doctor",
    "associate specialist",
)

# Bare ``sas`` is searched separately because it collides with substrings
# of unrelated words (e.g. ``classes``). Match as a whole token.
_SAS_TOKEN = re.compile(r"\bsas\b")

# Stage substrings — matched on the rendered title or body.
_HIGHER_SIGNALS: tuple[str, ...] = ("higher trainee", "higher")
_INTERMEDIATE_SIGNALS: tuple[str, ...] = ("intermediate",)
_ACCS_SIGNALS: tuple[str, ...] = ("accs trainee", "accs")
_ASSESSOR_SIGNALS: tuple[str, ...] = ("clinical supervisor",)

_DASH_TRANSLATION = str.maketrans({
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
    "\xa0": " ",
})


def _matches(haystack: str, needles: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(needle for needle in needles if needle in haystack)


def _normalise_detection_text(text: str | None) -> str:
    """Return stable lowercase text for Kaizen dashboard signal matching."""
    if not text:
        return ""
    return unicodedata.normalize("NFKC", text).translate(_DASH_TRANSLATION).lower()


def load_selectors() -> dict:
    """Load the Kaizen domain skill selectors."""
    path = DOMAIN_SKILL_DIR / "selectors.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def load_2025_uuids() -> dict:
    """Load 2025 form UUID map."""
    path = DOMAIN_SKILL_DIR / "2025-uuids.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def detect_portfolio_profile(dashboard_title: str, page_text: str) -> PortfolioProfile:
    """Classify a Kaizen dashboard along (category, stage) with evidence.

    The category check runs before the stage check so a Non-Trainee
    Higher account does not get classified as ``training`` just because
    the title carries the ``Higher Trainee`` chrome. A non-training shape
    whose stage cannot be read from the page surfaces as stage
    ``unknown`` — the explicit guardrail against silently mapping the
    account to HST / ACCS / Intermediate or to a stage we cannot verify.
    """
    title = _normalise_detection_text(dashboard_title)
    body = _normalise_detection_text(page_text)
    combined = f"{title}\n{body}"
    evidence: list[str] = []

    non_training_hits = list(_matches(combined, _NON_TRAINING_SIGNALS))
    if _SAS_TOKEN.search(combined):
        non_training_hits.append("sas")

    accs_hits = _matches(combined, _ACCS_SIGNALS)
    intermediate_hits = _matches(combined, _INTERMEDIATE_SIGNALS)
    higher_hits = _matches(combined, _HIGHER_SIGNALS)

    assessor_hits = _matches(combined, _ASSESSOR_SIGNALS)
    title_assessor_hits = _matches(title, _ASSESSOR_SIGNALS)
    personal_portfolio_hits = bool(
        non_training_hits or accs_hits or intermediate_hits or higher_hits
    )
    explicit_body_assessor_hint = "you cannot create any events" in combined
    if assessor_hits and (
        title_assessor_hits
        or (explicit_body_assessor_hint and not personal_portfolio_hits)
    ):
        evidence.extend(assessor_hits)
        return PortfolioProfile(
            category="assessor",
            stage="unknown",
            portfolio_type="assessor",
            evidence=tuple(evidence),
        )

    if non_training_hits:
        evidence.extend(non_training_hits)
        if any(signal in combined for signal in _HIGHER_SIGNALS):
            evidence.extend(_matches(combined, _HIGHER_SIGNALS))
            return PortfolioProfile(
                category="non_training",
                stage="higher",
                portfolio_type="non_training_higher",
                evidence=tuple(evidence),
            )
        # Intermediate / Core stages on non-training accounts are not yet
        # verified from a real Kaizen surface; keep them as ``unknown``
        # rather than inventing a category we cannot defend.
        return PortfolioProfile(
            category="non_training",
            stage="unknown",
            portfolio_type="non_training_unknown",
            evidence=tuple(evidence),
        )

    if accs_hits and intermediate_hits:
        evidence.extend(accs_hits + intermediate_hits)
        return PortfolioProfile(
            category="training",
            stage="accs",
            portfolio_type="accs_intermediate",
            evidence=tuple(evidence),
        )
    if accs_hits:
        evidence.extend(accs_hits)
        return PortfolioProfile(
            category="training",
            stage="accs",
            portfolio_type="accs",
            evidence=tuple(evidence),
        )
    if higher_hits:
        evidence.extend(higher_hits)
        return PortfolioProfile(
            category="training",
            stage="higher",
            portfolio_type="hst",
            evidence=tuple(evidence),
        )
    if intermediate_hits:
        evidence.extend(intermediate_hits)
        return PortfolioProfile(
            category="training",
            stage="intermediate",
            portfolio_type="intermediate",
            evidence=tuple(evidence),
        )

    return PortfolioProfile(
        category="unknown",
        stage="unknown",
        portfolio_type="unknown",
        evidence=tuple(evidence),
    )


def detect_portfolio_type(dashboard_title: str, page_text: str) -> str:
    """Back-compat wrapper around :func:`detect_portfolio_profile`.

    Returns one of:
        ``hst`` / ``accs`` / ``accs_intermediate`` / ``intermediate`` /
        ``non_training_higher`` / ``non_training_unknown`` /
        ``assessor`` / ``unknown``.

    The two ``non_training_*`` strings replace the historical single
    ``sas`` bucket: ``non_training_higher`` when the page exposes the
    Higher stage alongside a non-training marker, ``non_training_unknown``
    otherwise. Downstream catalogues still treat both as the existing
    SAS / Non-Trainee bucket (see ``bot._DETECTED_ROLE_TO_TRAINING_LEVEL``),
    so this change is additive at the detection surface.
    """
    return detect_portfolio_profile(dashboard_title, page_text).portfolio_type


def get_form_types_for_role(portfolio_type: str) -> dict:
    """Get available form types for the detected portfolio role."""
    selectors = load_selectors()
    types = selectors.get("form_types_by_role", {})

    if portfolio_type == "assessor":
        return types.get("assessor", {"note": "Assessors do not create forms"})

    # Trainee roles share the base form set
    base_forms = selectors.get("form_types_by_role", {}).get("hst", {})
    role_specific = types.get(portfolio_type, {})

    if role_specific:
        merged = {**base_forms, **role_specific}
        return merged
    return base_forms


def get_role_config(portfolio_type: str) -> dict:
    """Get full configuration for a portfolio type."""
    configs = {
        "hst": {
            "display_name": "Higher Specialist Trainee",
            "dashboard_label": "Higher Trainee",
            "form_types": get_form_types_for_role("hst"),
        },
        "accs": {
            "display_name": "ACCS Trainee",
            "dashboard_label": "ACCS Trainee",
            "form_types": get_form_types_for_role("accs"),
        },
        "accs_intermediate": {
            "display_name": "ACCS + Intermediate Trainee",
            "dashboard_label": "ACCS Trainee",
            "form_types": get_form_types_for_role("accs"),
        },
        "intermediate": {
            "display_name": "Intermediate Trainee",
            "dashboard_label": "Intermediate Trainee",
            "form_types": get_form_types_for_role("intermediate"),
        },
        "non_training_higher": {
            "display_name": "Non-training (Higher level)",
            "dashboard_label": "Non-Trainee Higher",
            "form_types": get_form_types_for_role("sas"),
        },
        "non_training_unknown": {
            "display_name": "Non-training (level unknown)",
            "dashboard_label": "Non-Trainee",
            "form_types": get_form_types_for_role("sas"),
        },
        "assessor": {
            "display_name": "Clinical Supervisor / Assessor",
            "dashboard_label": "Clinical Supervisor",
            "form_types": get_form_types_for_role("assessor"),
        },
    }
    if portfolio_type == "unknown":
        return configs.get(portfolio_type)
    return configs.get(portfolio_type, configs["hst"])
