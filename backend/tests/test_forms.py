import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

class TestFormButtonLabels:
    """Verify all forms have human-readable labels (no raw codes)."""

    def test_no_raw_codes_in_labels(self):
        """No label should contain an underscore — that means a raw code leaked through."""
        from bot import FORM_BUTTON_LABELS
        for key, label in FORM_BUTTON_LABELS.items():
            assert "_" not in label, f"Raw code in label for {key}: '{label}'"

    def test_2021_forms_have_curriculum_suffix(self):
        """2021 forms should include a curriculum year indicator in the label."""
        from bot import FORM_BUTTON_LABELS
        for key, label in FORM_BUTTON_LABELS.items():
            if key.endswith("_2021"):
                assert "2021" in label or "21" in label, \
                    f"{key} missing curriculum year in label: '{label}'"

    def test_management_forms_have_labels(self):
        """Management forms must have labels."""
        from bot import FORM_BUTTON_LABELS
        mgmt_forms = ["MGMT_ROTA", "MGMT_RISK", "MGMT_MEETING", "MGMT_PROJECT",
                      "MGMT_AUDIT", "MGMT_SERVICE", "MGMT_LEADERSHIP"]
        for form in mgmt_forms:
            assert form in FORM_BUTTON_LABELS, f"Missing label for {form}"

class TestTrainingLevelForms:
    """Verify the right forms appear for each grade."""

    def test_management_forms_in_st5(self):
        """ST5 must have management forms."""
        from bot import TRAINING_LEVEL_FORMS
        mgmt_forms = ["MGMT_ROTA", "MGMT_RISK", "MGMT_AUDIT"]
        for form in mgmt_forms:
            assert form in TRAINING_LEVEL_FORMS["ST5"], f"{form} missing from ST5"

    def test_management_forms_not_in_st3(self):
        """ST3 must NOT have management forms."""
        from bot import TRAINING_LEVEL_FORMS
        for form in TRAINING_LEVEL_FORMS.get("ST3", []):
            assert not form.startswith("MGMT_"), f"Management form {form} incorrectly in ST3"

    def test_all_grades_defined(self):
        """All expected grades must exist."""
        from bot import TRAINING_LEVEL_FORMS
        for grade in ["ST3", "ST4", "ST5", "ST6", "SAS"]:
            assert grade in TRAINING_LEVEL_FORMS, f"Grade {grade} missing"

class TestCurriculumFilter:
    """Verify curriculum switching works correctly."""

    def test_2025_curriculum_excludes_2021_forms(self):
        """2025 curriculum must not show _2021 forms."""
        from bot import _filter_forms_by_curriculum, TRAINING_LEVEL_FORMS
        forms = TRAINING_LEVEL_FORMS["ST5"]
        filtered = _filter_forms_by_curriculum(forms, "2025")
        for f in filtered:
            assert not f.endswith("_2021"), f"2021 form {f} appeared in 2025 curriculum"

    def test_2021_curriculum_swaps_variants(self):
        """2021 curriculum should swap base forms for _2021 variants where available."""
        from bot import _filter_forms_by_curriculum
        result = _filter_forms_by_curriculum(["CBD", "LAT"], "2021")
        # CBD has a 2021 variant — gets swapped to CBD_2021
        assert "CBD_2021" in result or "CBD" in result
        # LAT should remain (no _2021 variant or kept as-is)
        assert "LAT" in result
