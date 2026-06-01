#!/usr/bin/env python3
"""
Model/Pathway Evaluation for Portfolio Guru.

Compares extraction quality across providers by calling each one DIRECTLY
(not through _generate's fallback chain). Ensures true isolation.

Usage:
    cd ~/projects/portfolio-guru
    python3 backend/eval_model_pathways.py [--all] [--cases 1,3,5] [--providers deepseek-v4-flash]
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time as _time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("eval")

# ── API key bootstrapping ──────────────────────────────────────────────────

BWS_PATH = "/Users/moeedahmed/.cargo/bin/bws"
BWS_TOKEN_PATH = os.path.expanduser("~/.openclaw/.bws-token")


def _bws_secret(secret_id: str) -> str:
    bws_token = os.environ.get("BWS_ACCESS_TOKEN")
    if not bws_token and os.path.exists(BWS_TOKEN_PATH):
        with open(BWS_TOKEN_PATH) as f:
            bws_token = f.read().strip()
    if not bws_token:
        raise ValueError("BWS_ACCESS_TOKEN not available")
    result = subprocess.run(
        [BWS_PATH, "secret", "get", secret_id, "--output", "json"],
        env={**os.environ, "BWS_ACCESS_TOKEN": bws_token},
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)["value"]


def _ensure_env():
    secrets = {
        "GOOGLE_API_KEY": "af6579a0-2cbe-4cef-94b3-b405017b48fe",
        "OPENAI_API_KEY": "6c82eeff-f52d-49b6-8ccf-b3f4007d10dd",
    }
    for key, secret_id in secrets.items():
        if not os.environ.get(key):
            try:
                os.environ[key] = _bws_secret(secret_id)
                logger.info(f"Loaded {key} from BWS")
            except Exception as e:
                logger.warning(f"Could not load {key}: {e}")


# ── Test cases (anonymised real clinical scenarios) ────────────────────────

TEST_CASES = [
    {
        "id": 1,
        "title": "STEMI in Resus",
        "form_types": ["CBD", "DOPS"],
        "case": """45-year-old male presented with central chest pain radiating to left arm, onset 2 hours ago, associated with shortness of breath and diaphoresis. ECG showed anterior ST elevation in V2-V4. I assessed in Resus, started oxygen to maintain sats >94%, gave 300mg aspirin and GTN spray. Cardiology were called for primary PCI. I discussed the diagnosis and management with the patient and his wife. This was a good case for managing an acute STEMI with the team.""",
    },
    {
        "id": 2,
        "title": "Reflection on Difficult Airways",
        "form_types": ["REFLECT_LOG", "CBD"],
        "case": """I attended a difficult airway call overnight in Resus. A 67-year-old male with known obesity and obstructive sleep apnoea presented in type 2 respiratory failure with reduced GCS. Anaesthetics struggled with intubation - they needed a bougie on the second attempt and eventually used a McGrath video laryngoscope. On reflection, I think I should have anticipated this and called for senior help earlier. I was focused on the desaturation and didn't consider the difficult airway predictors. I'll now routinely assess Mallampati and neck mobility in any patient I'm bagging, even before the airway team arrives.""",
    },
    {
        "id": 3,
        "title": "Chest Drain Procedure (DOPS)",
        "form_types": ["DOPS", "PROC_LOG"],
        "case": """I performed an ultrasound-guided chest drain insertion on a 55-year-old male with a large right-sided pleural effusion secondary to pneumonia. I used the Seldinger technique with a 12Fr chest drain, under direct supervision of the respiratory registrar. The patient was given 1% lidocaine local anaesthetic (20ml). I confirmed position with ultrasound before and after. Drain inserted successfully with 800ml of straw-coloured fluid drained initially. No complications. I gained consent and explained the procedure to the patient beforehand.""",
    },
    {
        "id": 4,
        "title": "Shift Leadership in Majors",
        "form_types": ["LAT", "ESLE", "ACAT"],
        "case": """I was the EPIC doctor for a busy evening shift in Majors. We had a full department with 12 patients waiting. I coordinated patient flow, prioritised the sickest patients, and allocated cases to the SHO and F2. I did a safety brief at the start of the shift, sent a capacity message to the site coordinator, and managed a cardiac arrest in Resus while still overseeing majors flow. I handed over to the night team at 9pm. The consultant gave feedback that I managed the flow well and communicated clearly with the nursing team.""",
    },
    {
        "id": 5,
        "title": "Teaching on ECG Interpretation",
        "form_types": ["TEACH", "STAT"],
        "case": """I delivered a 30-minute bedside teaching session to two F1 doctors and a medical student on interpreting ECGs in chest pain. We went through 5 ECGs: normal, anterior STEMI, inferior STEMI, pericarditis, and PE patterns. I used the department's ECG machine and printed strips. I gave them a systematic approach: rate, rhythm, axis, intervals, ST changes, then clinical context. I didn't have a formal assessor watching.""",
    },
    {
        "id": 6,
        "title": "Procedural Sedation for Fracture Reduction",
        "form_types": ["PROC_LOG", "CBD"],
        "case": """A 32-year-old female attended with a closed Colles fracture after a fall onto outstretched hand. Orthopaedics wanted manipulation under procedural sedation in ED. I administered 2mg midazolam and 50mcg fentanyl IV under supervision of the EM consultant. I monitored sats, ECG and BP throughout. The reduction was successful, placed in a backslab. No complications and the patient tolerated it well.""",
    },
    {
        "id": 7,
        "title": "QI Project on Door-to-Needle Times",
        "form_types": ["QIAT", "CBD"],
        "case": """I completed a QI project on door-to-needle times for STEMI. I audited 40 cases over 3 months. Baseline mean time was 48 minutes (RCEM target 30). I introduced a pre-alert checklist for triage nurse and a streamlined ECG pathway. Re-audit of 38 cases showed mean time reduced to 31 minutes. I presented results at the departmental governance meeting and the checklist has been adopted permanently.""",
    },
    {
        "id": 8,
        "title": "Reflection on Missed PE Diagnosis",
        "form_types": ["REFLECT_LOG", "CBD"],
        "case": """A 72-year-old female with history of DVT was admitted with shortness of breath. I initially treated her for an exacerbation of COPD based on smoking history. After 24 hours she deteriorated, and the consultant reviewed and discovered I hadn't assessed PE risk with Wells or PERC. CT pulmonary angiogram showed bilateral PE. She was started on anticoagulation and improved. On reflection, I had anchoring bias - assumed COPD because of smoking history and didn't properly work through the differential. I'll now use PERC and Wells in all breathless patients regardless of known diagnoses.""",
    },
]

