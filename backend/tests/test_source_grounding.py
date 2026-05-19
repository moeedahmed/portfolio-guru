"""Anti-fabrication tests for image-derived case input.

Regression for the rib-fracture incident: screenshots of a regional block /
right-sided rib fractures / pulmonary nodule follow-up were turned into a
CBD draft full of CPR, ALS, ROSC and other invented resuscitation content.

These tests assert defense-in-depth:
  1. The vision prompt forbids extrapolation from imaging into clinical
     narrative.
  2. recommend_form_types is told the input came from an image and is biased
     toward procedure / reflection / DOPS-style forms when the source is
     sparse or procedural.
  3. extract_cbd_data / extract_form_data inject an image-source grounding
     block when input_source indicates a photo.
  4. enforce_image_source_grounding strips sentences containing high-risk
     fabrication terms (CPR, ALS, ROSC, CT head, coronary angiography, …)
     that are not anchored in the source text.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


RIB_FRACTURE_IMAGE_TEXT = (
    "Right-sided rib fractures (ribs 4 to 7). Serratus anterior plane (SA/ES) "
    "block performed under ultrasound guidance with levobupivacaine. "
    "No pneumothorax visible on follow-up CT chest. Right chest wall soft "
    "tissue haematoma. Incidental pulmonary nodule noted, plan for outpatient "
    "follow-up imaging."
)

RIB_FRACTURE_WITH_WEAK_ADMIN_CPR_TEXT = (
    "Links Documentation Scheduling. Age 68. Sex Male. Resus: For CPR. "
    "Procedure note: right-sided displaced rib fractures. Serratus anterior "
    "and erector spinae block performed under ultrasound guidance. "
    "Levobupivacaine and lidocaine used. Patient tolerated the procedure well "
    "with no complications. CT report: multilevel right-sided rib fractures, "
    "no pneumothorax, right iliac subcutaneous haematoma, small right lower "
    "lobe pulmonary nodule for 3-month CT follow-up. Plan: SA and ES block."
)

BANNED_RESUS_TERMS = [
    "cpr",
    "cardiac arrest",
    "als",
    "advanced life support",
    "defibrillation",
    "adrenaline",
    "rosc",
    "ct head",
    "coronary angiography",
]


class TestVisionPromptIsSourceGrounded:
    def test_vision_prompt_forbids_extrapolation_from_imaging(self):
        """The image extraction prompt must explicitly forbid inferring
        management/resuscitation narrative from imaging findings."""
        from vision import IMAGE_EXTRACTION_PROMPT

        prompt_lower = IMAGE_EXTRACTION_PROMPT.lower()
        # Must mention source-grounding / verbatim transcription
        assert "explicitly visible" in prompt_lower or "only what" in prompt_lower
        # Must forbid extrapolation
        assert "do not" in prompt_lower
        assert "infer" in prompt_lower or "extrapolate" in prompt_lower or "interpretation" in prompt_lower
        # Must mention the case-discussion framing must NOT be added
        assert "case discussion" in prompt_lower or "narrative" in prompt_lower
        # Must keep the existing NOT_CLINICAL contract
        assert "NOT_CLINICAL" in IMAGE_EXTRACTION_PROMPT


class TestRecommenderHonoursImageSource:
    @pytest.mark.asyncio
    async def test_recommend_form_types_includes_portfolio_skill_rubric(self):
        """Form choice should inherit the durable Claude/Medic portfolio
        heuristics without depending on those skills at runtime."""
        from extractor import recommend_form_types

        captured = {}

        async def fake_generate(prompt, retries=1, tier=""):
            captured["prompt"] = prompt
            return json.dumps([
                {"form_type": "CBD", "rationale": "Single-patient clinical reasoning."}
            ])

        with patch("extractor._generate", new=AsyncMock(side_effect=fake_generate)):
            await recommend_form_types(
                "I managed a patient with breathlessness and changed management after senior discussion.",
                input_source="text",
            )

        prompt = captured["prompt"].lower()
        assert "portfolio skill quality rubric" in prompt
        assert "match what actually happened, not keywords" in prompt
        assert "prefer the most specific form before cbd" in prompt
        assert "select key capabilities first" in prompt

    @pytest.mark.asyncio
    async def test_recommend_with_image_source_injects_image_guard(self):
        """When input_source is 'photo', the recommender prompt must include
        the image-derived bias toward procedure/reflection forms."""
        from extractor import recommend_form_types

        captured = {}

        async def fake_generate(prompt, retries=1, tier=""):
            captured["prompt"] = prompt
            return json.dumps([
                {"form_type": "PROC_LOG", "rationale": "Regional block performed."}
            ])

        with patch("extractor._generate", new=AsyncMock(side_effect=fake_generate)):
            await recommend_form_types(RIB_FRACTURE_IMAGE_TEXT, input_source="photo")

        prompt = captured["prompt"].lower()
        assert "image" in prompt or "photo" in prompt
        # The prompt must instruct against extrapolating beyond what is in the source
        assert "do not invent" in prompt or "do not fabricate" in prompt or "only the facts" in prompt
        # The prompt must bias toward procedure/reflection-style forms
        assert "proc_log" in prompt or "procedure" in prompt
        assert "reflect" in prompt

    @pytest.mark.asyncio
    async def test_image_recommendation_drops_cbd_when_only_admin_cpr_anchor_exists(self):
        """A header/admin phrase like 'Resus: For CPR' must not make CBD
        survive when the body evidence is a regional block procedure note."""
        from extractor import recommend_form_types

        async def fake_generate(prompt, retries=1, tier=""):
            return json.dumps([
                {"form_type": "CBD", "rationale": "For CPR in resus."}
            ])

        with patch("extractor._generate", new=AsyncMock(side_effect=fake_generate)):
            recommendations = await recommend_form_types(
                RIB_FRACTURE_WITH_WEAK_ADMIN_CPR_TEXT,
                input_source="photo",
            )

        form_types = [rec.form_type for rec in recommendations]
        assert "CBD" not in form_types
        assert form_types[:2] == ["PROC_LOG", "DOPS"]

    @pytest.mark.asyncio
    async def test_recommend_with_text_source_omits_image_guard(self):
        """Text-input cases should not get the image guard — doctors who
        typed their case have authored the content themselves."""
        from extractor import recommend_form_types

        captured = {}

        async def fake_generate(prompt, retries=1, tier=""):
            captured["prompt"] = prompt
            return json.dumps([
                {"form_type": "CBD", "rationale": "ED case management."}
            ])

        case_text = (
            "45 year old male presented to ED with central chest pain radiating "
            "to left arm. Troponin positive. Managed as ACS with aspirin and "
            "clopidogrel, escalated to cardiology, transferred for PCI."
        )
        with patch("extractor._generate", new=AsyncMock(side_effect=fake_generate)):
            await recommend_form_types(case_text, input_source="text")

        prompt = captured["prompt"].lower()
        # No image-specific guard should be in a text-source prompt
        assert "image-derived" not in prompt and "this case was extracted from a photo" not in prompt


class TestExtractorHonoursImageSource:
    @pytest.mark.asyncio
    async def test_extract_cbd_prompt_requires_synthesis_across_notes_and_story(self):
        """The CBD prompt must tell the model to use later image/OCR note
        evidence, not just the first free-text narrative."""
        from extractor import extract_cbd_data

        captured = {}

        async def fake_generate(prompt, retries=1, tier=""):
            captured["prompt"] = prompt
            return json.dumps({
                "form_type": "CBD",
                "date_of_encounter": "",
                "patient_age": "",
                "patient_presentation": "Shortness of breath",
                "clinical_setting": "",
                "stage_of_training": None,
                "trainee_role": "",
                "clinical_reasoning": "I considered pleural effusion and changed the plan after consultant discussion.",
                "reflection": "I learned to integrate the wider clinical picture before choosing an invasive procedure.",
                "level_of_supervision": "",
                "supervisor_name": None,
                "curriculum_links": [],
                "key_capabilities": [],
            })

        case_text = (
            "I initially planned to drain a pleural effusion after CXR and ultrasound.\n\n"
            "Image/OCR notes: BNP raised. AF with LBBB. Bedside echo possible reduced "
            "contractility. Plan antibiotics, furosemide, OptiFlow, ITU review, medical team aware."
        )

        with patch("extractor._generate", new=AsyncMock(side_effect=fake_generate)):
            await extract_cbd_data(case_text, input_source="text", leave_missing_blank=True)

        prompt = captured["prompt"].lower()
        assert "one evidence bundle" in prompt
        assert "first narrative drown" in prompt
        assert "later image/note evidence" in prompt
        assert "portfolio skill quality rubric" in prompt
        assert "third parties by role" in prompt
        assert "key capabilities first" in prompt
        assert "bnp" in prompt
        assert "senior challenge" in prompt
        assert "no septations" in prompt

    @pytest.mark.asyncio
    async def test_extract_form_data_with_image_source_injects_grounding_block(self):
        """extract_form_data must add a stronger source-grounding block when
        input_source is 'photo' so the LLM only fills fields from explicit
        content in the source."""
        from extractor import extract_form_data

        captured = {}

        async def fake_generate(prompt, retries=1, tier=""):
            captured["prompt"] = prompt
            # Return a minimal, grounded PROC_LOG JSON
            return json.dumps({
                "date_of_activity": "",
                "stage_of_training": "",
                "year_of_training": "",
                "higher_procedural_skill": "Other",
                "higher_procedural_skill_other": "Serratus anterior plane block",
                "intermediate_procedural_skill": "",
                "accs_procedural_skill": "",
                "age_of_patient": "",
                "reflective_comments": "",
                "curriculum_links": ["SLO6"],
                "key_capabilities": [],
            })

        with patch("extractor._generate", new=AsyncMock(side_effect=fake_generate)):
            await extract_form_data(
                RIB_FRACTURE_IMAGE_TEXT,
                "PROC_LOG",
                input_source="photo",
                leave_missing_blank=True,
            )

        prompt = captured["prompt"].lower()
        assert "image" in prompt or "photo" in prompt
        # Must instruct facts-only mode and forbid resuscitation-style
        # narrative continuations that don't appear in the source.
        assert "do not invent" in prompt or "do not infer" in prompt or "do not fabricate" in prompt

    @pytest.mark.asyncio
    async def test_extract_cbd_data_with_image_source_injects_grounding_block(self):
        from extractor import extract_cbd_data

        captured = {}

        async def fake_generate(prompt, retries=1, tier=""):
            captured["prompt"] = prompt
            return json.dumps({
                "form_type": "CBD",
                "date_of_encounter": "",
                "patient_age": "",
                "patient_presentation": "Right-sided rib fractures",
                "clinical_setting": "",
                "stage_of_training": None,
                "trainee_role": "",
                "clinical_reasoning": "",
                "reflection": "",
                "level_of_supervision": "",
                "supervisor_name": None,
                "curriculum_links": [],
                "key_capabilities": [],
            })

        with patch("extractor._generate", new=AsyncMock(side_effect=fake_generate)):
            await extract_cbd_data(
                RIB_FRACTURE_IMAGE_TEXT,
                input_source="photo",
                leave_missing_blank=True,
            )

        prompt = captured["prompt"].lower()
        assert "image" in prompt or "photo" in prompt
        assert "do not invent" in prompt or "do not fabricate" in prompt or "do not infer" in prompt


class TestEnforceImageSourceGrounding:
    def test_portfolio_quality_polish_softens_judgement_language(self):
        """Draft previews should not file blunt self-punitive judgement
        language or overstate simple effusion ultrasound findings."""
        from extractor import _humanize_all_fields

        fields = {
            "reflection": (
                "This was wrong judgement. I made a mistake and was narrowly "
                "focused on a chest strain."
            ),
            "clinical_reasoning": (
                "Ultrasound showed effusion with no septation, suggesting a "
                "transudative effusion. Plan was to admit under ITU on board."
            ),
        }

        polished = _humanize_all_fields(fields)
        joined = " ".join(polished.values()).lower()

        assert "wrong judgement" not in joined
        assert "wrong judgment" not in joined
        assert "mistake" not in joined
        assert "chest strain" not in joined
        assert "no septation, suggesting a transudative effusion" not in joined
        assert "initial judgement" in joined
        assert "non-complex effusion" in joined
        assert "medical admission kept under review" in joined

    def test_portfolio_quality_polish_deidentifies_third_parties_and_centres(self):
        """Narrative fields should use roles/generic centres rather than
        third-party names or identifiable tertiary centres."""
        from extractor import _humanize_all_fields

        fields = {
            "reflection": (
                "I discussed the case with Dr Alice Smith after the patient had "
                "previous surgery in 2009 at Royal Brompton Hospital."
            )
        }

        polished = _humanize_all_fields(fields)
        value = polished["reflection"]

        assert "Dr Alice Smith" not in value
        assert "Royal Brompton" not in value
        assert "2009" not in value
        assert "the doctor" in value
        assert "tertiary centre" in value

    def test_weak_admin_cpr_anchor_does_not_support_resus_narrative(self):
        """The second failure: OCR saw 'For CPR' in the admin/header area,
        then allowed a full CPR/ALS/ROSC story. That phrase is not enough."""
        from extractor import enforce_image_source_grounding

        fields = {
            "reflection": (
                "I led CPR in resus and followed ALS. ROSC was achieved. "
                "The regional block was performed under ultrasound guidance."
            )
        }

        cleaned, stripped = enforce_image_source_grounding(
            fields, RIB_FRACTURE_WITH_WEAK_ADMIN_CPR_TEXT
        )

        value = cleaned["reflection"].lower()
        assert "cpr" not in value
        assert "als" not in value
        assert "rosc" not in value
        assert "regional block" in value or "ultrasound" in value
        assert stripped

    def test_strips_unsupported_resuscitation_terms(self):
        """Narrative sentences containing CPR/ALS/ROSC/etc that are NOT in
        the source text must be removed."""
        from extractor import enforce_image_source_grounding

        fields = {
            "clinical_reasoning": (
                "I performed CPR and delivered three shocks via defibrillation. "
                "I administered adrenaline and achieved ROSC. "
                "I performed a serratus anterior block under ultrasound for "
                "right-sided rib fractures."
            ),
            "reflection": (
                "On reflection, the ALS protocol was applied correctly and the "
                "trauma CT head was unremarkable. The regional block provided "
                "good analgesia."
            ),
        }

        cleaned, stripped = enforce_image_source_grounding(
            fields, RIB_FRACTURE_IMAGE_TEXT
        )

        for term in BANNED_RESUS_TERMS:
            for value in cleaned.values():
                assert term not in value.lower(), (
                    f"Banned term {term!r} not stripped from field {value!r}"
                )

        # The legitimate source-anchored content must survive in some form.
        joined = " ".join(cleaned.values()).lower()
        assert "rib fracture" in joined or "ribs 4" in joined or "serratus" in joined or "block" in joined

        assert stripped, "Should have reported at least one stripped term"

    def test_keeps_terms_when_anchored_in_source(self):
        """A term that DOES appear in the source must not be stripped — the
        validator is only meant to remove fabricated, source-less content."""
        from extractor import enforce_image_source_grounding

        source = (
            "Trauma call. Patient had cardiac arrest on arrival, CPR in progress. "
            "ALS protocol followed, two cycles. ROSC achieved at 6 minutes."
        )
        fields = {
            "reflection": (
                "We followed ALS, gave adrenaline as per protocol, "
                "achieved ROSC after CPR cycles."
            ),
        }
        cleaned, _ = enforce_image_source_grounding(fields, source)
        # "als", "rosc", "cpr" all appear in source so they should be retained.
        assert "rosc" in cleaned["reflection"].lower()
        assert "als" in cleaned["reflection"].lower()
        assert "cpr" in cleaned["reflection"].lower()

    def test_noop_when_source_text_empty(self):
        """With no source text, the validator must not destructively edit
        fields — that would break the text-input path."""
        from extractor import enforce_image_source_grounding

        fields = {"reflection": "Patient had CPR and ROSC."}
        cleaned, stripped = enforce_image_source_grounding(fields, "")
        assert cleaned["reflection"] == "Patient had CPR and ROSC."
        assert stripped == []


class TestRibFractureFailureMode:
    """End-to-end: extract_form_data with a misbehaving LLM that wants to
    invent CPR/ROSC narrative from a rib-fracture image must NOT produce a
    draft containing the banned resuscitation terms. The legitimate facts
    (rib fractures, regional block) must remain visible."""

    @pytest.mark.asyncio
    async def test_image_derived_draft_strips_fabricated_resus_content(self):
        from extractor import extract_form_data

        # Simulate a misbehaving LLM that tries to embellish the image content
        # with a fabricated trauma resuscitation narrative.
        bad_payload = json.dumps({
            "date_of_activity": "",
            "stage_of_training": "",
            "year_of_training": "",
            "higher_procedural_skill": "Other",
            "higher_procedural_skill_other": "Serratus anterior plane block for right rib fractures",
            "intermediate_procedural_skill": "",
            "accs_procedural_skill": "",
            "age_of_patient": "",
            "reflective_comments": (
                "Patient had cardiac arrest on arrival. I led ALS with CPR and "
                "two cycles of defibrillation, gave adrenaline, achieved ROSC. "
                "CT head was clear. Coronary angiography was arranged. "
                "I performed a serratus anterior block under ultrasound after "
                "the patient was stabilised."
            ),
            "curriculum_links": ["SLO6"],
            "key_capabilities": [
                "SLO6 KC2: the knowledge and psychomotor skills to perform EM procedural skills safely and in a timely fashion (2025 Update)"
            ],
        })

        async def fake_generate(prompt, retries=1, tier=""):
            return bad_payload

        with patch("extractor._generate", new=AsyncMock(side_effect=fake_generate)):
            draft = await extract_form_data(
                RIB_FRACTURE_IMAGE_TEXT,
                "PROC_LOG",
                input_source="photo",
                leave_missing_blank=True,
            )

        joined = " ".join(
            str(v) for v in draft.fields.values() if isinstance(v, str)
        ).lower()

        for term in BANNED_RESUS_TERMS:
            assert term not in joined, (
                f"Banned term {term!r} leaked into final image-derived draft: {joined!r}"
            )

        # The legitimate procedural facts must still be present.
        assert (
            "serratus" in joined
            or "block" in joined
            or "rib fracture" in joined
            or "ribs 4" in joined
        )

    @pytest.mark.asyncio
    async def test_text_derived_draft_preserves_user_resus_content(self):
        """Text-derived input is trusted: when a doctor types CPR/ROSC into
        their own case, we must NOT strip it. The image-source guard must
        only apply to image-derived input."""
        from extractor import extract_form_data

        text_case = (
            "Trauma call: cardiac arrest on arrival, ALS led by me, two cycles "
            "of CPR, defibrillation x1, adrenaline x2, ROSC at 6 min. "
            "Post-ROSC CT head normal."
        )
        payload = json.dumps({
            "date_of_activity": "",
            "stage_of_training": "",
            "year_of_training": "",
            "higher_procedural_skill": "Other",
            "higher_procedural_skill_other": "Adult resuscitation",
            "intermediate_procedural_skill": "",
            "accs_procedural_skill": "",
            "age_of_patient": "",
            "reflective_comments": (
                "I led the ALS team, delivered CPR cycles, defibrillation and "
                "adrenaline, achieved ROSC. CT head was normal."
            ),
            "curriculum_links": ["SLO3"],
            "key_capabilities": [],
        })

        async def fake_generate(prompt, retries=1, tier=""):
            return payload

        with patch("extractor._generate", new=AsyncMock(side_effect=fake_generate)):
            draft = await extract_form_data(
                text_case,
                "PROC_LOG",
                input_source="text",
                leave_missing_blank=True,
            )

        joined = " ".join(
            str(v) for v in draft.fields.values() if isinstance(v, str)
        ).lower()
        # Doctor-authored CPR/ROSC content must survive the text path.
        assert "rosc" in joined
        assert "cpr" in joined or "resuscitation" in joined
