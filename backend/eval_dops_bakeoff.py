#!/usr/bin/env python3
"""DOPS bake-off — compare extraction providers on the WPBA fields that
actually matter for a Kaizen DOPS draft (procedure, indication, trainee
performance, reflection, KC links, grammar).

This is a lightweight wrapper around the existing model_pathways eval that
focuses scoring on the DOPS-specific quality dimensions Moeed flagged from
dogfood: thin narrative, missing indication, missing trainee performance,
fragmented reflection.

The deterministic scoring function (`score_dops_extraction`) is unit-tested
in `tests/test_eval_dops_bakeoff.py`. Live provider calls require
GOOGLE_API_KEY and DEEPSEEK_API_KEY and are intentionally not part of the
offline gate.

Usage (live, with creds exported via BWS):

    cd ~/projects/portfolio-guru
    python3 backend/eval_dops_bakeoff.py --case 3 \\
        --providers deepseek-v4,gemini-3-5-flash
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time as _time
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from model_config import (
    gemini_fast_model,
    gemini_stable_model,
    gemini_three_five_flash_model,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("dops-eval")


# ─── Scoring (offline-testable, no provider calls) ────────────────────────────


def _density_score(text: str, *, min_chars: int, ideal_chars: int) -> float:
    """Score a free-text field by length, capped at 1.0.

    Anything shorter than `min_chars` is treated as effectively missing and
    scores 0.0. Length scales linearly up to `ideal_chars` for the full 1.0.
    """
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) < min_chars:
        return 0.0
    return min(1.0, len(cleaned) / ideal_chars)


_GRAMMAR_MIN_CHARS = 20


def _grammar_score(text: str) -> float:
    """Heuristic grammar/coherence score in [0, 1]. Looks at sentence count,
    capitalised starts, and average sentence length. Fragments or all-caps
    rants score near 0.
    """
    cleaned = (text or "").strip()
    if len(cleaned) < _GRAMMAR_MIN_CHARS:
        return 0.0
    if cleaned == cleaned.upper() and any(c.isalpha() for c in cleaned):
        return 0.2
    sentences = [s.strip() for s in re.split(r"[.!?]+", cleaned) if s.strip()]
    if not sentences:
        return 0.0
    well_formed = 0
    for s in sentences:
        words = s.split()
        if len(words) >= 4 and s[0].isupper():
            well_formed += 1
    return min(1.0, well_formed / max(1, len(sentences)))


_DOPS_SCORE_DIMENSIONS = (
    "procedure",
    "indication",
    "trainee_performance",
    "reflection",
    "kc_links",
    "grammar",
)


def score_dops_extraction(fields: dict) -> dict:
    """Return per-dimension scores and an overall mean for a DOPS extraction.

    Dimensions (each scored 0.0–1.0):
      - procedure: procedure_name or procedural_skill present
      - indication: substantive indication text
      - trainee_performance: substantive trainee performance text
      - reflection: substantive reflection text
      - kc_links: count of key_capabilities (full credit at >= 4)
      - grammar: heuristic coherence on reflection + trainee_performance

    `overall` is the unweighted mean of the six dimensions.
    """
    fields = fields or {}
    procedure = (fields.get("procedure_name") or fields.get("procedural_skill") or "").strip()
    indication = (fields.get("indication") or "").strip()
    trainee_performance = (fields.get("trainee_performance") or "").strip()
    reflection = (fields.get("reflection") or "").strip()
    kcs = fields.get("key_capabilities") or []

    scores = {
        "procedure": 1.0 if procedure else 0.0,
        "indication": _density_score(indication, min_chars=20, ideal_chars=80),
        "trainee_performance": _density_score(trainee_performance, min_chars=25, ideal_chars=120),
        "reflection": _density_score(reflection, min_chars=20, ideal_chars=150),
        "kc_links": min(1.0, len(kcs) / 4.0),
        "grammar": _grammar_score(" ".join([reflection, trainee_performance])),
    }
    scores["overall"] = round(
        sum(scores[k] for k in _DOPS_SCORE_DIMENSIONS) / len(_DOPS_SCORE_DIMENSIONS),
        3,
    )
    return scores


# ─── DOPS extraction prompt ───────────────────────────────────────────────────


def _dops_extraction_prompt(case_text: str) -> str:
    today = date.today().isoformat()
    return f"""You are a medical portfolio assistant. Extract structured data
from a clinical case description for a Direct Observation of Procedural
Skills (DOPS) WPBA entry. Today's date: {today}.

Return ONLY a JSON object with:
{{
  "form_type": "DOPS",
  "date_of_encounter": "YYYY-MM-DD",
  "stage_of_training": "Higher/ST4-ST6",
  "clinical_setting": "ED setting",
  "procedure_name": "procedure performed",
  "indication": "why the procedure was needed",
  "trainee_performance": "what the trainee did, in their own voice",
  "reflection": "what was learned, first-person, specific",
  "curriculum_links": ["SLO3", "SLO6"],
  "key_capabilities": ["SLO3 KC3: full text...", "SLO6 KC2: full text..."]
}}

Rules:
- Indication must explain the clinical reason, not just restate the
  procedure name.
- Trainee performance must be the trainee's own narrative, not generic.
- Reflection is first person, specific, at least three sentences. No AI
  filler ("delve", "crucial", "comprehensive").
- British English. Return ONLY valid JSON, no code fences.