# ── Prompts (from extractor.py, identical for fair comparison) ─────────────

RCEM_KC_MAP = """RCEM Higher EM Curriculum (2025 Update) — Exact Kaizen Checkbox Labels:

SLO1: Care for acutely physiologically stable adult patients presenting to acute care across the full range of complexity (2025 Update)
  KC1: to be expert in assessing and managing all adult patients attending the ED. These capabilities will apply to patients attending with both physical and psychological ill health (2025 Update)

SLO2: Support the ED team by answering clinical questions and making safe decisions (2025 Update)
  KC1: able to support the pre-hospital, medical, nursing and administrative team in answering clinical questions and in making safe decisions for patients with appropriate levels of risk in the ED (2025 Update)
  KC2: aware of when it is appropriate to review patients remotely or directly and able to teach these principles to others (2025 Update)

SLO3: Resuscitate and stabilise patients in the ED knowing when it is appropriate to stop (2025 Update)
  KC1: provide airway management & ventilatory support to critically ill patients (2025 Update)
  KC2: be expert in fluid management and circulatory support in critically ill patients (2025 Update)
  KC3: manage all the life-threatening conditions including peri-arrest & arrest situations in the ED (2025 Update)
  KC4: be expert in caring for ED patients and their relatives and loved ones at the end of the patient's life (2025 Update)
  KC5: effectively lead and support resuscitation teams (2025 Update)

SLO4: Care for acutely injured patients across the full range of complexity (2025 Update)
  KC1: be expert in assessment, investigation and clinical management of patients attending with all injuries, regardless of complexity (2025 Update)
  KC2: provide expert leadership of the Major Trauma Team (2025 Update)

SLO5: Care for children of all ages, at all stages of development and with complex needs (2025 Update)
  KC1: be expert in assessing and managing all children and young adult patients attending the ED (2025 Update)
  KC2: be able to provide airway management & ventilatory support to critically ill paediatric patients (2025 Update)
  KC3: be able to lead and support a multidisciplinary paediatric resuscitation including trauma (2025 Update)
  KC4: be expert in fluid management and circulatory support in critically ill paediatric patients (2025 Update)
  KC5: be able to manage all the life-threatening paediatric conditions including peri-arrest & arrest situations in the ED (2025 Update)
  KC6: be able to assess and formulate a management plan for children and young adults who present with complex medical and social needs (2025 Update)

SLO6: Deliver key procedural skills needed in EM (2025 Update)
  KC1: the clinical knowledge to identify when key EM practical/emergency skills are indicated (2025 Update)
  KC2: the knowledge and psychomotor skills to perform EM procedural skills safely and in a timely fashion (2025 Update)
  KC3: be able to supervise and guide colleagues in delivering procedural skills (2025 Update)

SLO7: Deal with complex or challenging situations in the workplace (2025 Update)
  KC1: have expert communication skills to negotiate, manage complicated or evolving interactions (2025 Update)
  KC2: behave professionally in dealings with colleagues and team members within the ED (2025 Update)
  KC3: work professionally and effectively with those outside the ED (2025 Update)

SLO8: Lead the ED shift (2025 Update)
  KC1: will provide support to ED staff at all levels and disciplines on the ED shift (2025 Update)
  KC2: will be able to liaise with the rest of the acute/urgent care team and wider hospital as shift leader (2025 Update)
  KC3: will maintain situational awareness throughout the shift to ensure safety is optimised (2025 Update)
  KC4: will anticipate challenges, generate options, make decisions and communicate these effectively to the team as lead clinician (2025 Update)

SLO9: Support, supervise & educate others working in the ED (2025 Update)
  KC1: be able to undertake training and supervision of members of the ED team in the clinical environment (2025 Update)
  KC2: be able to prepare and deliver teaching sessions outside of the clinical environment, including simulation, small group work, and didactic presentations (2025 Update)
  KC3: be able to provide effective constructive feedback to colleagues, including debrief (2025 Update)
  KC4: understand the principles necessary to mentor and appraise junior doctors (2025 Update)

SLO10: Participate in research and manage data appropriately (2025 Update)
  KC1: be able to appraise, synthesise, communicate and use research evidence to develop EM care (2025 Update)
  KC2: be able to actively participate in research (2025 Update)

SLO11: Participate in & promote activity to improve quality & safety of patient care (2025 Update)
  KC1: be able to provide clinical leadership on effective Quality Improvement work (2025 Update)
  KC2: be able to support and develop a culture of departmental safety, and good clinical governance (2025 Update)

SLO12: Lead & Manage (2025 Update)
  KC1: be able to demonstrate their involvement in a range of management activities and show an understanding of the relevant medicolegal directives (2025 Update)
  KC2: be able to investigate a patient safety incident, participate and contribute effectively to department clinical governance activities and risk reduction processes (2025 Update)
  KC3: be able to manage the staff rota being aware of relevant employment law and recruitment activities (2025 Update)
  KC4: be able to effectively represent the ED at inter-specialty meetings (2025 Update)
  KC5: demonstrate an understanding of how effective Emergency Medicine Leadership positively impacts on standards of patient care and patient safety (2025 Update)
  KC6: demonstrate a positive impact on the culture of the Emergency Department through attitudes and behaviours that impact positively on colleagues, patients and their relatives (2025 Update)
"""


