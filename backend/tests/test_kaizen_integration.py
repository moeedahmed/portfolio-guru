"""
Kaizen integration tests — real Playwright against live Kaizen.
Marked @pytest.mark.kaizen — never runs in CI.

Run manually:
    KAIZEN_LIVE_TESTS=1 pytest tests/test_kaizen_integration.py -v -m kaizen -s

Safety contract:
  - These tests write real private drafts to Kaizen.
  - Default pytest runs exclude them via pytest.ini.
  - The explicit KAIZEN_LIVE_TESTS=1 gate is required even when credentials exist.
  - Draft text is visibly prefixed so test artefacts can be identified and deleted safely.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from kaizen_form_filer import file_to_kaizen


TEST_PREFIX = "INTEGRATION TEST — DO NOT USE — "


def _get_kaizen_credentials():
    """Get Kaizen credentials only after an explicit live-write opt-in."""
    if os.environ.get("KAIZEN_LIVE_TESTS") != "1":
        pytest.skip("Live Kaizen write tests require KAIZEN_LIVE_TESTS=1")
    username = os.environ.get("KAIZEN_USERNAME")
    password = os.environ.get("KAIZEN_PASSWORD")
    if not username or not password:
        pytest.skip("KAIZEN_USERNAME and KAIZEN_PASSWORD must be set")
    return username, password


CLEANUP_MSG = (
    "\n\n⚠️  Live Kaizen integration test complete.\n"
    "     Delete the private draft labelled 'INTEGRATION TEST — DO NOT USE' once verified.\n"
    "     URL: https://kaizenep.com/activities (Saved drafts)\n"
)


@pytest.mark.kaizen
@pytest.mark.asyncio
class TestKaizenIntegration:

    async def test_cbd_files_and_appears_in_kaizen(self):
        username, password = _get_kaizen_credentials()
        fields = {
            "date_of_encounter": "2026-03-21",
            "date_of_event": "2026-03-21",
            "stage_of_training": "Higher",
            "clinical_reasoning": (
                TEST_PREFIX
                + "72yo male presenting with chest pain. ECG showed ST elevation in leads II, III, aVF. "
                "Activated primary PCI pathway. Discussed with cardiology on-call."
            ),
            "reflection": (
                TEST_PREFIX
                + "This case reinforced the importance of rapid ECG interpretation and early activation "
                "of the PCI pathway. I felt confident in my initial assessment but need to improve "
                "my communication with the cath lab team."
            ),
        }
        result = await file_to_kaizen(
            "CBD", fields, username, password,
            curriculum_links=["SLO1"],
        )
        assert result["status"] in ("success", "partial"), f"CBD filing failed: {result}"
        assert len(result["filled"]) >= 3, f"Expected >=3 filled fields, got {result['filled']}"
        print(CLEANUP_MSG)

    async def test_reflect_log_files_and_appears_in_kaizen(self):
        username, password = _get_kaizen_credentials()
        fields = {
            "date_of_encounter": "2026-03-21",
            "reflection_title": TEST_PREFIX + "night shift cardiac arrest",
            "date_of_event": "2026-03-20",
            "reflection": (
                TEST_PREFIX
                + "I was the team leader for a cardiac arrest in resus. The patient was a 65yo "
                "female who collapsed in the waiting room. PEA arrest on arrival."
            ),
            "replay_differently": (
                "I would have delegated the airway management sooner rather than attempting "
                "it myself while also leading the team."
            ),
            "why": "I was trying to do too many things simultaneously under pressure.",
            "different_outcome": (
                "Earlier delegation would have allowed me to focus on reversible causes "
                "and overall team coordination."
            ),
            "focussing_on": "Team leadership and delegation during cardiac arrest.",
            "learned": (
                "Effective team leadership requires stepping back from procedures and "
                "focusing on coordination and decision-making."
            ),
        }
        result = await file_to_kaizen("REFLECT_LOG", fields, username, password)
        assert result["status"] in ("success", "partial"), f"REFLECT_LOG filing failed: {result}"
        assert result["status"] != "failed", "REFLECT_LOG must not fail — this was the original bug"
        assert "reflection" in result["filled"] or "reflection_title" in result["filled"], (
            f"Expected at least reflection or reflection_title filled, got {result['filled']}"
        )
        print(CLEANUP_MSG)

    async def test_dops_files_correctly(self):
        username, password = _get_kaizen_credentials()
        fields = {
            "date_of_encounter": "2026-03-21",
            "procedure_name": TEST_PREFIX + "Chest drain insertion — Seldinger technique",
            "stage_of_training": "Higher",
            "reflection": (
                TEST_PREFIX
                + "Successfully inserted a chest drain for a large pneumothorax. "
                "Used ultrasound to confirm the safe triangle."
            ),
        }
        result = await file_to_kaizen("DOPS", fields, username, password)
        assert result["status"] in ("success", "partial"), f"DOPS filing failed: {result}"
        assert result["status"] != "failed"
        print(CLEANUP_MSG)

    async def test_mini_cex_files_correctly(self):
        username, password = _get_kaizen_credentials()
        fields = {
            "date_of_encounter": "2026-03-21",
            "clinical_setting": "Emergency Department",
            "patient_presentation": (
                TEST_PREFIX
                + "45yo female presenting with acute abdominal pain. Systematic history "
                "and examination leading to diagnosis of acute appendicitis."
            ),
            "stage_of_training": "Higher",
            "reflection": (
                TEST_PREFIX
                + "This case demonstrated good systematic approach to the acute abdomen. "
                "I need to improve my ultrasound skills for appendicitis assessment."
            ),
        }
        result = await file_to_kaizen("MINI_CEX", fields, username, password)
        assert result["status"] in ("success", "partial"), f"MINI_CEX filing failed: {result}"
        assert result["status"] != "failed"
        print(CLEANUP_MSG)