Case: {case_text}"""


# ─── Provider routing ─────────────────────────────────────────────────────────


PROVIDERS = [
    {
        "name": "gemini-fast",
        "caller": "gemini",
        "model": gemini_fast_model,
        "env_key": "GOOGLE_API_KEY",
    },
    {
        "name": "gemini-2.5-flash",
        "caller": "gemini",
        "model": gemini_stable_model,
        "env_key": "GOOGLE_API_KEY",
    },
    {
        "name": "gemini-3-5-flash",
        "caller": "gemini",
        "model": gemini_three_five_flash_model,
        "env_key": "GOOGLE_API_KEY",
    },
    {
        "name": "deepseek-v4",
        "caller": "deepseek",
        "model": lambda: "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
    },
]


def _provider_model(provider: dict) -> str:
    model = provider["model"]
    return model() if callable(model) else model


async def _call_gemini(prompt: str, model: str) -> tuple[str, float]:
    from google import genai

    api_key = os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)
    loop = asyncio.get_event_loop()
    t0 = _time.monotonic()
    response = await loop.run_in_executor(
        None,
        lambda: client.models.generate_content(model=model, contents=prompt),
    )
    return response.text, _time.monotonic() - t0


async def _call_deepseek(prompt: str, model: str) -> tuple[str, float]:
    from openai import OpenAI

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    loop = asyncio.get_event_loop()
    t0 = _time.monotonic()
    response = await loop.run_in_executor(
        None,
        lambda: client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
        ),
    )
    return response.choices[0].message.content, _time.monotonic() - t0


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


async def run_one(provider: dict, case_text: str) -> dict:
    prompt = _dops_extraction_prompt(case_text)
    model = _provider_model(provider)
    try:
        if provider["caller"] == "gemini":
            raw, elapsed = await _call_gemini(prompt, model)
        else:
            raw, elapsed = await _call_deepseek(prompt, model)
        fields = _parse_json(raw)
    except Exception as exc:
        return {
            "provider": provider["name"],
            "model": model,
            "status": "error",
            "error": str(exc)[:300],
        }
    scores = score_dops_extraction(fields)
    return {
        "provider": provider["name"],
        "model": model,
        "status": "ok",
        "elapsed_s": round(elapsed, 2),
        "scores": scores,
        "preview": {
            "procedure": (fields.get("procedure_name") or "")[:60],
            "indication": (fields.get("indication") or "")[:120],
            "trainee_performance": (fields.get("trainee_performance") or "")[:160],
            "reflection": (fields.get("reflection") or "")[:200],
            "kc_count": len(fields.get("key_capabilities") or []),
        },
    }


# ─── Built-in DOPS cases ──────────────────────────────────────────────────────


DOPS_CASES = {
    "chest-drain": (
        "I performed an ultrasound-guided chest drain insertion on a 55-year-"
        "old male with a large right-sided pleural effusion secondary to "
        "pneumonia. Seldinger technique with a 12Fr chest drain, under direct "
        "supervision of the respiratory registrar. Lidocaine 1% (20ml) local. "
        "Confirmed position with ultrasound before and after. 800ml of straw-"
        "coloured fluid drained. No complications. I gained consent and "
        "explained the procedure beforehand."
    ),
    "unstable-af": (
        "ST5 EM higher trainee in the resus room. 62-year-old in unstable AF "
        "with RVR, hypotensive and clammy. Emergency synchronised DC "
        "cardioversion under ketamine sedation. First two shocks did not "
        "capture; third converted briefly then went refractory. Loaded "
        "amiodarone and gave IV magnesium. Bedside echo showed adequate LV. "
        "Escalated early to the med reg and ITU. Patient stabilised."
    ),
}


# ─── Entry point ──────────────────────────────────────────────────────────────


def _select_providers(names: list[str]) -> list[dict]:
    selected = []
    for n in names:
        key = n.strip().lower().replace(".", "-").replace("_", "-")
        for p in PROVIDERS:
            pkey = p["name"].lower().replace(".", "-")
            if key == pkey:
                selected.append(p)
                break
    return selected


async def main_async(args):
    case_text = DOPS_CASES.get(args.case)
    if not case_text:
        logger.error("Unknown case key %s — available: %s", args.case, sorted(DOPS_CASES))
        sys.exit(2)

    providers = _select_providers(args.providers.split(","))
    if not providers:
        logger.error("No matching providers — available: %s", [p["name"] for p in PROVIDERS])
        sys.exit(2)

    providers = [p for p in providers if os.environ.get(p["env_key"])]
    if not providers:
        logger.error("None of the requested providers have their env_key set.")
        sys.exit(2)

    results = []
    for p in providers:
        logger.info("Running %s on case %r…", p["name"], args.case)
        r = await run_one(p, case_text)
        results.append(r)
        if r["status"] == "ok":
            s = r["scores"]
            logger.info(
                "  %s — overall %.2f (proc=%.1f ind=%.2f perf=%.2f refl=%.2f kc=%.2f gram=%.2f) in %.1fs",
                p["name"], s["overall"], s["procedure"], s["indication"],
                s["trainee_performance"], s["reflection"], s["kc_links"],
                s["grammar"], r["elapsed_s"],
            )
        else:
            logger.warning("  %s — %s", p["name"], r["error"])

    out_dir = Path(__file__).parent.parent / "memory" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"dops-bakeoff-{args.case}-{date.today().isoformat()}.json"
    out_path.write_text(json.dumps({"case": args.case, "results": results}, indent=2))
    logger.info("Saved %s", out_path)


def main():
    parser = argparse.ArgumentParser(description="DOPS bake-off across extraction providers")
    parser.add_argument("--case", default="chest-drain", help=f"One of: {sorted(DOPS_CASES)}")
    parser.add_argument(
        "--providers",
        default="deepseek-v4,gemini-3-5-flash",
        help="Comma-separated provider names from the PROVIDERS list",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