def _form_recommendation_prompt(case_text: str) -> str:
    return f"""You are an expert RCEM portfolio advisor. Analyse the clinical or educational event described and recommend the 1-3 most appropriate RCEM Kaizen WPBA form types.

Available forms: CBD, DOPS, Mini-CEX, ACAT, LAT, ACAF, STAT, MSF, QIAT, JCF, TEACH, PROC_LOG, SDL, US_CASE, ESLE, COMPLAINT, SERIOUS_INC, EDU_ACT, FORMAL_COURSE, REFLECT_LOG, TEACH_OBS.

Case: {case_text}

Return ONLY a JSON array: [{{"form_type": "CBD", "rationale": "one-line reason"}}]"""


def _cbd_extraction_prompt(case_text: str) -> str:
    today = date.today()
    return f"""You are a medical portfolio assistant. Extract structured data from a clinical case description for a Case-Based Discussion (CBD) WPBA entry. Today's date: {today.isoformat()}.

Return ONLY a JSON object with:
{{
  "form_type": "CBD",
  "date_of_encounter": "YYYY-MM-DD",
  "patient_age": "e.g. '45-year-old'",
  "patient_presentation": "presenting complaint",
  "clinical_setting": "ED setting",
  "stage_of_training": null,
  "trainee_role": "what the trainee did",
  "clinical_reasoning": "what they did and why",
  "reflection": "what was learned",
  "level_of_supervision": "Direct/Indirect/Distant",
  "supervisor_name": null,
  "curriculum_links": ["SLO1", "SLO3"],
  "key_capabilities": ["SLO1 KC1: full text..."]
}}

KC SELECTION from this curriculum:
{RCEM_KC_MAP}

Rules:
- Select KCs DIRECTLY demonstrated by the case. Do NOT select KC1 just because it "could apply" — only if something specific to KC1 is shown.
- Target 3-6 KCs per case. Quality over quantity.
- Use FULL KC text including "(2025 Update)" suffix.
- curriculum_links = just SLO codes from KCs selected.
- Write reflection in first person, specific, genuine. No AI-tells (em dashes, "delve", "crucial", "importantly", "comprehensive").
- British English spelling.
- Return ONLY valid JSON, no code fences."""


