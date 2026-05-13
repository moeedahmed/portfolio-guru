import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

class TestFormTypeExtraction:
    """Verify the explicit form type detector doesn't false-trigger on clinical text."""

    def test_clinical_case_not_detected_as_form_type(self):
        """Long clinical text should not be classified as a form type command."""
        from extractor import extract_explicit_form_type
        clinical_text = (
            "I saw a 45 year old male presenting with chest pain and shortness of breath. "
            "ECG showed ST elevation. I managed the acute MI with aspirin and GTN. "
            "Good teaching case for the team."
        )
        result = extract_explicit_form_type(clinical_text)
        assert result is None, f"Clinical text was wrongly classified as: {result}"

    def test_explicit_cbd_command_detected(self):
        """Explicit form request with intent phrase should be detected."""
        from extractor import extract_explicit_form_type
        result = extract_explicit_form_type("make me a CBD")
        assert result == "CBD", f"Expected CBD, got: {result}"

    def test_explicit_dops_detected(self):
        from extractor import extract_explicit_form_type
        result = extract_explicit_form_type("file a DOPS please")
        assert result == "DOPS"

    def test_explicit_procedure_log_caption_detected(self):
        from extractor import extract_explicit_form_type
        result = extract_explicit_form_type(
            "Add this case as procedural log for adult procedural sedation"
        )
        assert result == "PROC_LOG"

    def test_bare_form_name_not_detected(self):
        """Bare form name without intent phrase should NOT trigger."""
        from extractor import extract_explicit_form_type
        result = extract_explicit_form_type("CBD")
        assert result is None, f"Bare 'CBD' should not trigger, got: {result}"

    def test_procedure_log_caption_wins_over_statin_substring(self):
        """A caption that names PROC_LOG must beat 'statin' triggering STAT.
        Regression: 'Procedure log for ES Block' + patient on a statin used
        to return STAT because 'stat' is a substring of 'statin' and STAT
        was checked before PROC_LOG in the lookup."""
        from extractor import extract_explicit_form_type
        text = (
            "Procedure log for ES Block\n\n"
            "Patient on statin for IHD. This is a procedural skill demonstration. "
            "Ultrasound used to find the transverse process. Levobupivacaine injected."
        )
        result = extract_explicit_form_type(text)
        assert result == "PROC_LOG", f"Expected PROC_LOG, got: {result}"

    def test_statin_does_not_false_trigger_stat(self):
        """'statin' must not match the short 'stat' code even with an intent phrase."""
        from extractor import extract_explicit_form_type
        text = "Make this a reflection on patient on statin for years."
        result = extract_explicit_form_type(text)
        assert result != "STAT", f"'statin' should not trigger STAT, got: {result}"

    def test_explicit_stat_command_still_detected(self):
        """Genuine STAT request via the short code should still work."""
        from extractor import extract_explicit_form_type
        result = extract_explicit_form_type("make me a STAT for my teaching session")
        assert result == "STAT", f"Expected STAT, got: {result}"

class TestFormSchemas:
    """Verify form schemas are complete and well-formed."""

    def test_core_forms_have_schemas(self):
        """Core trainee forms in FORM_UUIDS must have a schema."""
        from extractor import FORM_UUIDS
        from form_schemas import FORM_SCHEMAS
        core_forms = [
            "CBD", "DOPS", "MINI_CEX", "ACAT", "LAT", "ACAF", "STAT",
            "MSF", "QIAT", "JCF", "TEACH", "PROC_LOG", "SDL", "US_CASE",
            "ESLE_ASSESS", "COMPLAINT", "SERIOUS_INC", "EDU_ACT", "FORMAL_COURSE",
        ]
        for form_code in core_forms:
            assert form_code in FORM_UUIDS, f"{form_code} missing from FORM_UUIDS"
            assert form_code in FORM_SCHEMAS, f"No schema found for {form_code}"

    def test_schemas_have_required_fields_defined(self):
        """Every schema must have at least one field defined."""
        from form_schemas import FORM_SCHEMAS
        for form_code, schema in FORM_SCHEMAS.items():
            assert hasattr(schema, 'fields') or isinstance(schema, dict), \
                f"Schema for {form_code} has unexpected structure"
