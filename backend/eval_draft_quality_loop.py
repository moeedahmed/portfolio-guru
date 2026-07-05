#!/usr/bin/env python3
"""Synthetic draft-quality loop for Portfolio Guru.

This is an offline QA harness: no Telegram sends, no Kaizen writes, no user
account, and no Supabase product rows. It feeds synthetic scenarios through
the real recommender/extractor path, scores the resulting draft, and writes
reviewable eval artefacts under memory/eval/draft-quality/.
"""

from __future__ import annotations

import argparse
import atexit
import asyncio
import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from extractor import extract_cbd_data, extract_form_data, recommend_form_types, review_draft
from form_display import public_form_name
from form_schemas import FORM_SCHEMAS


EVAL_DIR = Path(__file__).resolve().parent.parent / "memory" / "eval" / "draft-quality"
SOURCE_CYCLE = ("text", "voice", "document", "image")
_TEMP_VERTEX_CREDENTIALS: Path | None = None

AI_TELL_PHRASES = (
    "delve",
    "crucial",
    "importantly",
    "comprehensive",
    "moreover",
    "furthermore",
    "holistic",
    "robust",
    "multifaceted",
    "pivotal",
    "seamless",
    "facilitate",
    "leverage",
    "this case highlights",
    "moving forward",
)

HIGH_RISK_CLAIMS: dict[str, tuple[str, ...]] = {
    "sedation": ("sedation", "sedated", "midazolam", "ketamine", "propofol", "fentanyl"),
    "intubation": ("intubat", "airway", "bougie", "laryngoscope"),
    "cardioversion": ("cardioversion", "synchronised shock", "dc shock"),
    "arrest": ("cardiac arrest", "peri-arrest", "rosc", "cpr"),
    "pci": ("pci", "cath lab", "stemi"),
    "thrombolysis": ("thrombolysis", "alteplase", "tenecteplase"),
    "ct": ("ctpa", "ct head", "ct scan"),
    "ultrasound": ("ultrasound", "pocus", "echo", "efast"),
    "safeguarding": ("safeguarding", "social care", "domestic abuse"),
}