def _review_prompt(form_type: str, fields_json: str, case_text: str) -> str:
    return f"""You are a senior UK EM consultant and WPBA assessor reviewing a {form_type} draft.

ORIGINAL CASE: {case_text}

DRAFT FIELDS: {fields_json}

Score each criterion 1-5 and return JSON:

{{
  "overall_score": <float 1-5>,
  "scores": {{
    "clinical_accuracy": {{"score": 1-5, "feedback": "..."}},
    "voice_preservation": {{"score": 1-5, "feedback": "..."}},
    "kc_slo_mapping": {{"score": 1-5, "feedback": "..."}},
    "completeness": {{"score": 1-5, "feedback": "..."}},
    "reflection_depth": {{"score": 1-5, "feedback": "..."}}
  }},
  "top_issue": "single biggest problem or blank if none",
  "verdict": "ready|improve|weak"
}}

Verdict: ready >= 3.5, improve 2.5-3.4, weak < 2.5."""


# ── Direct provider calls (isolated, no production code modification) ─────

async def _call_gemini(prompt: str, model: str) -> str:
    """Direct call to Google Gemini."""
    loop = asyncio.get_event_loop()
    from google import genai
    api_key = os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)
    t0 = _time.monotonic()
    response = await loop.run_in_executor(
        None,
        lambda: client.models.generate_content(model=model, contents=prompt),
    )
    elapsed = _time.monotonic() - t0
    return response.text, elapsed


async def _call_deepseek(prompt: str, model: str = "deepseek-v4-flash") -> tuple[str, float]:
    """Direct call to DeepSeek API."""
    from openai import OpenAI
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    loop = asyncio.get_event_loop()
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
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
    elapsed = _time.monotonic() - t0
    return response.choices[0].message.content, elapsed


# ── Evaluation per case per provider ──────────────────────────────────────

PROVIDERS = [
    {"name": "gemini-fast",   "caller": "gemini",
     "model": "gemini-3-flash-preview",        "env_key": "GOOGLE_API_KEY",       "cost_note": "Free tier / Google One AI Premium"},
    {"name": "gemini-2.5-flash", "caller": "gemini",
     "model": "gemini-2.5-flash",               "env_key": "GOOGLE_API_KEY",       "cost_note": "Free tier / Google One AI Premium"},
    {"name": "gemini-3-5-flash", "caller": "gemini",
     "model": "gemini-3.5-flash",               "env_key": "GOOGLE_API_KEY",       "cost_note": "Google API key (pay-as-you-go)"},
    {"name": "gemini-pro",    "caller": "gemini",
     "model": "gemini-3.1-pro-preview",          "env_key": "GOOGLE_API_KEY",       "cost_note": "Google One AI Premium (subscription)"},
    {"name": "deepseek-v4-flash", "caller": "deepseek",
     "model": "deepseek-v4-flash",               "env_key": "DEEPSEEK_API_KEY",     "cost_note": "DeepSeek console balance"},
]


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    return json.loads(text)


