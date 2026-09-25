import asyncio
import json
from unittest.mock import patch

import pytest

from extractor import _prepare_case_description_for_model, extract_cbd_data
from privacy_guard import deidentify_clinical_text, privacy_summary


def test_privacy_guard_deidentifies_uk_patient_identifiers():
    text = (
        "Patient Aisha Khan, NHS No 943 476 5919, MRN KGH-123456, "
        "hospital number KH1234567, DOB 14/02/1977, attended Kingston Hospital, Ward Astor."
    )

    redacted, findings = deidentify_clinical_text(text)

    assert findings
    for identifier in ("Aisha Khan", "943 476 5919", "KGH-123456", "KH1234567", "14/02/1977", "Kingston Hospital", "Ward Astor"):
        assert identifier not in redacted
    assert "[NHS number]" in redacted
    assert "[MRN]" in redacted
    assert "[hospital number]" in redacted
    assert "[date of birth]" in redacted


def test_privacy_summary_is_phi_safe():
    summary = privacy_summary(["MRN KGH-123456 at Kingston Hospital"])

    assert summary["status"] == "blocked"
    assert summary["high_risk_count"] >= 2
    assert "KGH-123456" not in json.dumps(summary)
    assert "Kingston Hospital" not in json.dumps(summary)


def test_prepare_case_description_for_model_removes_identifiers():
    cleaned = _prepare_case_description_for_model(
        "I saw Mr Ben Whitfield, NHS No 401 023 2137, in Ward Astor at Kingston Hospital."
    )

    assert "Ben Whitfield" not in cleaned
    assert "401 023 2137" not in cleaned
    assert "Ward Astor" not in cleaned
    assert "Kingston Hospital" not in cleaned


def test_extract_cbd_data_prompt_receives_deidentified_case():
    captured = {}

    async def fake_generate(prompt: str) -> str:
        captured["prompt"] = prompt
        return json.dumps({
            "form_type": "CBD",
            "date_of_encounter": "",
            "patient_age": "",
            "patient_presentation": "Chest pain",
            "clinical_setting": "Emergency Department",
            "stage_of_training": None,
            "trainee_role": "",
            "clinical_reasoning": "I assessed chest pain.",
            "reflection": "I will continue to use structured assessment.",
            "level_of_supervision": "Indirect",
            "supervisor_name": None,
            "curriculum_links": [],
            "key_capabilities": [],
        })

    with patch("extractor._generate", fake_generate):
        asyncio.run(
            extract_cbd_data("I saw Mr Ben Whitfield, MRN KGH-123456, at Kingston Hospital with chest pain.")
        )

    assert "Ben Whitfield" not in captured["prompt"]
    assert "KGH-123456" not in captured["prompt"]
    assert "Kingston Hospital" not in captured["prompt"]


# === Gap-closure run: identifier forms the review found slipping through ===
#
# All three were verified failing before these rules existed: the MRN colon
# form, an untitled patient name in a note header, and a street address.