SCENARIO_HINTS: dict[str, str] = {
    "CBD": (
        "Yesterday in ED majors I assessed a 62-year-old man with pleuritic chest pain. "
        "I considered ACS, PE and pneumonia, used Wells score, reviewed ECG and CXR, "
        "discussed with my consultant, and arranged CTPA. I learned to state my risk "
        "assessment more clearly when escalating."
    ),
    "DOPS": (
        "Today I performed DC cardioversion for unstable atrial fibrillation as a formal "
        "DOPS. My consultant observed each step directly. I checked consent, monitoring and "
        "sedation plan, delivered a synchronised shock, and reflected on calling for airway "
        "support earlier."
    ),
    "DOPS_ACCS": (
        "During my ACCS anaesthetic placement I inserted a chest drain using Seldinger "
        "technique as an observed DOPS on 5 July 2026. My supervisor watched the procedure. "
        "I confirmed landmarks, used sterile technique and reflected on slowing down before dilating."
    ),
    "MINI_CEX": (
        "A consultant directly observed me taking a focused history and examination from "
        "a 7-year-old with wheeze in paediatric ED. I explained the plan to the parent and "
        "received feedback on structuring my assessment."
    ),
    "ACAT": (
        "On an evening acute take I managed six patients across majors and resus, including "
        "sepsis, chest pain and a frail fall. My consultant observed my prioritisation, "
        "handover and escalation decisions over the shift."
    ),
    "LAT": (
        "I acted as EPIC for a crowded evening ED shift. I allocated junior doctors, held a "
        "safety huddle, escalated capacity concerns to site management and reflected on "
        "protecting time for re-review of high-risk patients."
    ),
    "ACAF": (
        "I appraised evidence on using age-adjusted D-dimer for suspected PE. I framed a "
        "PICO question, searched NICE and a recent meta-analysis, and discussed how I would "
        "apply the evidence safely in low-risk patients. I learned to document the limits of "
        "the evidence when explaining risk to patients."
    ),
    "STAT": (
        "I delivered a 25-minute teaching session to four junior doctors on ECGs in chest "
        "pain. A consultant observed the session and gave feedback on pacing and checking "
        "understanding."
    ),
    "MSF": (
        "I reviewed feedback from nurses, juniors and consultants. Themes were calm leadership "
        "and clear explanations, with a development point around documenting senior discussions "
        "more consistently."
    ),
    "QIAT": (
        "I led a QI project on time to antibiotics in adult sepsis. Baseline audit was 42 cases, "
        "we introduced a triage prompt and re-audit showed improvement from 58 to 39 minutes. "
        "I presented it at governance. My PDP summary this year is to improve quality improvement "
        "leadership. Next year I will focus on embedding the prompt and supporting re-audit."
    ),
    "JCF": (
        "I presented a journal club on CT coronary angiography in low-risk chest pain. I "
        "summarised the paper, discussed validity and explored how it might change local ED "
        "pathways."
    ),
    "TEACH": (
        "I taught two F1 doctors and a medical student a structured approach to ECG interpretation "
        "using five anonymised ECGs from the teaching file. I collected verbal feedback afterwards."
    ),
    "PROC_LOG": (
        "I performed a shoulder reduction in ED with senior advice available but no formal DOPS. "
        "I documented neurovascular status before and after and reflected on analgesia planning."
    ),
    "PROCEDURAL_LOG_ACCS": (
        "On ACCS I performed a lumbar puncture under supervision. I checked contraindications, "
        "used sterile technique, obtained CSF samples and reflected on patient positioning."
    ),
    "SDL": (
        "I completed a self-directed learning module on paediatric fever pathways, then applied "
        "the learning by reviewing our local traffic-light guidance and changing my safety-netting."
    ),
    "US_CASE": (
        "I performed lung ultrasound on a breathless patient with suspected pulmonary oedema. "
        "I identified bilateral B-lines, discussed images with my consultant and reflected on "
        "integrating ultrasound with clinical assessment."
    ),
    "COMPLAINT": (
        "I reflected on a complaint about delayed communication with a family during a busy resus "
        "shift. The clinical care was appropriate, but I should have allocated a clearer update "
        "role and documented conversations better."
    ),
    "SERIOUS_INC": (
        "I contributed to a serious incident review after delayed recognition of sepsis in majors. "
        "I reviewed the timeline, identified handover gaps and learned to use an explicit trigger "
        "for senior review."
    ),
    "EDU_ACT": (
        "I attended a regional paediatric emergency medicine teaching day covering safeguarding, "
        "sepsis and non-accidental injury. I reflected on using the learning in future paediatric "
        "assessments."
    ),
    "FORMAL_COURSE": (
        "I completed ATLS today. The course refreshed my trauma team approach, primary survey and "
        "communication with the team leader. I will apply the structured handover in resus."
    ),
    "ESLE_ASSESS": (
        "My consultant assessed me during a three-hour evening ED shift. I managed flow in majors, "
        "supported juniors, reviewed two unwell patients and reflected on maintaining situational "
        "awareness under pressure."
    ),
    "REFLECT_LOG": (
        "I managed a breathless patient where I initially anchored on COPD. Senior review prompted "
        "a PE workup. I learned to pause when the story does not fit and to document my differential "
        "more explicitly."
    ),
    "TEACH_OBS": (
        "A consultant observed me teaching an F2 doctor how to assess ankle injuries. They fed back "
        "that my explanations were clear but I should check the learner's baseline knowledge first."
    ),
    "AUDIT": (
        "I completed an audit of documentation of capacity assessments in ED mental health presentations. "
        "I reviewed 30 notes, presented results and proposed a documentation prompt."
    ),
    "RESEARCH": (
        "I recruited patients to an ED research study after completing GCP training. I checked eligibility, "
        "explained the study, obtained consent and reflected on protecting clinical flow."
    ),
    "PDP": (
        "My PDP goal is to improve paediatric safeguarding confidence. I will attend local safeguarding "
        "teaching, discuss one case monthly with my supervisor and review progress before ARCP."
    ),
}


@dataclass(frozen=True)
class SyntheticScenario:
    case_id: str
    form_type: str
    form_name: str
    input_source: str
    source_text: str
    synthetic: bool = True