async def eval_one(provider: dict, case: dict) -> dict:
    """Run a single case through a single provider - all steps."""
    result = {
        "provider": provider["name"],
        "model": provider["model"],
        "case_id": case["id"],
        "title": case["title"],
        "status": "error",
        "steps": [],
    }

    try:
        # ── Step 1: Form type recommendation ──
        fr_prompt = _form_recommendation_prompt(case["case"])
        t0 = _time.monotonic()
        if provider["caller"] == "gemini":
            fr_text, fr_time = await _call_gemini(fr_prompt, provider["model"])
        else:
            fr_text, fr_time = await _call_deepseek(fr_prompt, provider["model"])
        recoms = _parse_json(fr_text) if isinstance(fr_text, str) else []
        if isinstance(recoms, dict):
            recoms = [recoms]
        rec_types = [r.get("form_type", "?") for r in (recoms if isinstance(recoms, list) else [])]
        matched = any(rt in case["form_types"] for rt in rec_types)
        result["steps"].append({
            "step": "form_recommendation",
            "recommended": rec_types,
            "expected": case["form_types"],
            "matched": matched,
            "time_s": round(fr_time, 2),
        })

        # ── Step 2: CBD extraction ──
        ext_prompt = _cbd_extraction_prompt(case["case"])
        if provider["caller"] == "gemini":
            ext_text, ext_time = await _call_gemini(ext_prompt, provider["model"])
        else:
            ext_text, ext_time = await _call_deepseek(ext_prompt, provider["model"])
        ext_data = _parse_json(ext_text)
        result["steps"].append({
            "step": "extraction",
            "output": {
                "clinical_reasoning_cut": (ext_data.get("clinical_reasoning", "") or "")[:300],
                "reflection_cut": (ext_data.get("reflection", "") or "")[:300],
                "kcs": ext_data.get("key_capabilities", []),
                "slos": ext_data.get("curriculum_links", []),
            },
            "time_s": round(ext_time, 2),
        })

        # ── Step 3: Draft review ──
        fields_slim = {
            "clinical_reasoning": ext_data.get("clinical_reasoning", ""),
            "reflection": ext_data.get("reflection", ""),
            "patient_presentation": ext_data.get("patient_presentation", ""),
            "key_capabilities": ext_data.get("key_capabilities", []),
            "curriculum_links": ext_data.get("curriculum_links", []),
        }
        rev_prompt = _review_prompt("CBD", json.dumps(fields_slim, indent=2, default=str), case["case"])
        if provider["caller"] == "gemini":
            rev_text, rev_time = await _call_gemini(rev_prompt, provider["model"])
        else:
            rev_text, rev_time = await _call_deepseek(rev_prompt, provider["model"])
        review = _parse_json(rev_text)
        result["steps"].append({
            "step": "draft_review",
            "scores": review.get("scores", {}),
            "overall_score": review.get("overall_score"),
            "verdict": review.get("verdict", "unknown"),
            "top_issue": review.get("top_issue", ""),
            "time_s": round(rev_time, 2),
        })

        # ── Step 4: KC analysis ──
        result["steps"].append({
            "step": "kc_analysis",
            "kc_count": len(ext_data.get("key_capabilities", []) or []),
            "slo_count": len(ext_data.get("curriculum_links", []) or []),
            "reflection_chars": len(ext_data.get("reflection", "") or ""),
            "reasoning_chars": len(ext_data.get("clinical_reasoning", "") or ""),
        })

        result["status"] = "completed"

        logger.info(f"  ✓ {provider['name']} — case {case['id']} — "
                     f"score={review.get('overall_score', '?'):.1f} "
                     f"verdict={review.get('verdict', '?')} "
                     f"rec={rec_types}")

    except Exception as e:
        result["error"] = str(e)[:400]
        logger.warning(f"  ✗ {provider['name']} — case {case['id']} failed: {str(e)[:80]}")

    return result


# ── Report generation ─────────────────────────────────────────────────────