class TestIdentifierFormsFoundInReview:
    def test_mrn_colon_space_and_hyphen_forms_all_redact(self):
        for written_form in ("MRN: A44571 was admitted", "MRN A44571 was admitted", "MRN-A44571 was admitted"):
            redacted, findings = deidentify_clinical_text(written_form)
            assert "A44571" not in redacted, f"MRN leaked from {written_form!r}: {redacted!r}"
            assert "[MRN]" in redacted
            assert "MRN" in [f.label for f in findings]
        # The verb survives: the identifier is replaced, not the sentence.
        assert deidentify_clinical_text("MRN: A44571 was admitted")[0] == "[MRN] was admitted"

    def test_mrn_without_an_identifier_keeps_the_sentence_intact(self):
        """"MRN was checked" must not lose its verb to the replacement."""
        assert deidentify_clinical_text("MRN was checked on arrival")[0] == "MRN was checked on arrival"

    def test_untitled_patient_name_in_note_header_redacts(self):
        redacted, findings = deidentify_clinical_text("Jane Doe, 54F, chest pain")
        assert "Jane" not in redacted and "Doe" not in redacted
        assert redacted == "[patient name], 54F, chest pain"
        assert "PATIENT_NAME" in [f.label for f in findings]

    def test_untitled_patient_name_keeps_the_demographics_the_form_needs(self):
        redacted, _ = deidentify_clinical_text("Jane Doe, 54 female, presented with sepsis")
        assert "54 female" in redacted
        assert "sepsis" in redacted
        assert "Jane" not in redacted

    def test_street_address_redacts_without_eating_the_city(self):
        redacted, findings = deidentify_clinical_text("lives at 14 Elm Road, Manchester")
        assert redacted == "lives at [address], Manchester"
        assert "ADDRESS" in [f.label for f in findings]

    def test_street_address_covers_the_common_street_types(self):
        for line in (
            "22 Oak Street", "3 Victoria Avenue", "101 Church Lane", "7 Mill Close",
            "45 Kings Drive", "9 Abbey Way", "12 Chapel Court", "60 Park Crescent",
            "5 Beech Terrace", "18 Cherry Grove", "2 Market Place", "31 Primrose Hill",
            "77 Rose Gardens",
        ):
            redacted, _ = deidentify_clinical_text(f"address on file: {line}")
            assert "[address]" in redacted, f"missed {line!r} -> {redacted!r}"


class TestNegativeCorpusMustSurviveUntouched:
    """A false positive that mangles clinical content is worse than a miss.

    These are the exact strings the foreground review pinned. Each is checked
    byte-for-byte, not merely 'contains'.
    """

    def test_clinical_shorthand_and_drug_names_are_byte_identical(self):
        for line in (
            "Chest pain, ECG normal, troponin negative",
            "Well's score 2, PE unlikely",
            "co-amoxiclav 1.2g IV",
            "54F, GCS 15, BM 6.1",
        ):
            redacted, findings = deidentify_clinical_text(line)
            assert redacted == line, f"{line!r} was altered to {redacted!r} by {[f.label for f in findings]}"
            assert findings == []

    def test_clinician_name_still_becomes_the_doctor_not_a_patient_name(self):
        redacted, findings = deidentify_clinical_text("discussed with Dr Sarah Patel")
        assert redacted == "discussed with the doctor"
        assert [f.label for f in findings] == ["CLINICIAN_NAME"]

    def test_named_hospital_keeps_its_own_pre_existing_replacement(self):
        redacted, findings = deidentify_clinical_text("Manchester Royal Infirmary")
        assert redacted == "the hospital"
        assert [f.label for f in findings] == ["NAMED_HOSPITAL"]

    def test_title_case_clinical_phrase_without_an_age_is_not_a_patient_name(self):
        for line in (
            "Chest Pain, reviewed in ED",
            "Sepsis Six, completed within the hour",
        ):
            assert deidentify_clinical_text(line)[0] == line

    def test_redaction_is_idempotent(self):
        """A later pass over already-redacted text must be a no-op.

        Photo text is redacted at the read, and the case-start path redacts
        again; a second pass must not corrupt the placeholders.
        """
        once, _ = deidentify_clinical_text("Jane Doe, 54F. MRN: A44571. 14 Elm Road, Manchester")
        twice, _ = deidentify_clinical_text(once)
        assert twice == once


# === Gap-closure run: photos sent *after* the case started ===

