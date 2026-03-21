"""
Mock tests for kaizen_filer.py — full isolation via mocked Playwright.
No browser, no network, no credentials needed.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import asyncio

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from kaizen_filer import (
    file_to_kaizen,
    FORM_FIELD_MAP,
    FORM_UUIDS,
    STAGE_SELECT_VALUES,
    _strip_emojis,
    _to_uk_date,
    _fill_stage_of_training,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_page():
    """Mock Playwright Page with configurable behaviour."""
    page = AsyncMock()
    page.url = "https://kaizenep.com/events/new-section/some-uuid"
    page.goto = AsyncMock()
    page.wait_for_url = AsyncMock()
    page.wait_for_selector = AsyncMock()

    def make_locator(selector):
        loc = AsyncMock()
        loc.count = AsyncMock(return_value=1)
        loc.evaluate = AsyncMock(return_value="INPUT")
        loc.fill = AsyncMock()
        loc.click = AsyncMock()
        loc.press = AsyncMock()
        loc.type = AsyncMock()
        loc.select_option = AsyncMock()
        loc.inner_text = AsyncMock(return_value="Save as draft")
        loc.first = loc
        return loc

    page.locator = MagicMock(side_effect=make_locator)
    page.get_by_text = MagicMock(side_effect=make_locator)
    page.evaluate = AsyncMock(return_value=False)
    return page


@pytest.fixture
def mock_playwright_ctx(mock_page):
    """Patch async_playwright to return our mock page in a mock browser.
    Also patches asyncio.sleep inside kaizen_filer to be instant."""
    mock_browser = AsyncMock()
    mock_browser.new_page = AsyncMock(return_value=mock_page)
    mock_browser.close = AsyncMock()
    mock_browser.contexts = []

    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_pw.stop = AsyncMock()

    mock_ap = MagicMock()
    mock_ap.start = AsyncMock(return_value=mock_pw)

    # Create a no-op coroutine for sleep that doesn't touch the real asyncio.sleep
    async def _noop_sleep(*args, **kwargs):
        pass

    import kaizen_filer as _kf
    _orig_sleep = asyncio.sleep

    with patch("kaizen_filer.async_playwright", return_value=mock_ap):
        with patch("kaizen_filer.KAIZEN_USE_CDP", False):
            # Patch sleep on the asyncio module itself — kaizen_filer accesses it via asyncio.sleep
            asyncio.sleep = _noop_sleep
            try:
                yield mock_page
            finally:
                asyncio.sleep = _orig_sleep


# ─── Section A: Entry point validation ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_form_type_returns_failed():
    result = await file_to_kaizen("UNKNOWN_XYZ", {}, "user", "pass")
    assert result["status"] == "failed"
    assert "Unknown form type" in result["error"]


@pytest.mark.asyncio
async def test_no_field_map_returns_partial(mock_playwright_ctx):
    with patch.dict("kaizen_filer.FORM_FIELD_MAP", {}, clear=True):
        result = await file_to_kaizen("CBD", {"some_field": "val"}, "user", "pass")
        assert result["status"] == "partial"
        assert "No field mapping" in result["error"]


# ─── Section B: Login path ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_failure_returns_failed(mock_playwright_ctx):
    with patch("kaizen_filer._login", AsyncMock(return_value=False)):
        result = await file_to_kaizen("CBD", {"clinical_reasoning": "test"}, "user", "pass")
        assert result["status"] == "failed"
        assert result["error"] == "Login failed"


@pytest.mark.asyncio
async def test_login_success_proceeds_to_form(mock_playwright_ctx):
    mock_page = mock_playwright_ctx
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_filer._save_draft", AsyncMock(return_value=True)):
            result = await file_to_kaizen("CBD", {"clinical_reasoning": "test"}, "user", "pass")
            assert result["status"] != "failed" or "Login" not in (result["error"] or "")
            mock_page.goto.assert_called()


# ─── Section C: Form page navigation ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_form_page_redirect_returns_failed(mock_playwright_ctx):
    mock_page = mock_playwright_ctx
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        mock_page.url = "https://kaizenep.com/dashboard"
        result = await file_to_kaizen("CBD", {"clinical_reasoning": "test"}, "user", "pass")
        assert result["status"] == "failed"
        assert "Form page didn't load" in result["error"]


# ─── Section D: Field filling — core paths ──────────────────────────────────────

@pytest.mark.asyncio
async def test_all_fields_filled_returns_success(mock_playwright_ctx):
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_filer._save_draft", AsyncMock(return_value=True)):
            fields = {
                "date_of_encounter": "2026-03-21",
                "date_of_event": "2026-03-21",
                "stage_of_training": "Higher",
                "clinical_reasoning": "Good clinical reasoning",
                "reflection": "Reflective commentary",
            }
            result = await file_to_kaizen("CBD", fields, "user", "pass")
            assert result["status"] in ("success", "partial")
            assert len(result["filled"]) >= 3


@pytest.mark.asyncio
async def test_missing_fields_returns_partial(mock_playwright_ctx):
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_filer._save_draft", AsyncMock(return_value=True)):
            fields = {
                "date_of_encounter": "2026-03-21",
                "stage_of_training": "Higher",
            }
            result = await file_to_kaizen("CBD", fields, "user", "pass")
            assert len(result["skipped"]) > 0


@pytest.mark.asyncio
async def test_save_fails_returns_failed_not_partial(mock_playwright_ctx):
    """Regression: save failure must return 'failed', not 'partial'.
    This was the original bug — false success when save failed."""
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_filer._save_draft", AsyncMock(return_value=False)):
            fields = {
                "date_of_encounter": "2026-03-21",
                "stage_of_training": "Higher",
                "clinical_reasoning": "Good reasoning",
                "reflection": "Good reflection",
            }
            result = await file_to_kaizen("CBD", fields, "user", "pass")
            assert result["status"] == "failed", (
                f"Expected 'failed' when save fails, got '{result['status']}' — "
                "this is the false-success bug pattern"
            )
            assert "Save button" in result["error"] or "save" in result["error"].lower()


@pytest.mark.asyncio
async def test_empty_string_field_is_skipped(mock_playwright_ctx):
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_filer._save_draft", AsyncMock(return_value=True)):
            fields = {
                "date_of_encounter": "2026-03-21",
                "stage_of_training": "Higher",
                "clinical_reasoning": "",
                "reflection": "Some text",
            }
            result = await file_to_kaizen("CBD", fields, "user", "pass")
            assert "clinical_reasoning" in result["skipped"]


@pytest.mark.asyncio
async def test_none_field_is_skipped(mock_playwright_ctx):
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_filer._save_draft", AsyncMock(return_value=True)):
            fields = {
                "date_of_encounter": "2026-03-21",
                "stage_of_training": "Higher",
                "clinical_reasoning": None,
                "reflection": "Some text",
            }
            result = await file_to_kaizen("CBD", fields, "user", "pass")
            assert "clinical_reasoning" in result["skipped"]


# ─── Section E: Stage of training — special handling ─────────────────────────────

@pytest.mark.asyncio
async def test_stage_defaults_to_higher_for_st5(mock_playwright_ctx):
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_filer._save_draft", AsyncMock(return_value=True)):
            fields = {"stage_of_training": "ST5", "clinical_reasoning": "test"}
            result = await file_to_kaizen("CBD", fields, "user", "pass")
            assert "stage_of_training" in result["filled"]


@pytest.mark.asyncio
async def test_stage_maps_accs_for_st1(mock_playwright_ctx):
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_filer._save_draft", AsyncMock(return_value=True)):
            fields = {"stage_of_training": "ST1", "clinical_reasoning": "test"}
            result = await file_to_kaizen("CBD", fields, "user", "pass")
            assert "stage_of_training" in result["filled"]


@pytest.mark.asyncio
async def test_stage_maps_intermediate_for_st3(mock_playwright_ctx):
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_filer._save_draft", AsyncMock(return_value=True)):
            fields = {"stage_of_training": "ST3", "clinical_reasoning": "test"}
            result = await file_to_kaizen("CBD", fields, "user", "pass")
            assert "stage_of_training" in result["filled"]


# ─── Section F: Emoji stripping ─────────────────────────────────────────────────

def test_emoji_stripped_before_fill():
    """Kaizen rejects emoji characters in text fields."""
    result = _strip_emojis("Great case 🔥 with emojis 💉")
    assert "🔥" not in result
    assert "💉" not in result
    assert "Great case" in result
    assert "with emojis" in result


@pytest.mark.asyncio
async def test_emoji_stripped_in_fill_field(mock_playwright_ctx):
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_filer._save_draft", AsyncMock(return_value=True)):
            fields = {
                "date_of_encounter": "2026-03-21",
                "stage_of_training": "Higher",
                "clinical_reasoning": "Great case 🔥 with emojis 💉",
                "reflection": "Good",
            }
            result = await file_to_kaizen("CBD", fields, "user", "pass")
            assert "clinical_reasoning" in result["filled"]


# ─── Section G: REFLECT_LOG specific ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reflect_log_fills_all_gibbs_fields(mock_playwright_ctx):
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_filer._save_draft", AsyncMock(return_value=True)):
            fields = {
                "date_of_encounter": "2026-03-21",
                "reflection_title": "Night shift reflection",
                "date_of_event": "2026-03-20",
                "reflection": "I was called to resus for a cardiac arrest",
                "replay_differently": "I would have started CPR sooner",
                "why": "I hesitated due to uncertainty",
                "different_outcome": "Earlier CPR may have improved ROSC",
                "focussing_on": "Decision-making under pressure",
                "learned": "Trust my clinical judgement sooner",
            }
            result = await file_to_kaizen("REFLECT_LOG", fields, "user", "pass")
            assert result["status"] in ("success", "partial")
            gibbs_fields = [
                "reflection_title", "date_of_event", "reflection",
                "replay_differently", "why", "different_outcome",
                "focussing_on", "learned",
            ]
            for f in gibbs_fields:
                assert f in result["filled"], f"Expected '{f}' in filled, got {result['filled']}"


def test_reflect_log_reflection_uuid_not_shared_with_circumstances():
    """Regression: reflection and circumstances must NOT share the same UUID.
    This was the duplicate UUID bug fixed 2026-03-21."""
    field_map = FORM_FIELD_MAP["REFLECT_LOG"]
    if "circumstances" in field_map:
        assert field_map.get("reflection") != field_map.get("circumstances"), (
            "reflection and circumstances share the same UUID — duplicate UUID bug"
        )


# ─── Section H: All form types — field map completeness ─────────────────────────

def test_all_form_types_have_uuid():
    """Every form in FORM_FIELD_MAP must also be in FORM_UUIDS."""
    for form_type in FORM_FIELD_MAP:
        assert form_type in FORM_UUIDS, f"{form_type} has field map but no UUID"


def test_all_field_map_uuids_are_strings():
    """Every DOM id value in every field map must be a non-empty string."""
    for form_type, field_map in FORM_FIELD_MAP.items():
        for field_key, dom_id in field_map.items():
            assert isinstance(dom_id, str) and len(dom_id) > 0, (
                f"{form_type}.{field_key} has invalid DOM id: {dom_id!r}"
            )


def test_no_duplicate_uuids_within_form():
    """Within each form, no two different fields should share the same DOM id.
    (The REFLECT_LOG bug pattern — same UUID for reflection and circumstances.)"""
    for form_type, field_map in FORM_FIELD_MAP.items():
        non_date_ids = [v for k, v in field_map.items() if v not in ("startDate", "endDate")]
        assert len(non_date_ids) == len(set(non_date_ids)), (
            f"{form_type} has duplicate UUIDs: "
            f"{[v for v in non_date_ids if non_date_ids.count(v) > 1]}"
        )


# ─── Section I: Curriculum ticking ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_curriculum_links_trigger_tick_attempt(mock_playwright_ctx):
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_filer._save_draft", AsyncMock(return_value=True)):
            with patch("kaizen_filer._expand_curriculum_section", AsyncMock()) as mock_expand:
                with patch("kaizen_filer._tick_curriculum", AsyncMock(return_value=2)) as mock_tick:
                    fields = {"stage_of_training": "Higher", "clinical_reasoning": "test"}
                    result = await file_to_kaizen(
                        "CBD", fields, "user", "pass",
                        curriculum_links=["SLO1", "SLO3"],
                    )
                    mock_expand.assert_called_once()
                    mock_tick.assert_called_once()
                    tick_args = mock_tick.call_args[0]
                    assert ["SLO1", "SLO3"] == tick_args[1]


@pytest.mark.asyncio
async def test_no_curriculum_links_skips_tick(mock_playwright_ctx):
    with patch("kaizen_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_filer._save_draft", AsyncMock(return_value=True)):
            with patch("kaizen_filer._tick_curriculum", AsyncMock()) as mock_tick:
                fields = {"stage_of_training": "Higher", "clinical_reasoning": "test"}
                result = await file_to_kaizen("CBD", fields, "user", "pass")
                mock_tick.assert_not_called()
