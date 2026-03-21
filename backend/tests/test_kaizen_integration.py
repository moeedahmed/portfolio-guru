"""
Kaizen integration tests — real Playwright against live Kaizen.
Marked @pytest.mark.kaizen — never runs in CI.

Run manually:
    pytest tests/test_kaizen_integration.py -v -m kaizen -s
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from kaizen_filer import file_to_kaizen


def _get_kaizen_credentials():
    """Get Kaizen credentials from environment (loaded by run_local.sh or config.py)."""
    username = os.environ.get("KAIZEN_USERNAME")
    password = os.environ.get("KAIZEN_PASSWORD")
    if not username or not password:
        pytest.skip("KAIZEN_USERNAME and KAIZEN_PASSWORD must be set")
    return username, password


CLEANUP_MSG = (
    "\n\n⚠️  Integration test complete — check Kaizen and delete the test draft manually.\n"
    "     URL: https://kaizenep.com/events (look for today's date)\n"
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
                "72yo male presenting with chest pain. ECG showed ST elevation in leads II, III, aVF. "
                "Activated primary PCI pathway. Discussed with cardiology on-call."
            ),
            "reflection": (
                "This case reinforced the importance of rapid ECG interpretation and early activation "
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
            "reflection_title": "Integration test — night shift cardiac arrest",
            "date_of_event": "2026-03-20",
            "reflection": (
                "I was the team leader for a cardiac arrest in resus. The patient was a 65yo "
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
            "procedure_name": "Chest drain insertion — Seldinger technique",
            "stage_of_training": "Higher",
            "reflection": (
                "Successfully inserted a chest drain for a large pneumothorax. "
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
                "45yo female presenting with acute abdominal pain. Systematic history "
                "and examination leading to diagnosis of acute appendicitis."
            ),
            "stage_of_training": "Higher",
            "reflection": (
                "This case demonstrated good systematic approach to the acute abdomen. "
                "I need to improve my ultrasound skills for appendicitis assessment."
            ),
        }
        result = await file_to_kaizen("MINI_CEX", fields, username, password)
        assert result["status"] in ("success", "partial"), f"MINI_CEX filing failed: {result}"
        assert result["status"] != "failed"
        print(CLEANUP_MSG)
