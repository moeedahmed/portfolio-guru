"""
Router contract tests — verifies deterministic vs browser-use routing,
coverage-based escalation, and fallback behaviour.

No live browser or network needed.
"""
import pytest
import sys
import os
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from filer_router import route_filing, PLATFORM_REGISTRY, _get_kaizen_uuids

# Force-import filing_coverage so it's in sys.modules before route_filing's
# lazy `from filing_coverage import ...` runs
import filing_coverage


# ─── Section A: Platform registry contracts ───────────────────��───────────────

class TestPlatformRegistry:

    def test_kaizen_is_registered(self):
        assert "kaizen" in PLATFORM_REGISTRY

    def test_kaizen_is_deterministic(self):
        assert PLATFORM_REGISTRY["kaizen"]["deterministic"] is True

    def test_kaizen_has_supported_forms(self):
        forms = PLATFORM_REGISTRY["kaizen"]["supported_forms"]
        assert len(forms) >= 20
        for expected in ("CBD", "DOPS", "MINI_CEX", "PROC_LOG", "TEACH", "US_CASE"):
            assert expected in forms, f"{expected} not in Kaizen supported_forms"

    def test_kaizen_has_form_url_pattern(self):
        pattern = PLATFORM_REGISTRY["kaizen"]["form_url_pattern"]
        assert "{uuid}" in pattern
        assert "kaizenep.com" in pattern

    def test_horus_is_not_deterministic(self):
        assert PLATFORM_REGISTRY["horus"]["deterministic"] is False

    def test_soar_is_not_deterministic(self):
        assert PLATFORM_REGISTRY["soar"]["deterministic"] is False


# ─── Section B: Routing decisions ──────────────��──────────────────────────────

class TestRoutingDecisions:

    @pytest.mark.asyncio
    async def test_unknown_platform_returns_failed(self):
        """Unknown platform with no URL returns failed."""
        with patch.object(filing_coverage, "should_use_browser_use", return_value=False), \
             patch.object(filing_coverage, "record_run"):
            result = await route_filing(
                platform="unknown_platform",
                form_type="CBD",
                fields={"clinical_reasoning": "test"},
                credentials={"username": "u", "password": "p"},
            )
            assert result["status"] == "failed"
            assert "Unknown platform" in result["error"]

    @pytest.mark.asyncio
    async def test_deterministic_form_routes_to_playwright(self):
        """A mapped Kaizen form should route to deterministic filer."""
        mock_result = {
            "status": "success", "filled": ["clinical_reasoning"],
            "skipped": [], "error": None, "method": "deterministic",
        }
        with patch.object(filing_coverage, "should_use_browser_use", return_value=False), \
             patch.object(filing_coverage, "record_run"), \
             patch("filer_router._route_deterministic", new_callable=AsyncMock, return_value=mock_result) as mock_det:
            result = await route_filing(
                platform="kaizen",
                form_type="CBD",
                fields={"clinical_reasoning": "test"},
                credentials={"username": "u", "password": "p"},
            )
            mock_det.assert_called_once()
            assert result["method"] == "deterministic"
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_coverage_escalation_routes_to_browser_use(self):
        """When should_use_browser_use returns True, skip deterministic."""
        mock_result = {
            "status": "success", "filled": ["clinical_reasoning"],
            "skipped": [], "error": None, "method": "browser-use",
        }
        with patch.object(filing_coverage, "should_use_browser_use", return_value=True), \
             patch.object(filing_coverage, "record_run"), \
             patch("filer_router._route_browser_use", new_callable=AsyncMock, return_value=mock_result) as mock_bu:
            result = await route_filing(
                platform="kaizen",
                form_type="CBD",
                fields={"clinical_reasoning": "test"},
                credentials={"username": "u", "password": "p"},
            )
            mock_bu.assert_called_once()

    @pytest.mark.asyncio
    async def test_partial_with_important_skipped_escalates(self):
        """Deterministic partial with curriculum_links skipped should escalate."""
        det_result = {
            "status": "partial", "filled": ["clinical_reasoning"],
            "skipped": ["curriculum_links"], "error": None,
        }
        bu_result = {
            "status": "success", "filled": ["clinical_reasoning", "curriculum_links"],
            "skipped": [], "error": None, "method": "browser-use",
        }
        with patch.object(filing_coverage, "should_use_browser_use", return_value=False), \
             patch.object(filing_coverage, "record_run"), \
             patch("filer_router._route_deterministic", new_callable=AsyncMock, return_value=det_result), \
             patch("filer_router._route_browser_use", new_callable=AsyncMock, return_value=bu_result) as mock_bu:
            result = await route_filing(
                platform="kaizen",
                form_type="CBD",
                fields={"clinical_reasoning": "test"},
                credentials={"username": "u", "password": "p"},
                curriculum_links=["SLO1"],
            )
            mock_bu.assert_called_once()

    @pytest.mark.asyncio
    async def test_p0_schema_field_skipped_escalates(self):
        """Deterministic partial with P0 schema-critical fields skipped should escalate."""
        det_result = {
            "status": "partial", "filled": ["clinical_reasoning", "reflection"],
            "skipped": ["clinical_setting"], "error": None,
        }
        bu_result = {
            "status": "success", "filled": ["clinical_reasoning", "reflection", "clinical_setting"],
            "skipped": [], "error": None, "method": "browser-use",
        }
        with patch.object(filing_coverage, "should_use_browser_use", return_value=False), \
             patch.object(filing_coverage, "record_run"), \
             patch("filer_router._route_deterministic", new_callable=AsyncMock, return_value=det_result), \
             patch("filer_router._route_browser_use", new_callable=AsyncMock, return_value=bu_result) as mock_bu:
            result = await route_filing(
                platform="kaizen",
                form_type="CBD",
                fields={"clinical_reasoning": "test", "clinical_setting": "ED"},
                credentials={"username": "u", "password": "p"},
            )
            mock_bu.assert_called_once()
            assert result["method"] == "browser-use"

    @pytest.mark.asyncio
    async def test_partial_without_important_skipped_does_not_escalate(self):
        """Deterministic partial with non-important fields skipped should NOT escalate."""
        det_result = {
            "status": "partial", "filled": ["clinical_reasoning", "reflection"],
            "skipped": ["some_optional_field"], "error": None, "method": "deterministic",
        }
        with patch.object(filing_coverage, "should_use_browser_use", return_value=False), \
             patch.object(filing_coverage, "record_run"), \
             patch("filer_router._route_deterministic", new_callable=AsyncMock, return_value=det_result), \
             patch("filer_router._route_browser_use", new_callable=AsyncMock) as mock_bu:
            result = await route_filing(
                platform="kaizen",
                form_type="CBD",
                fields={"clinical_reasoning": "test"},
                credentials={"username": "u", "password": "p"},
            )
            mock_bu.assert_not_called()
            assert result["status"] == "partial"
            assert result["method"] == "deterministic"


