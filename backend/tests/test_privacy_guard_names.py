"""Patient-name de-identification and the all-fields draft sweep.

The incident: an echocardiogram photo produced a draft titled
"Echocardiogram Review - Munawar Ahmed", which would have been saved to Kaizen
as portfolio evidence.

Two independent defects let that through:

1. Scanned reports print the patient in block capitals ("report of patient
   (E R) MUNAWAR AHMED"). Every name rule required Title Case, so the name
   survived `_prepare_case_description_for_model` and reached the model.
2. De-identification of draft output ran only over `_HUMANIZE_FIELDS`, and
   only for values longer than 20 characters. `reflection_title` is in
   neither set, so nothing swept it afterwards.

Both directions are load-bearing. A missed identifier is a PHI leak; a false
positive silently deletes clinical content from a doctor's evidence.
"""

import pytest

from privacy_guard import deidentify_clinical_text, deidentify_draft_fields

ECHO_OCR = (
    "Impression: Mild concentric LVH with SWMA. Poor acoustic windows. "
    "Dr. Saba Hussain Performing physician. "
    "Page 1 of 1 for report of patient (E R) MUNAWAR AHMED"
)


class TestBlockCapitalPatientNames:
    def test_block_capital_name_is_removed_before_the_model_sees_it(self):
        cleaned, _ = deidentify_clinical_text(ECHO_OCR)
        assert "MUNAWAR" not in cleaned
        assert "AHMED" not in cleaned
        assert "[patient name]" in cleaned

    def test_clinical_findings_are_preserved(self):
        cleaned, _ = deidentify_clinical_text(ECHO_OCR)
        assert "Mild concentric LVH" in cleaned
        assert "SWMA" in cleaned
        assert "Poor acoustic windows" in cleaned

    @pytest.mark.parametrize(
        "text",
        [
            # Bare "patient" + capitals is clinical shorthand, not a name. Only
            # the strong labels ("report of patient", "patient name") qualify.
            "patient ECG NORMAL sinus rhythm",
            "Patient CT HEAD reported as NAD",
            "patient BP 130/80 HR 88",
        ],
    )
    def test_clinical_shorthand_is_not_mistaken_for_a_name(self, text):
        cleaned, _ = deidentify_clinical_text(text)
        assert cleaned == text

    @pytest.mark.parametrize(
        "label",
        ["report of patient", "patient name:", "name of patient"],
    )
    def test_each_strong_label_is_recognised(self, label):
        cleaned, _ = deidentify_clinical_text(f"{label} JOHN SMITH")
        assert "SMITH" not in cleaned


class TestDraftFieldSweep:
    def test_short_non_narrative_fields_are_swept(self):
        """The leak was in a title: not a narrative field, and under the
        humanizer's 20-character floor."""
        fields, labels = deidentify_draft_fields(
            {
                "reflection_title": "Review of Dr Saba Hussain case",
                "event_type": "ED patient",
                "date_of_encounter": "18/06/2026",
            }
        )
        assert "Saba Hussain" not in fields["reflection_title"]
        assert "CLINICIAN_NAME" in labels
        # Untouched fields must stay byte-identical.
        assert fields["event_type"] == "ED patient"
        assert fields["date_of_encounter"] == "18/06/2026"

    def test_list_fields_are_swept_too(self):
        fields, _ = deidentify_draft_fields(
            {"curriculum_links": ["SLO2", "Discussed with Dr Saba Hussain"]}
        )
        assert "SLO2" in fields["curriculum_links"]
        assert not any("Saba Hussain" in item for item in fields["curriculum_links"])

    def test_a_clean_draft_is_returned_unchanged(self):
        original = {
            "reflection_title": "Echocardiogram Review",
            "reflection": "Mild concentric LVH with SWMA. I escalated to cardiology.",
            "learned": "I will escalate borderline studies earlier.",
        }
        fields, labels = deidentify_draft_fields(dict(original))
        assert fields == original
        assert labels == []

    def test_non_dict_input_is_tolerated(self):
        assert deidentify_draft_fields(None) == (None, [])