class TestLaterPhotosAreRedactedToo:
    """Only the first photo used to be redacted (it went through
    `_process_case_text`). A photo sent into an open case — at draft review,
    as approval feedback, or as a refinement reply — reached storage, the
    audit trail and the model as raw OCR text.

    These drive the real handlers, not the helper in isolation.
    """

    PHOTO_OCR_WITH_IDENTIFIERS = (
        "Jane Doe, 54F, chest pain. MRN: A44571. NHS No 943 476 5919. "
        "Lives at 14 Elm Road, Manchester. Troponin raised, treated as ACS."
    )

    @staticmethod
    def _photo_update(sim):
        from unittest.mock import AsyncMock, MagicMock

        update = sim._make_text_update("")
        photo = MagicMock()
        file_obj = MagicMock()
        file_obj.download_to_drive = AsyncMock()
        photo.get_file = AsyncMock(return_value=file_obj)
        update.message.photo = [photo]
        update.message.text = None
        update.message.caption = None
        return update

    def _assert_scrubbed(self, blob: str):
        for identifier in ("Jane Doe", "A44571", "943 476 5919", "14 Elm Road"):
            assert identifier not in blob, f"{identifier!r} leaked into: {blob!r}"

    @pytest.mark.asyncio
    async def test_approval_media_feedback_photo_is_redacted_before_model_and_storage(self):
        from unittest.mock import AsyncMock, patch

        from bot import AWAIT_APPROVAL, _store_draft, handle_approval_media_feedback
        from models import CBDData
        from tests.bot_simulator import BotSimulator

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data["case_text"] = "Chest pain case already in progress."
        context.user_data["case_input_source"] = "text"
        _store_draft(context, CBDData(patient_presentation="Chest pain"))

        redrafted = CBDData(patient_presentation="Chest pain, troponin raised")
        with patch("bot.extract_from_image", new=AsyncMock(return_value=self.PHOTO_OCR_WITH_IDENTIFIERS)), \
             patch("bot.extract_cbd_data", new=AsyncMock(return_value=redrafted)) as extract_mock, \
             patch("bot.get_voice_profile", return_value=None):
            result = await handle_approval_media_feedback(self._photo_update(sim), context)

        assert result == AWAIT_APPROVAL
        extract_mock.assert_awaited()

        # Nothing identifying may reach the model...
        sent_to_model = repr(extract_mock.await_args.args) + repr(extract_mock.await_args.kwargs)
        self._assert_scrubbed(sent_to_model)
        # ...nor the stored case text the draft and audit trail are built from.
        self._assert_scrubbed(str(context.user_data.get("case_text", "")))
        # The clinical content itself must survive the scrub.
        assert "troponin" in sent_to_model.lower()

    @pytest.mark.asyncio
    async def test_draft_review_photo_is_redacted_before_model_and_storage(self):
        from unittest.mock import AsyncMock, patch

        from bot import handle_template_review_media
        from models import FormDraft
        from tests.bot_simulator import BotSimulator

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data["case_text"] = "Chest pain case already in progress."
        context.user_data["chosen_form"] = "CBD"

        analysed = FormDraft(form_type="CBD", uuid="uuid-cbd", fields={"patient_presentation": "Chest pain"})
        with patch("bot.extract_from_image", new=AsyncMock(return_value=self.PHOTO_OCR_WITH_IDENTIFIERS)), \
             patch("bot._analyse_selected_form", new=AsyncMock(return_value=analysed)) as analyse_mock:
            await handle_template_review_media(self._photo_update(sim), context)

        analyse_mock.assert_awaited()
        self._assert_scrubbed(repr(analyse_mock.await_args.args) + repr(analyse_mock.await_args.kwargs))
        self._assert_scrubbed(str(context.user_data.get("case_text", "")))

    @pytest.mark.asyncio
    async def test_doctor_is_told_what_was_removed_from_a_later_photo(self):
        """Proportionate surfacing: one line on the acknowledgement the photo
        was already getting — no second message, no new blocking step."""
        from unittest.mock import AsyncMock, patch

        from bot import _store_draft, handle_approval_media_feedback
        from models import CBDData
        from tests.bot_simulator import BotSimulator

        sim = BotSimulator()
        context = sim._make_context()
        context.user_data["case_text"] = "Chest pain case already in progress."
        _store_draft(context, CBDData(patient_presentation="Chest pain"))

        with patch("bot.extract_from_image", new=AsyncMock(return_value=self.PHOTO_OCR_WITH_IDENTIFIERS)), \
             patch("bot.extract_cbd_data", new=AsyncMock(return_value=CBDData(patient_presentation="Chest pain"))), \
             patch("bot.get_voice_profile", return_value=None):
            await handle_approval_media_feedback(self._photo_update(sim), context)

        everything_said = " ".join(text for _, text, _ in sim.messages_sent if text)
        assert "I removed" in everything_said
        assert "a record number" in everything_said or "an NHS number" in everything_said