# ─── Section C: Submit safety gate in router ──────────────────────────────────

class TestRouterSafetyGate:

    @pytest.mark.asyncio
    async def test_submit_blocked_without_env_flag(self, monkeypatch):
        """submit=True at router level must be blocked unless KAIZEN_ALLOW_SUBMIT set."""
        monkeypatch.delenv("KAIZEN_ALLOW_SUBMIT", raising=False)
        mock_result = {
            "status": "success", "filled": ["field1"],
            "skipped": [], "error": None,
        }
        with patch.object(filing_coverage, "should_use_browser_use", return_value=False), \
             patch.object(filing_coverage, "record_run"), \
             patch("filer_router._route_deterministic", new_callable=AsyncMock, return_value=mock_result) as mock_det:
            result = await route_filing(
                platform="kaizen",
                form_type="CBD",
                fields={"clinical_reasoning": "test"},
                credentials={"username": "u", "password": "p"},
                submit=True,
            )
            # The submit=False should have been forced by the safety gate
            call_kwargs = mock_det.call_args
            # _route_deterministic(platform, form_type, fields, credentials, curriculum_links, submit=...)
            if call_kwargs[1]:
                assert call_kwargs[1].get("submit", True) is False
            else:
                # positional: submit is the 6th arg (index 5)
                assert call_kwargs[0][5] is False


# ─── Section D: Coverage tracker contracts ──────────────���─────────────────────

class TestCoverageTracker:

    def test_should_use_browser_use_for_unmapped_form(self):
        result = filing_coverage.should_use_browser_use("TOTALLY_UNMAPPED_FORM_TYPE")
        assert result is True

    def test_should_not_use_browser_use_returns_bool(self):
        result = filing_coverage.should_use_browser_use("CBD")
        assert isinstance(result, bool)

    def test_p0_fields_are_important_for_escalation(self):
        for field in (
            "clinical_setting", "patient_presentation", "trainee_role",
            "level_of_supervision", "procedure_name", "indication",
            "trainee_performance", "stage_of_training",
        ):
            assert field in filing_coverage.IMPORTANT_FIELDS

    def test_record_run_doesnt_crash(self, tmp_path, monkeypatch):
        """record_run should not crash even with empty coverage file."""
        monkeypatch.setattr(filing_coverage, "COVERAGE_PATH", tmp_path / "test_coverage.json")
        filing_coverage.record_run("TEST_FORM", "deterministic", ["field1"], ["field2"])