def _avg(vals: list) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def build_report(results: list[dict]) -> str:
    completed = [r for r in results if r["status"] == "completed"]
    skipped = [r for r in results if r["status"] == "skipped"]
    lines = []

    def emit(line=""):
        lines.append(line)

    emit("=" * 72)
    emit("  PORTFOLIO GURU — MODEL PATHWAY EVALUATION REPORT")
    emit(f"  {date.today().isoformat()}  |  {len(completed)} runs, {len(skipped)} skipped")
    emit("=" * 72)

    # Group by provider
    prov_names = sorted(set(r["provider"] for r in completed))
    for pn in prov_names:
        prov_runs = [r for r in completed if r["provider"] == pn]
        model = prov_runs[0]["model"]
        emit()
        emit(f"  ── {pn.upper()} ({model}) ──")

        all_scores = []
        verdicts = {"ready": 0, "improve": 0, "weak": 0}
        form_matches = 0
        times_rec, times_ext, times_rev = [], [], []
        kc_counts = []

        for r in prov_runs:
            ss = {s["step"]: s for s in r["steps"]}
            if "draft_review" in ss:
                os_ = ss["draft_review"].get("overall_score")
                if os_ is not None:
                    all_scores.append(float(os_))
                verdicts[ss["draft_review"].get("verdict", "improve")] += 1
            if "form_recommendation" in ss:
                if ss["form_recommendation"].get("matched"):
                    form_matches += 1
                times_rec.append(ss["form_recommendation"].get("time_s", 0))
            if "extraction" in ss:
                times_ext.append(ss["extraction"].get("time_s", 0))
            if "draft_review" in ss:
                times_rev.append(ss["draft_review"].get("time_s", 0))
            if "kc_analysis" in ss:
                kc_counts.append(ss["kc_analysis"].get("kc_count", 0))

        n = len(prov_runs)
        emit(f"    Overall score:      {_avg(all_scores):.2f}/5.0")
        emit(f"    Verdicts:            {' | '.join(f'{v}×{k}' for k,v in sorted(verdicts.items(), key=lambda x:-x[1]))}")
        emit(f"    Form type match:     {form_matches}/{n} ({form_matches/n*100:.0f}%)")
        emit(f"    Avg KCs:             {_avg(kc_counts):.1f}")
        emit(f"    Avg speed:           {_avg(times_rec):.1f}s recommend "
             f"| {_avg(times_ext):.1f}s extract "
             f"| {_avg(times_rev):.1f}s review")
        emit(f"    Total per case:      {_avg(times_rec + times_ext + times_rev):.1f}s")

        # Per-case detail
        emit()
        emit(f"    Case-by-case:")
        for r in prov_runs:
            ss = {s["step"]: s for s in r["steps"]}
            rev = ss.get("draft_review", {})
            fr = ss.get("form_recommendation", {})
            kc = ss.get("kc_analysis", {})
            status = "✓" if r["status"] == "completed" else "✗"
            os_val = rev.get('overall_score')
            os_str = f'{os_val:.1f}' if os_val is not None else '-'
            emit(f"      {status} #{r['case_id']} {r['title']:32s}"
                 f"  {os_str:>4s}/5"
                 f"  {str(rev.get('verdict','-')):>7s}"
                 f"  KC:{str(kc.get('kc_count','-')):>2s}"
                 f"  {fr.get('recommended','-')}")

    # Cost table
    emit()
    emit("  ── COST / BILLING ROUTE ──")
    emit()
    for pn in prov_names:
        p = next(p for p in PROVIDERS if p["name"] == pn)
        emit(f"    • {pn} ({p['model']})")
        emit(f"      Auth: {p['cost_note']}")
    if not os.environ.get("GOOGLE_API_KEY", "").endswith("free"):
        emit()
        emit("    Note: GOOGLE_API_KEY appears to be a pay-as-you-go key.")
        emit("    Using Google One AI Premium (subscription) would cover Gemini Pro at no extra per-call cost.")

    # Recommendation
    emit()
    emit("  ── RECOMMENDATION ──")
    emit()

    # Determine best provider by score
    if prov_names:
        best = max(
            prov_names,
            key=lambda pn: _avg(
                [float(r["steps"][-2]["overall_score"])
                 for r in completed if r["provider"] == pn
                 and any(s["step"] == "draft_review" for s in r["steps"])
                 and (sr := next((s for s in r["steps"] if s["step"] == "draft_review"), None))
                 and sr.get("overall_score") is not None]
                or [0]
            ) if any(r["provider"] == pn and
                     any(s["step"] == "draft_review" and s.get("overall_score") is not None
                         for s in r["steps"])
                     for r in completed)
            else 0,
        )

    emit("  Default extraction policy (proposed):")
    emit()
    emit("    Primary: Gemini Flash (fast, cheapest, good enough for most cases)")
    emit("    Fallback: DeepSeek V4 (when Gemini quota exhausted)")
    emit("    Premium escalation: Gemini Pro (ambiguous cases, complex multi-form)")
    emit()
    emit("  Kaizen filing pathway:")
    emit("    Primary: Playwright deterministic (fast, cheap, DOM-mapped)")
    emit("    AI escalation: Browser-use harness (unreliable fields, new forms)")
    emit()
    emit("  This eval does not commit DeepSeek as default. It provides")
    emit("  measured comparison data for an informed decision. Run with")
    emit("  --all to test all 8 cases across all providers for fuller data.")
    emit()
    emit(f"  Full results: {EVAL_RESULTS_FILE}")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────

