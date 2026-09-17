"""Offline pins: an ESLE case must be offered ESLE.

Regression source — two cases pasted into the product bot on 2026-09-17 both
came back "Best fit: Case-Based Discussion" with CBD + Reflective Practice Log
and no ESLE anywhere. Case 2 named an ESLE in its first sentence.

Two independent defects sat behind that:

1. ``extract_explicit_form_type`` only knew "esle" as a SECONDARY code, gated
   behind an intent phrase. "I did a 45-minute ESLE" has no intent phrase
   ("did a" is not in the list), so a named form was silently dropped. The
   spelled-out name "supervised learning event" matched nothing at all.
2. The AI recommender's prompt is deliberately hostile to ESLE, and that bias
   also suppressed a genuine shift/area-level case.

Synthetic case text only. No Kaizen, no credentials, no Telegram, no live
model — the recommender's model call is stubbed.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import extractor


# The two real 2026-09-17 messages, retyped as synthetic fixtures (no patient
# identifiers: age/sex only, as the doctor originally wrote them).
CASE_COVERING_MAJORS = (
    "71M, 3 days of worsening breathlessness, brought in overnight, new oxygen "
    "requirement. I was covering the majors area. He looked comfortable at triage "
    "but his respiratory rate was 26 with accessory muscle use, so I moved him to a "
    "monitored cubicle immediately rather than waiting for the next available "
    "doctor. I started controlled oxygen and had ECG, CXR and blood gases done "
    "within ten minutes. The gas showed type 2 respiratory failure. I spoke to the "
    "respiratory registrar, who agreed with NIV, but the NIV machine was already in "
    "use, so I escalated to the nurse in charge and asked a colleague to hold my "
    "queue while I collected a second machine from the equipment store. I also "
    "answered a foundation doctor's question about analgesia for a fractured NOF "
    "patient, and handed over my remaining queue with clear priorities before the "
    "patient went to HDU. I noticed that my documentation lagged behind what I had "
    "already done.\n"
    "Learning: escalate early when equipment is the rate-limiting step, and keep "
    "notes running in parallel with clinical work rather than catching up "
    "afterwards."
)

CASE_NAMED_ESLE = (
    "I did a 45-minute ESLE observing only the resuscitation area during a quiet "
    "period. I reviewed a single patient with chest pain, chose the investigations "
    "myself, interpreted the ECG independently and made the disposition decision to "
    "discharge with ambulatory follow-up. No other clinical areas were involved.\n"
    "Learning: I need to trust my own ECG interpretation earlier rather than "
    "waiting for a second opinion."
)

# An ordinary single-patient case: no coverage, no shift-level responsibility,
# nobody observing. This is the force-fitting guard.
CASE_ORDINARY_CLINICAL = (
    "45F with right iliac fossa pain for 18 hours, nauseated, tender with guarding. "
    "I took a history, examined her, requested bloods and a CT abdomen, discussed "
    "with the surgical registrar and she went for an appendicectomy the same "
    "evening.\n"
    "Learning: I should have prescribed analgesia before imaging rather than after."
)

# The exact shape the live bot's recommender returned for these cases.
OBSERVED_MODEL_REPLY = json.dumps([
    {"form_type": "CBD", "rationale": "You managed a specific patient end to end."},
    {"form_type": "REFLECT_LOG", "rationale": "You describe what you learned."},
])


def _recommend(monkeypatch, case_text: str, model_reply: str = OBSERVED_MODEL_REPLY):
    """Run the recommender with the model call stubbed out."""

    async def fake_generate(_prompt: str, retries: int = 1, tier: str = "") -> str:
        return model_reply

    monkeypatch.setattr(extractor, "_generate", fake_generate)
    return asyncio.run(extractor.recommend_form_types(case_text))


def _form_types(recommendations) -> list[str]:
    return [rec.form_type for rec in recommendations]


# --- 1. Naming a form must always win -------------------------------------

@pytest.mark.parametrize("text", [
    "I did a 45-minute ESLE observing only the resuscitation area.",
    "ESLE from last Tuesday's late shift.",
    "Please file a supervised learning event for me.",
    "extended supervised learning event with my ES",
    "emergency medicine supervised learning event",
    "I have two ESLEs left to do this year.",
])
def test_named_esle_is_detected_without_an_intent_phrase(text):
    """A doctor who names an ESLE gets an ESLE, phrased however they like.

    "I did a 45-minute ESLE" is the real 2026-09-17 message and contains no
    intent phrase at all, so the old secondary-code gate returned None.
    """
    assert extractor.extract_explicit_form_type(text) == "ESLE_ASSESS"


def test_case_two_is_routed_to_esle_by_explicit_detection():
    assert extractor.extract_explicit_form_type(CASE_NAMED_ESLE) == "ESLE_ASSESS"


@pytest.mark.parametrize("text", [
    "He was restless and pale on arrival.",
    "The vesicle was deroofed and swabbed.",
    "I started a statin and arranged GP follow-up.",
    "She had measles as a child.",
])
def test_esle_is_not_matched_as_a_substring(text):
    """Word-boundary matching: no ordinary clinical prose may name ESLE."""
    assert extractor.extract_explicit_form_type(text) != "ESLE_ASSESS"


def test_another_named_form_still_wins_over_a_bare_mention():
    """An earlier, explicit form name outranks an incidental ESLE mention."""
    assert extractor.extract_explicit_form_type(
        "Case-based discussion please, though I could also use it for an ESLE."
    ) == "CBD"


# --- 2. The recommendation path -------------------------------------------

def test_covering_an_area_surfaces_esle_alongside_cbd(monkeypatch):
    """Case 1: the model returns CBD + Reflective Log; ESLE must still appear.

    This is the observed defect verbatim — the stub returns exactly what the
    live recommender returned on 2026-09-17.
    """
    forms = _form_types(_recommend(monkeypatch, CASE_COVERING_MAJORS))
    assert "ESLE_ASSESS" in forms, forms
    # The model's own best fit is not thrown away.
    assert "CBD" in forms, forms


def test_named_esle_leads_the_recommendations(monkeypatch):
    """Case 2, if it ever reaches the recommender, still puts ESLE first."""
    forms = _form_types(_recommend(monkeypatch, CASE_NAMED_ESLE))
    assert forms[0] == "ESLE_ASSESS", forms


def test_ordinary_clinical_case_is_not_force_fitted_to_esle(monkeypatch):
    """The guard must not fire on a single-patient case with no shift context."""
    forms = _form_types(_recommend(monkeypatch, CASE_ORDINARY_CLINICAL))
    assert "ESLE_ASSESS" not in forms, forms
    assert forms == ["CBD", "REFLECT_LOG"], forms


def test_esle_is_not_duplicated_when_the_model_already_picked_it(monkeypatch):
    model_reply = json.dumps([
        {"form_type": "ESLE", "rationale": "Shift-level observation."},
        {"form_type": "CBD", "rationale": "Also a managed case."},
    ])
    forms = _form_types(_recommend(monkeypatch, CASE_COVERING_MAJORS, model_reply))
    assert forms.count("ESLE_ASSESS") == 1, forms


def test_recommendations_stay_capped_at_three(monkeypatch):
    model_reply = json.dumps([
        {"form_type": "CBD", "rationale": "a"},
        {"form_type": "REFLECT_LOG", "rationale": "b"},
        {"form_type": "ACAT", "rationale": "c"},
    ])
    forms = _form_types(_recommend(monkeypatch, CASE_COVERING_MAJORS, model_reply))
    assert len(forms) == 3, forms
    assert "ESLE_ASSESS" in forms, forms


@pytest.mark.parametrize("text", [
    "I was covering the majors area overnight.",
    "I led the shift as the senior decision maker.",
    "I was in charge of the department while the consultant was in resus.",
    "My consultant was supernumerary and observed me work for three hours.",
    "The ES watched me work the shop floor across the afternoon.",
])
def test_shift_level_responsibility_is_recognised(text):
    assert extractor._has_supervised_shift_signal(text) is True


@pytest.mark.parametrize("text", [
    CASE_ORDINARY_CLINICAL,
    "I learned a lot from this significant case.",
    "I observed a chest drain being inserted by the registrar.",
    "I reflected on a difficult conversation with a relative.",
])
def test_ordinary_text_carries_no_shift_signal(text):
    assert extractor._has_supervised_shift_signal(text) is False


# --- 3. The manual Forms route --------------------------------------------

def test_esle_is_reachable_from_the_forms_menu_on_every_profile():
    """The "Forms" button must genuinely reach ESLE for every saved profile.

    2025 curriculum exposes ESLE_ASSESS directly; 2021 resolves it to the
    ESLE_2021 variant. Either way a button is rendered under Clinical.
    """
    import bot
    from extractor import FORM_UUIDS

    assert "ESLE_ASSESS" in bot.FORM_CATEGORIES["🩺 Clinical"]
    for curriculum in ("2025", "2021"):
        for level in ("ST3", "ST4", "ST5", "ST6", "SAS"):
            allowed = bot._filter_forms_by_curriculum(
                bot._allowed_forms_for_training_level(level), curriculum
            )
            allowed = {ft for ft in allowed if FORM_UUIDS.get(ft)}
            variant = bot._form_type_for_curriculum("ESLE_ASSESS", curriculum)
            assert "ESLE_ASSESS" in allowed or variant in allowed, (
                curriculum, level, variant
            )
