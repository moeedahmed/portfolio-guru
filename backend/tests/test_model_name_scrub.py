"""Model-based name scrubbing: the flag, the failure mode, and the guarantees.

The deterministic rules in `privacy_guard` cannot see an unlabelled name
("chatted to Sarah about it"). A local sidecar (`services/phi-name`) supplies
those, gated behind `PG_ENABLE_MODEL_NAME_SCRUB`.

No test here loads the model. The model's accuracy was measured separately
against `tests/fixtures/phi_evalset.py`; what needs guarding in CI is the
*wiring*: that the flag really gates it, that a dead service degrades instead of
breaking drafting, and that the degradation is visible to the doctor.
"""

import asyncio
from unittest.mock import patch

import pytest

import extractor
import privacy_guard


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    monkeypatch.delenv("PG_ENABLE_MODEL_NAME_SCRUB", raising=False)


class TestFlagGating:
    def test_disabled_by_default(self):
        """Matches the PG_ENABLE_BROWSER_USE_FALLBACK opt-in precedent."""
        assert privacy_guard.model_name_scrub_enabled() is False

    def test_disabled_means_the_service_is_never_called(self):
        with patch("privacy_guard.urllib.request.urlopen") as urlopen:
            names, available = privacy_guard.model_person_names("chatted to Sarah")
        urlopen.assert_not_called()
        assert (names, available) == ([], True)

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "ON"])
    def test_recognised_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("PG_ENABLE_MODEL_NAME_SCRUB", value)
        assert privacy_guard.model_name_scrub_enabled() is True


class TestServiceFailureDegrades:
    """A privacy helper must never be why a doctor cannot file."""

    @pytest.mark.parametrize(
        "boom",
        [ConnectionRefusedError("down"), TimeoutError("slow"), ValueError("garbage")],
    )
    def test_every_failure_shape_returns_unavailable(self, monkeypatch, boom):
        monkeypatch.setenv("PG_ENABLE_MODEL_NAME_SCRUB", "1")
        with patch("privacy_guard.urllib.request.urlopen", side_effect=boom):
            names, available = privacy_guard.model_person_names("chatted to Sarah")
        assert names == []
        assert available is False

    def test_unexpected_payload_shape_is_not_trusted(self, monkeypatch):
        monkeypatch.setenv("PG_ENABLE_MODEL_NAME_SCRUB", "1")

        class _Response:
            def read(self):
                return b'{"names": "Sarah"}'  # str, not list
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with patch("privacy_guard.urllib.request.urlopen", return_value=_Response()):
            names, available = privacy_guard.model_person_names("x")
        assert (names, available) == ([], False)


class TestNameReplacement:
    def test_names_are_replaced_case_insensitively(self):
        out, labels = privacy_guard.apply_model_names(
            "chatted to SARAH and later to Sarah again", ["Sarah"]
        )
        assert "SARAH" not in out and "Sarah" not in out
        assert labels == ["PATIENT_NAME", "PATIENT_NAME"][: len(labels)]

    def test_absent_names_leave_the_text_byte_identical(self):
        original = "Mild concentric LVH with SWMA, poor acoustic windows"
        out, labels = privacy_guard.apply_model_names(original, ["Sarah", "Priya"])
        assert out == original
        assert labels == []


class TestExtractorIntegration:
    def _prepare(self, text):
        return asyncio.run(extractor._prepare_case_description_for_model_async(text))

    def test_flag_off_matches_the_deterministic_result_exactly(self):
        text = "chatted to Sarah about the case, NHS 943 476 5919"
        assert self._prepare(text) == extractor._prepare_case_description_for_model(text)

    def test_names_from_the_service_are_removed(self, monkeypatch):
        monkeypatch.setenv("PG_ENABLE_MODEL_NAME_SCRUB", "1")
        # Patch on `extractor`: it imported the symbol directly, so patching
        # privacy_guard's attribute would not be seen here.
        with patch.object(
            extractor, "model_person_names", return_value=(["Sarah"], True)
        ):
            out = self._prepare("chatted to Sarah about the case afterwards")
        assert "Sarah" not in out

    def test_dead_service_still_returns_usable_text(self, monkeypatch):
        monkeypatch.setenv("PG_ENABLE_MODEL_NAME_SCRUB", "1")
        text = "chatted to Sarah about the case, NHS 943 476 5919"
        with patch.object(extractor, "model_person_names", return_value=([], False)):
            out = self._prepare(text)
        # Drafting continues, and the deterministic rules still did their job.
        assert "943" not in out


class TestFailureCounterReachesTheCaller:
    """The mechanism that carries "cover was reduced" out of the extractor.

    A ContextVar cannot: `asyncio.wait_for` runs the extractor inside a Task,
    and a Task gets a *copy* of the context, so writes never reach the caller.
    This test is the guard against that regression.
    """

    def test_a_failed_call_increments_the_counter(self, monkeypatch):
        monkeypatch.setenv("PG_ENABLE_MODEL_NAME_SCRUB", "1")
        before = privacy_guard.service_failure_count()
        with patch(
            "privacy_guard.urllib.request.urlopen",
            side_effect=ConnectionRefusedError("down"),
        ):
            privacy_guard.model_person_names("chatted to Sarah")
        assert privacy_guard.service_failure_count() == before + 1

    def test_the_increment_survives_a_wait_for_boundary(self, monkeypatch):
        monkeypatch.setenv("PG_ENABLE_MODEL_NAME_SCRUB", "1")

        async def scenario():
            before = privacy_guard.service_failure_count()
            with patch(
                "privacy_guard.urllib.request.urlopen",
                side_effect=ConnectionRefusedError("down"),
            ):
                await asyncio.wait_for(
                    extractor._prepare_case_description_for_model_async("chatted to Sarah"),
                    timeout=5,
                )
            return privacy_guard.service_failure_count() > before

        assert asyncio.run(scenario()) is True


class TestDegradationIsVisible:
    def test_preview_says_so_when_cover_was_reduced(self):
        from bot import _format_draft_preview
        from models import FormDraft

        draft = FormDraft(form_type="REFLECT_LOG", fields={"reflection": "Mild LVH."})
        degraded = _format_draft_preview(draft, name_check_degraded=True)
        normal = _format_draft_preview(draft, name_check_degraded=False)

        assert "reduced cover" in degraded
        assert "double-check names" in degraded
        assert "reduced cover" not in normal

    def test_note_is_suppressed_when_the_safety_layer_is_off(self):
        """LLM-facing renders must not inherit user-facing chrome."""
        from bot import _format_draft_preview
        from models import FormDraft

        draft = FormDraft(form_type="REFLECT_LOG", fields={"reflection": "Mild LVH."})
        out = _format_draft_preview(
            draft, include_safety_layer=False, name_check_degraded=True
        )
        assert "reduced cover" not in out
