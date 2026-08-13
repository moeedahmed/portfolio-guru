"""Local name-detection sidecar for Portfolio Guru.

Why this is a separate service
------------------------------
`backend/privacy_guard.py` closes most of the PHI gap deterministically, but it
cannot see an *unlabelled* name — "chatted to Sarah about it afterwards". A
small NER model can. Measured on the project's eval set, the deterministic
rules catch 1 of 8 unlabelled-name cases; the rules plus this service catch 7 of
8, with zero false positives on real EM prose.

The model does not live in the bot. `backend/requirements.txt` has no ML
dependencies and `scripts/deploy_mac.sh` reinstalls it on every deploy, so
importing torch there would add ~725 MB of RSS to a process that already takes
~3 minutes to start, and would slow every deploy. Keeping the model here means
it loads once, at service start, and the bot stays light — which is the intent
already recorded in `privacy_guard.py`'s own docstring.

Only person-name labels leave this service
------------------------------------------
The model also emits `age`, `time`, `city`, `occupation` and similar. Those are
PII in general but *clinical content* in a portfolio: on the eval set they
turned "58 year old" into "[age]", "reassessed at 30 minutes" into "[time]", and
"Ottawa ankle rules" into "[city]". A threshold sweep (0.5 / 0.7 / 0.9) did not
move them, because they are category errors rather than confidence errors.

So the filter below is an allowlist, not a blocklist. Location is deliberately
excluded even though it costs recall: "Kirkby Lonsdale" (should be removed) and
"Ottawa" (must not be) both come back as `city`, and nothing separates them.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from pydantic import BaseModel

logger = logging.getLogger("phi_name")
logging.basicConfig(level=logging.INFO)

# Person-name labels only. Adding a label here changes what gets deleted from a
# doctor's portfolio evidence — never widen it without re-running the eval set.
NAME_LABELS = frozenset({"first_name", "last_name", "middle_name", "person", "prefix"})

MODEL_NAME = os.environ.get(
    "PHI_NAME_MODEL", "OpenMed/OpenMed-PII-SuperClinical-Small-44M-v1"
)
CONFIDENCE_THRESHOLD = float(os.environ.get("PHI_NAME_THRESHOLD", "0.7"))

app = FastAPI(title="Portfolio Guru name detection", docs_url=None, redoc_url=None)


class NameRequest(BaseModel):
    text: str


class NameResponse(BaseModel):
    names: list[str]
    model: str


@app.on_event("startup")
def _warm_model() -> None:
    """Load and exercise the model once so the first real request is not slow."""
    import openmed

    logger.info("loading %s (threshold %.2f)", MODEL_NAME, CONFIDENCE_THRESHOLD)
    openmed.extract_pii(
        "warmup text for Sarah",
        model_name=MODEL_NAME,
        confidence_threshold=CONFIDENCE_THRESHOLD,
    )
    logger.info("model ready")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": MODEL_NAME}


@app.post("/names", response_model=NameResponse)
def names(request: NameRequest) -> NameResponse:
    """Return the person names found in `text`, longest first.

    Never returns offsets or the surrounding text — the caller does its own
    replacement — and never logs the matched values, matching the rule in
    `privacy_guard.deidentify_clinical_text`.
    """
    import openmed

    text = request.text or ""
    if not text.strip():
        return NameResponse(names=[], model=MODEL_NAME)

    result = openmed.extract_pii(
        text, model_name=MODEL_NAME, confidence_threshold=CONFIDENCE_THRESHOLD
    )
    # The model tags "Munawar Ahmed" as two entities, first_name + last_name.
    # Returned separately they are replaced separately, and the caller's text
    # reads "the patient the patient". Merge spans that are adjacent in the
    # source (touching, or separated only by whitespace) back into one name.
    spans = sorted(
        (e for e in result.entities if e.label in NAME_LABELS),
        key=lambda e: e.start,
    )
    merged: list[tuple[int, int]] = []
    for entity in spans:
        if merged and text[merged[-1][1] : entity.start].strip() == "":
            merged[-1] = (merged[-1][0], entity.end)
        else:
            merged.append((entity.start, entity.end))

    found: list[str] = []
    for start, end in merged:
        value = " ".join(text[start:end].split()).strip(" .,;:-")
        if value and value not in found:
            found.append(value)

    # Longest first so "Munawar Ahmed" is replaced before a bare "Munawar".
    found.sort(key=len, reverse=True)
    logger.info("names found: %d", len(found))
    return NameResponse(names=found, model=MODEL_NAME)