@dataclass(frozen=True)
class DraftQualityScore:
    overall: float
    required_coverage: float
    grounding: float
    form_fit: float
    reflection: float
    language: float
    issues: tuple[str, ...]


def fileable_form_types() -> list[str]:
    return [
        form_type
        for form_type, schema in FORM_SCHEMAS.items()
        if schema.get("filer_available", True)
    ]


def generate_scenarios(
    *,
    forms: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[SyntheticScenario]:
    selected = [f.strip().upper() for f in forms] if forms else fileable_form_types()
    scenarios: list[SyntheticScenario] = []
    for index, form_type in enumerate(selected, start=1):
        if form_type not in FORM_SCHEMAS:
            raise ValueError(f"Unknown form type: {form_type}")
        form_name = public_form_name(form_type)
        input_source = SOURCE_CYCLE[(index - 1) % len(SOURCE_CYCLE)]
        source_text = _scenario_text(form_type, form_name, input_source)
        scenarios.append(
            SyntheticScenario(
                case_id=f"synthetic-{index:03d}-{form_type.lower()}",
                form_type=form_type,
                form_name=form_name,
                input_source=input_source,
                source_text=source_text,
            )
        )
        if limit and len(scenarios) >= limit:
            break
    return scenarios


def _scenario_text(form_type: str, form_name: str, input_source: str) -> str:
    base = SCENARIO_HINTS.get(form_type)
    if not base:
        base = (
            f"I need to create a {form_name} entry. The activity happened yesterday in "
            "the Emergency Department. I was the trainee involved, the event was relevant "
            "to my training, and I reflected that I need to document the outcome and learning "
            "more clearly next time."
        )
    base = f"I am an ST5 Emergency Medicine trainee on my Emergency Medicine placement. {base}"
    if input_source == "voice":
        return f"Voice transcript: {base}"
    if input_source == "document":
        return f"Document extract from a portfolio note: {base}"
    if input_source == "image":
        return (
            "Context supplied with image: the image is supporting evidence only; "
            f"do not infer findings beyond this text. {base}"
        )
    return base


async def run_scenario(
    scenario: SyntheticScenario,
    *,
    dry_run: bool = False,
    with_ai_review: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    record: dict[str, Any] = {
        "scenario": asdict(scenario),
        "selected_form_type": scenario.form_type,
        "status": "error",
        "recommended_forms": [],
        "draft_fields": {},
        "quality": None,
    }

    try:
        if dry_run:
            fields = _dry_run_fields(scenario.form_type)
            recommendations = []
        else:
            recommendations = await recommend_form_types(
                scenario.source_text,
                input_source=scenario.input_source,
            )
            if scenario.form_type == "CBD":
                draft = await extract_cbd_data(
                    scenario.source_text,
                    input_source=scenario.input_source,
                )
            else:
                draft = await extract_form_data(
                    scenario.source_text,
                    scenario.form_type,
                    input_source=scenario.input_source,
                )
            fields = dict(draft.fields if hasattr(draft, "fields") else draft.model_dump())

        recommended_forms = [
            {
                "form_type": _normalise_recommended_form(getattr(rec, "form_type", "")),
                "rationale": getattr(rec, "rationale", ""),
            }
            for rec in recommendations
        ]
        score = score_draft(
            scenario=scenario,
            fields=fields,
            recommended_forms=[r["form_type"] for r in recommended_forms],
        )

        record.update(
            {
                "status": "completed",
                "recommended_forms": recommended_forms,
                "draft_fields": fields,
                "quality": asdict(score),
                "elapsed_s": round(time.monotonic() - started, 2),
            }
        )
        if with_ai_review and not dry_run:
            record["ai_review"] = await review_draft(
                scenario.form_type,
                fields,
                scenario.source_text,
            )
    except Exception as exc:
        record.update(
            {
                "status": "error",
                "error": str(exc)[:800],
                "elapsed_s": round(time.monotonic() - started, 2),
            }
        )
    return record


def _dry_run_fields(form_type: str) -> dict[str, Any]:
    schema = FORM_SCHEMAS[form_type]
    fields: dict[str, Any] = {}
    for field in schema["fields"]:
        key = field["key"]
        if field["type"] in {"multi_select", "kc_tick"}:
            fields[key] = []
        elif field["type"] == "date":
            fields[key] = "2026-07-05"
        elif field["type"] == "dropdown" and field.get("options"):
            fields[key] = field["options"][0]
        elif field["required"]:
            fields[key] = f"Synthetic {field['label'].lower()} for {public_form_name(form_type)}."
        else:
            fields[key] = ""
    return fields


def score_draft(
    *,
    scenario: SyntheticScenario,
    fields: dict[str, Any],
    recommended_forms: list[str] | None = None,
) -> DraftQualityScore:
    issues: list[str] = []
    required_coverage = _required_coverage(scenario.form_type, fields, issues)
    grounding = _grounding_score(scenario.source_text, fields, issues)
    form_fit = _form_fit_score(scenario.form_type, recommended_forms or [], issues)
    reflection = _reflection_score(fields, issues)
    language = _language_score(fields, issues)
    overall = round(
        (required_coverage * 0.35)
        + (grounding * 0.25)
        + (form_fit * 0.15)
        + (reflection * 0.15)
        + (language * 0.10),
        3,
    )
    return DraftQualityScore(
        overall=overall,
        required_coverage=round(required_coverage, 3),
        grounding=round(grounding, 3),
        form_fit=round(form_fit, 3),
        reflection=round(reflection, 3),
        language=round(language, 3),
        issues=tuple(dict.fromkeys(issues)),
    )


def _required_coverage(form_type: str, fields: dict[str, Any], issues: list[str]) -> float:
    required = [f for f in FORM_SCHEMAS[form_type]["fields"] if f.get("required")]
    if not required:
        return 1.0
    filled = [f for f in required if _has_value(fields.get(f["key"]))]
    missing = [f["label"] for f in required if not _has_value(fields.get(f["key"]))]
    if missing:
        issues.append(f"Missing required fields: {', '.join(missing[:5])}")
    return len(filled) / len(required)


def _grounding_score(source_text: str, fields: dict[str, Any], issues: list[str]) -> float:
    source = source_text.lower()
    draft_text = " ".join(_clinical_claim_text(fields)).lower()
    unsupported: list[str] = []
    for label, terms in HIGH_RISK_CLAIMS.items():
        in_draft = any(_contains_term(draft_text, term) for term in terms)
        in_source = any(_contains_term(source, term) for term in terms)
        if in_draft and not in_source:
            unsupported.append(label)
    if unsupported:
        issues.append(f"Potential unsupported high-risk claims: {', '.join(unsupported)}")
    return max(0.0, 1.0 - (len(unsupported) * 0.25))


def _form_fit_score(form_type: str, recommended_forms: list[str], issues: list[str]) -> float:
    if not recommended_forms:
        return 0.8
    if form_type in recommended_forms:
        return 1.0
    issues.append(f"Target form {form_type} not in recommendations: {', '.join(recommended_forms[:3])}")
    return 0.35


def _reflection_score(fields: dict[str, Any], issues: list[str]) -> float:
    reflection_keys = [
        key
        for key in fields
        if _is_reflection_key(key)
    ]
    if not reflection_keys:
        return 1.0
    reflection_values = [
        str(value)
        for key, value in fields.items()
        if _is_reflection_key(key)
        and isinstance(value, str)
        and value.strip()
    ]
    if not reflection_values:
        issues.append("No reflection/learning field populated")
        return 0.25
    text = " ".join(reflection_values)
    words = re.findall(r"[A-Za-z']+", text)
    first_person = bool(re.search(r"\b(I|my|me)\b", text))
    if len(words) < 18:
        issues.append("Reflection appears too thin")
    if not first_person:
        issues.append("Reflection is not clearly first-person")
    score = 1.0
    if len(words) < 18:
        score -= 0.35
    if not first_person:
        score -= 0.25
    return max(0.0, score)


def _language_score(fields: dict[str, Any], issues: list[str]) -> float:
    text = " ".join(_flatten_text(fields)).lower()
    found = [phrase for phrase in AI_TELL_PHRASES if phrase in text]
    long_blocks = [
        value
        for value in _flatten_text(fields)
        if isinstance(value, str) and len(value.split()) > 110 and "\n" not in value
    ]
    score = 1.0
    if found:
        issues.append(f"AI-tell phrases present: {', '.join(found[:5])}")
        score -= min(0.5, 0.1 * len(found))
    if long_blocks:
        issues.append("Long unbroken narrative paragraph present")
        score -= 0.25
    return max(0.0, score)


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return bool(value)


def _flatten_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for entry in value for item in _flatten_text(entry)]
    if isinstance(value, dict):
        return [item for entry in value.values() for item in _flatten_text(entry)]
    return []


def _clinical_claim_text(fields: dict[str, Any]) -> list[str]:
    """Return free-text fields where unsupported clinical claims matter.

    Curriculum/KC labels contain official wording such as "peri-arrest" even
    when the draft did not invent an arrest. Excluding taxonomy fields keeps
    the hallucination detector focused on narrative content.
    """
    values: list[str] = []
    for key, value in fields.items():
        lower = str(key).lower()
        if "curriculum" in lower or "capabilities" in lower or lower in {"key_capabilities"}:
            continue
        values.extend(_flatten_text(value))
    return values


def _normalise_recommended_form(form_type: str) -> str:
    raw = str(form_type or "").strip()
    if raw in FORM_SCHEMAS:
        return raw
    upper = raw.upper().replace("-", "_").replace(" ", "_")
    if upper in FORM_SCHEMAS:
        return upper
    public_lookup = {public_form_name(code).lower(): code for code in FORM_SCHEMAS}
    return public_lookup.get(raw.lower(), raw)


def _contains_term(text: str, term: str) -> bool:
    if len(term) <= 4 and term.replace(" ", "").isalnum():
        return bool(re.search(rf"\b{re.escape(term)}\b", text))
    return term in text


def _is_reflection_key(key: str) -> bool:
    lower = str(key).lower()
    exact = {
        "reflection",
        "learned",
        "replay_differently",
        "different_outcome",
        "focussing_on",
        "focusing_on",
        "why",
        "next_pdp",
        "pdp_summary",
    }
    if lower in exact:
        return True
    return lower.endswith("_reflection") or lower.startswith("reflection_")


async def run_evaluation(
    scenarios: list[SyntheticScenario],
    *,
    dry_run: bool = False,
    with_ai_review: bool = False,
    concurrency: int = 1,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def guarded(scenario: SyntheticScenario) -> dict[str, Any]:
        async with semaphore:
            return await run_scenario(
                scenario,
                dry_run=dry_run,
                with_ai_review=with_ai_review,
            )

    results = await asyncio.gather(*(guarded(s) for s in scenarios))
    completed = [r for r in results if r["status"] == "completed"]
    scores = [r["quality"]["overall"] for r in completed if r.get("quality")]
    weak = [r for r in completed if r.get("quality") and r["quality"]["overall"] < 0.75]
    errors = [r for r in results if r["status"] != "completed"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "synthetic_only": True,
        "storage": "local_eval_artifact",
        "scenarios_requested": len(scenarios),
        "completed": len(completed),
        "errors": len(errors),
        "average_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        "weak_count": len(weak),
        "results": results,
    }


def configure_eval_runtime() -> None:
    """Align the eval CLI with the live bot's model auth environment.

    In live runtime, run_local.sh materialises GCP_VERTEX_SA_JSON into a
    temporary GOOGLE_APPLICATION_CREDENTIALS file before Vertex calls. Codex
    shells can inherit the JSON without that file, so a direct eval would
    otherwise fail every case with an ADC error despite valid credentials.
    """
    global _TEMP_VERTEX_CREDENTIALS
    use_vertex = (
        os.environ.get("PG_USE_VERTEX", "").strip().lower() in {"1", "true", "yes", "on"}
        and bool(os.environ.get("GCP_PROJECT_ID"))
    )
    if not use_vertex:
        return

    existing = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if existing:
        try:
            with open(existing, encoding="utf-8") as handle:
                json.load(handle)
            return
        except Exception:
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

    sa_json = os.environ.get("GCP_VERTEX_SA_JSON")
    if not sa_json:
        raise RuntimeError(
            "PG_USE_VERTEX is enabled but GOOGLE_APPLICATION_CREDENTIALS is not set "
            "and GCP_VERTEX_SA_JSON is unavailable. Run via backend/run_local.sh "
            "environment or disable Vertex for this synthetic eval."
        )
    try:
        json.loads(sa_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "PG_USE_VERTEX is enabled but GCP_VERTEX_SA_JSON is not valid JSON in "
            "this shell. Re-run with the backend/run_local.sh secret environment "
            "or disable Vertex for this synthetic eval."
        ) from exc

    fd, path = tempfile.mkstemp(prefix="pg-eval-vertex-sa-")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(sa_json)
    os.chmod(path, 0o600)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path
    _TEMP_VERTEX_CREDENTIALS = Path(path)

    def cleanup() -> None:
        if _TEMP_VERTEX_CREDENTIALS and _TEMP_VERTEX_CREDENTIALS.exists():
            try:
                _TEMP_VERTEX_CREDENTIALS.unlink()
            except OSError:
                pass

    atexit.register(cleanup)


def write_outputs(payload: dict[str, Any]) -> tuple[Path, Path]:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = EVAL_DIR / f"draft-quality-{stamp}.json"
    md_path = EVAL_DIR / f"draft-quality-{stamp}.md"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(payload, json_path.name), encoding="utf-8")
    latest_json = EVAL_DIR / "latest.json"
    latest_md = EVAL_DIR / "latest.md"
    latest_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    return json_path, md_path


def render_markdown(payload: dict[str, Any], json_name: str) -> str:
    lines = [
        "# Portfolio Guru Draft Quality Eval",
        "",
        f"- Generated: {payload['generated_at']}",
        f"- Synthetic only: {payload['synthetic_only']}",
        f"- Storage: {payload['storage']}",
        f"- Completed: {payload['completed']}/{payload['scenarios_requested']}",
        f"- Errors: {payload['errors']}",
        f"- Average score: {payload['average_score']:.3f}",
        f"- Weak drafts: {payload['weak_count']}",
        f"- Raw JSON: `{json_name}`",
        "",
        "## Weakest Cases",
        "",
    ]
    completed = [r for r in payload["results"] if r["status"] == "completed"]
    weakest = sorted(completed, key=lambda r: r["quality"]["overall"])[:10]
    if not weakest:
        lines.append("No completed cases.")
    for record in weakest:
        scenario = record["scenario"]
        quality = record["quality"]
        lines.extend(
            [
                f"### {scenario['form_type']} - {scenario['form_name']}",
                "",
                f"- Case: `{scenario['case_id']}`",
                f"- Source: {scenario['input_source']}",
                f"- Score: {quality['overall']:.3f}",
                f"- Issues: {'; '.join(quality['issues']) if quality['issues'] else 'None'}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run synthetic Portfolio Guru draft-quality evals")
    parser.add_argument("--limit", type=int, default=20, help="Number of synthetic scenarios to run")
    parser.add_argument("--forms", default="", help="Comma-separated form codes. Default: fileable forms in schema order")
    parser.add_argument("--all", action="store_true", help="Run every fileable form scenario")
    parser.add_argument("--dry-run", action="store_true", help="Do not call LLMs; score synthetic placeholder fields")
    parser.add_argument("--with-ai-review", action="store_true", help="Also call the production draft reviewer per case")
    parser.add_argument("--concurrency", type=int, default=1, help="Concurrent LLM calls; keep low to avoid rate limits")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if not args.dry_run:
        configure_eval_runtime()
    forms = [item.strip() for item in args.forms.split(",") if item.strip()] or None
    scenarios = generate_scenarios(
        forms=forms,
        limit=None if args.all else args.limit,
    )
    payload = await run_evaluation(
        scenarios,
        dry_run=args.dry_run,
        with_ai_review=args.with_ai_review,
        concurrency=args.concurrency,
    )
    json_path, md_path = write_outputs(payload)
    print(f"RESULT_JSON={json_path}")
    print(f"RESULT_REPORT={md_path}")
    print(f"COMPLETED={payload['completed']}/{payload['scenarios_requested']}")
    print(f"AVERAGE_SCORE={payload['average_score']:.3f}")
    print(f"WEAK_COUNT={payload['weak_count']}")
    if payload["errors"]:
        print(f"ERRORS={payload['errors']}")


if __name__ == "__main__":
    asyncio.run(main())