EVAL_DIR = Path(__file__).parent.parent / "memory" / "eval"
EVAL_RESULTS_FILE = EVAL_DIR / f"model-pathways-{date.today().isoformat()}.json"


def save_results(results: list[dict]):
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "total_cases": len(TEST_CASES),
        "providers_tested": sorted(set(r["provider"] for r in results)),
        "results": results,
    }
    with open(EVAL_RESULTS_FILE, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    logger.info(f"Results saved to {EVAL_RESULTS_FILE}")


async def main():
    _ensure_env()

    import argparse
    parser = argparse.ArgumentParser(description="Portfolio Guru model pathway evaluation")
    parser.add_argument("--all", action="store_true", help="Run all 8 cases")
    parser.add_argument("--cases", type=str, default="1,3,5,8",
                        help="Comma-separated case IDs (default: 1,3,5,8 — diverse sampling)")
    parser.add_argument("--providers", type=str, default="deepseek-v4-flash",
                        help="Comma-separated provider names (default: deepseek-v4-flash)")
    args = parser.parse_args()

    case_ids = [int(x.strip()) for x in args.cases.split(",") if x.strip()]
    if args.all:
        case_ids = [c["id"] for c in TEST_CASES]

    sel_names = [p.strip() for p in args.providers.split(",") if p.strip()]
    providers = [p for p in PROVIDERS if p["name"] in sel_names]

    # Check API keys
    for p in providers:
        key = os.environ.get(p["env_key"])
        if not key:
            logger.warning(f"  {p['name']}: {p['env_key']} NOT available — will skip")
        else:
            logger.info(f"  {p['name']}: {p['env_key']} available (len={len(key)})")

    providers = [p for p in providers if os.environ.get(p["env_key"])]

    if not providers:
        logger.error("No providers available — aborting")
        sys.exit(1)

    logger.info(f"\nCases: {case_ids} ({len(case_ids)} cases)")
    logger.info(f"Providers: {[p['name'] for p in providers]} ({len(providers)} providers)")
    logger.info(f"Total runs: {len(case_ids) * len(providers)}")

    total_start = _time.monotonic()

    # Run each case through each provider sequentially (to avoid rate limiting)
    results = []
    for case in TEST_CASES:
        if case["id"] not in case_ids:
            continue
        logger.info(f"\n{'='*60}")
        logger.info(f"Case {case['id']}: {case['title']} (expects: {', '.join(case['form_types'])})")
        logger.info(f"{'='*60}")
        for provider in providers:
            r = await eval_one(provider, case)
            results.append(r)

    total_elapsed = _time.monotonic() - total_start
    logger.info(f"\nTotal evaluation time: {total_elapsed:.0f}s")

    report = build_report(results)
    print(report)
    save_results(results)

    # Also save human-readable report
    report_path = EVAL_DIR / f"model-pathways-{date.today().isoformat()}.txt"
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)
    logger.info(f"Report saved to {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
