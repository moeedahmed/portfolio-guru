"""Catalogue-backed curriculum validation shared by drafts and filing."""

import logging
import re

logger = logging.getLogger(__name__)

# RCEM Higher EM Curriculum (2025 Update) — Exact Kaizen checkbox labels
# Source: Live Kaizen CBD form screenshot (verified 2026-03-08)
# NOTE: Kaizen's SLO numbering differs from rcemcurriculum.co.uk — use these numbers.
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

def _parse_rcem_kc_full_text() -> dict[str, str]:
    full_text: dict[str, str] = {}
    current_slo: str | None = None
    for line in RCEM_KC_MAP.splitlines():
        slo_match = re.match(r"\s*SLO(\d+):", line)
        if slo_match:
            current_slo = f"SLO{int(slo_match.group(1))}"
            continue
        kc_match = re.match(r"\s*KC(\d+):\s*(.+)", line)
        if current_slo and kc_match:
            code = f"{current_slo} KC{int(kc_match.group(1))}"
            full_text[code] = f"{code}: {kc_match.group(2).strip()}"
    return full_text


KC_FULL_TEXT = _parse_rcem_kc_full_text() | {
    "SLO11 KC1": (
        "SLO11 KC1: be able to provide clinical leadership on effective "
        "Quality Improvement work (2025 Update)"
    ),
    "SLO11 KC2": (
        "SLO11 KC2: be able to support and develop a culture of departmental "
        "safety, and good clinical governance (2025 Update)"
    ),
    "SLO12 KC2": (
        "SLO12 KC2: be able to investigate a patient safety incident, "
        "participate and contribute effectively to department clinical "
        "governance activities and risk reduction processes (2025 Update)"
    ),
}

def canonical_kc(capability):
    if not isinstance(capability, str):
        return None
    match = re.fullmatch(r"\s*(SLO[1-9]\d*)\s+(KC[1-9]\d*)(?:\s*:\s*\S.*)?\s*",
                         capability, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return KC_FULL_TEXT.get(f"{match[1].upper()} {match[2].upper()}")


def canonical_kcs(capabilities, *, strict=False):
    if not isinstance(capabilities, list):
        raise ValueError("Invalid KC list")
    result = []
    dropped = 0
    for capability in capabilities:
        canonical = canonical_kc(capability)
        if canonical is None:
            dropped += 1
        elif canonical not in result:
            result.append(canonical)
    if dropped:
        logger.warning("Dropped invalid curriculum capabilities: count=%d", dropped)
        if strict:
            raise ValueError("Unknown or malformed KC")
    return result


def validate_curriculum(fields):
    """One catalogue choke point; never trust model labels or orphan SLOs.

    Accept legacy SLO-only selections when no KCs were supplied (including
    Pydantic default empty lists). For nonempty KC input, derive SLOs solely
    from the validated selection, even if every supplied KC is rejected.
    Possible metadata remains separate and can never become a selected link.
    No source text or rejected values are logged.
    """
    out = dict(fields)
    values = out.get('key_capabilities')
    if not isinstance(values, list):
        values = [] if values is None else [values]
    if values:
        out['key_capabilities'] = canonical_kcs(values)
        out['curriculum_links'] = list(dict.fromkeys(kc.split()[0] for kc in out['key_capabilities']))
    else:
        if 'key_capabilities' in out:
            out['key_capabilities'] = []
        if 'curriculum_links' in out:
            known = {kc.split()[0] for kc in KC_FULL_TEXT}
            links = out['curriculum_links']
            if not isinstance(links, list):
                links = [] if links is None else [links]
            out['curriculum_links'] = list(dict.fromkeys(v for v in links if isinstance(v, str) and v in known))
    if 'possible_key_capability' in out:
        candidate = out['possible_key_capability']
        canonical = canonical_kc(candidate.get('capability')) if isinstance(candidate, dict) else None
        if candidate is not None and canonical is None:
            logger.warning('Dropped invalid curriculum capabilities: count=1')
        out['possible_key_capability'] = {'capability': canonical} if canonical else None
    return out
