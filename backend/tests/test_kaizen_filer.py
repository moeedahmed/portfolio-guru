"""
Mock tests for kaizen_form_filer.py — full isolation via mocked Playwright.
No browser, no network, no credentials needed.
"""
import pytest

from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import asyncio

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from kaizen_form_filer import (
    file_to_kaizen,
    FORM_FIELD_MAP,
    FORM_UUIDS,
    STAGE_SELECT_VALUES,
    apply_common_header_defaults,
    _attach_file,
    _QA_READ_FIELD_JS,
    _QA_READ_KC_JS,
    _session_cache_path,
    _default_non_applicable_procedural_selects,
    _is_kaizen_app_url,
    _strip_emojis,
    _to_uk_date,
    _verify_fields,
    invalidate_session_cache,
    load_session_state,
    save_session_state,
    use_cached_session,
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
        if not isinstance(getattr(page, "_locators", None), dict):
            page._locators = {}
        page._locators[selector] = loc
        loc.count = AsyncMock(return_value=1)

        async def evaluate_mock(expr, *args):
            if "el.value" in expr:
                if getattr(loc, "_typed_val", None):
                    return loc._typed_val
                return "20/3/2026"
            if "tagName" in expr:
                return "INPUT"
            return "INPUT"
        loc.evaluate = evaluate_mock

        async def type_mock(text, **kwargs):
            loc._typed_val = text
        loc.type = type_mock

        loc.fill = AsyncMock()
        loc.click = AsyncMock()
        loc.press = AsyncMock()
        loc.select_option = AsyncMock()
        loc.inner_text = AsyncMock(return_value="Save as draft")
        loc.first = loc
        return loc

    page.locator = MagicMock(side_effect=make_locator)
    page.get_by_text = MagicMock(side_effect=make_locator)
    async def page_evaluate_mock(expr, *args):
        if expr == _QA_READ_FIELD_JS:
            return {"tag": "INPUT", "value": "mock persisted value"}
        if expr == _QA_READ_KC_JS:
            return True
        if isinstance(expr, str) and "add tags" in expr.lower():
            return 1
        if isinstance(expr, str) and "document.body" in expr:
            return "Stage of training Higher"
        return False

    page.evaluate = AsyncMock(side_effect=page_evaluate_mock)
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

    import kaizen_form_filer as _kf
    _orig_sleep = asyncio.sleep

    with patch("kaizen_form_filer.async_playwright", return_value=mock_ap):
        with patch("kaizen_form_filer.KAIZEN_USE_CDP", False):
            # Patch sleep on the asyncio module itself — kaizen_filer accesses it via asyncio.sleep
            asyncio.sleep = _noop_sleep
            try:
                yield mock_page
            finally:
                asyncio.sleep = _orig_sleep


# ─── Section A: Entry point validation ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_attach_file_uses_kaizen_upload_button_file_chooser(tmp_path, monkeypatch):
    attachment = tmp_path / "portfolio-guru-test.docx"
    attachment.write_text("synthetic attachment", encoding="utf-8")

    async def _noop_sleep(*args, **kwargs):
        pass

    monkeypatch.setattr("kaizen_form_filer.asyncio.sleep", _noop_sleep)

    upload_button = MagicMock()
    upload_button.is_visible = AsyncMock(return_value=True)
    upload_button.click = AsyncMock()

    upload_locator = MagicMock()
    upload_locator.first = upload_button

    chooser = MagicMock()
    chooser.set_files = AsyncMock()

    class ChooserInfo:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        @property
        def value(self):
            async def _value():
                return chooser

            return _value()

    confirmation = MagicMock()
    confirmation.is_visible = AsyncMock(return_value=True)
    confirmation.first = confirmation

    page = MagicMock()
    page.locator.return_value = upload_locator
    page.expect_file_chooser.return_value = ChooserInfo()
    page.get_by_text.return_value = confirmation

    assert await _attach_file(page, str(attachment)) is True
    upload_button.click.assert_awaited_once()
    chooser.set_files.assert_awaited_once_with(str(attachment))


@pytest.mark.asyncio
async def test_attach_file_without_visible_confirmation_returns_false(tmp_path, monkeypatch):
    """The upload click+chooser can fire without Kaizen actually accepting the
    file (e.g. wrong section, transient error). If nothing on the page shows
    the filename, an "Uploaded" status, or Remove/Replace controls, this must
    not be reported as a successful attachment."""
    attachment = tmp_path / "Moeed KH A Kind Life.pdf"
    attachment.write_text("synthetic attachment", encoding="utf-8")

    async def _noop_sleep(*args, **kwargs):
        pass

    monkeypatch.setattr("kaizen_form_filer.asyncio.sleep", _noop_sleep)

    upload_button = MagicMock()
    upload_button.is_visible = AsyncMock(return_value=True)
    upload_button.click = AsyncMock()

    upload_locator = MagicMock()
    upload_locator.first = upload_button

    chooser = MagicMock()
    chooser.set_files = AsyncMock()

    class ChooserInfo:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        @property
        def value(self):
            async def _value():
                return chooser

            return _value()

    no_confirmation = MagicMock()
    no_confirmation.is_visible = AsyncMock(return_value=False)
    no_confirmation.first = no_confirmation

    file_input = MagicMock()
    file_input.count = AsyncMock(return_value=0)

    page = MagicMock()
    page.locator.side_effect = lambda selector, *a, **kw: (
        file_input if selector == 'input[type="file"]' else upload_locator
    )
    page.expect_file_chooser.return_value = ChooserInfo()
    page.get_by_text.return_value = no_confirmation

    assert await _attach_file(page, str(attachment)) is False
    upload_button.click.assert_awaited_once()
    chooser.set_files.assert_awaited_once_with(str(attachment))


@pytest.mark.asyncio
async def test_unknown_form_type_returns_failed():
    result = await file_to_kaizen("UNKNOWN_XYZ", {}, "user", "pass")
    assert result["status"] == "failed"
    assert "Unknown form type" in result["error"]


@pytest.mark.asyncio
async def test_no_field_map_returns_partial(mock_playwright_ctx):
    with patch.dict("kaizen_form_filer.FORM_FIELD_MAP", {}, clear=True):
        result = await file_to_kaizen("CBD", {"some_field": "val"}, "user", "pass")
        assert result["status"] == "partial"
        assert "No field mapping" in result["error"]


# ─── Section B: Login path ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_failure_returns_failed(mock_playwright_ctx):
    with patch("kaizen_form_filer._login", AsyncMock(return_value=False)):
        result = await file_to_kaizen("CBD", {"clinical_reasoning": "test"}, "user", "pass")
        assert result["status"] == "failed"
        assert "log in" in result["error"].lower()


@pytest.mark.asyncio
async def test_login_success_proceeds_to_form(mock_playwright_ctx):
    mock_page = mock_playwright_ctx
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            result = await file_to_kaizen("CBD", {"clinical_reasoning": "test"}, "user", "pass")
            assert result["status"] != "failed" or "Login" not in (result["error"] or "")
            mock_page.goto.assert_called()


def test_auth_subdomain_is_not_kaizen_app_url():
    assert _is_kaizen_app_url("https://kaizenep.com/activities") is True
    assert _is_kaizen_app_url("https://auth.kaizenep.com/interaction/abc") is False


def test_minicex_maps_and_defaults_visible_date_of_event():
    field_map = FORM_FIELD_MAP["MINI_CEX"]
    assert field_map["date_of_event"] == "5391f8de-de63-4db3-9e08-baaa2a380cfe"

    fields, meta = apply_common_header_defaults(
        "MINI_CEX",
        {
            "clinical_setting": "Emergency Department",
            "patient_presentation": "Central chest pain with anterior ST elevation.",
            "reflection": "Repeat ECG earlier next time.",
        },
        field_map,
    )

    assert fields["date_of_encounter"]
    assert fields["end_date"] == fields["date_of_encounter"]
    assert fields["date_of_event"] == fields["date_of_encounter"]
    assert {"date_of_encounter", "end_date", "date_of_event"} <= set(meta["defaulted_fields"])


def test_esle_and_research_default_mapped_visible_date_fields():
    esle_map = FORM_FIELD_MAP["ESLE_PART1_2"]
    assert esle_map["date_of_esle"] == "2c86886b-0a18-4771-9b25-6c2272fdad6b"

    esle_fields, esle_meta = apply_common_header_defaults(
        "ESLE_ASSESS",
        {
            "stage_of_training": "Higher/ST4-ST6",
            "reflection": "Leadership and non-technical skills reviewed.",
        },
        esle_map,
    )

    assert esle_fields["date_of_encounter"]
    assert esle_fields["date_of_esle"] == esle_fields["date_of_encounter"]
    assert "date_of_esle" in esle_meta["defaulted_fields"]

    research_map = FORM_FIELD_MAP["RESEARCH"]
    assert research_map["date_started"] == "7fbf5f39-c9b1-4e2c-8c3d-455f159935fe"
    assert research_map["date_finished"] == "025d5d3f-363b-470d-9c74-7427a8b898fd"

    research_fields, research_meta = apply_common_header_defaults(
        "RESEARCH",
        {
            "title": "Poster presentation",
            "reflection": "Research activity completed and reflected on.",
        },
        research_map,
    )

    assert research_fields["date_of_encounter"]
    assert research_fields["date_started"] == research_fields["date_of_encounter"]
    assert research_fields["date_finished"] == research_fields["date_of_encounter"]
    assert {"date_started", "date_finished"} <= set(research_meta["defaulted_fields"])


@pytest.mark.asyncio
async def test_verify_fields_checks_mapped_visible_date_fields():
    page = AsyncMock()

    async def evaluate(script, dom_id):
        values = {
            FORM_FIELD_MAP["QIAT"]["date_of_completion"]: "1/1/2026",
            FORM_FIELD_MAP["TEACH"]["date_of_teaching_activity"]: "2/1/2026",
        }
        return values.get(dom_id)

    page.evaluate = AsyncMock(side_effect=evaluate)

    qiat_issues = await _verify_fields(
        page,
        "QIAT",
        {"date_of_completion": "2026-06-27"},
        FORM_FIELD_MAP["QIAT"],
        ["date_of_completion"],
    )
    teach_issues = await _verify_fields(
        page,
        "TEACH",
        {"date_of_teaching_activity": "2026-06-27"},
        FORM_FIELD_MAP["TEACH"],
        ["date_of_teaching_activity"],
    )

    assert any("date_of_completion" in issue for issue in qiat_issues)
    assert any("date_of_teaching_activity" in issue for issue in teach_issues)


@pytest.mark.asyncio
async def test_cached_session_rejects_auth_redirect(mock_playwright_ctx):
    mock_page = mock_playwright_ctx
    mock_page.url = "https://auth.kaizenep.com/interaction/abc"

    with patch("kaizen_form_filer.load_session_state", return_value={"cookies": []}):
        assert await use_cached_session(mock_page, 12345) is False


def test_session_cache_is_scoped_to_kaizen_username(monkeypatch, tmp_path):
    import kaizen_form_filer

    monkeypatch.setattr(kaizen_form_filer, "_SESSION_DIR", tmp_path)

    legacy = _session_cache_path(12345)
    moeed = _session_cache_path(12345, "moeed@example.com")
    haris = _session_cache_path(12345, "haris@example.com")

    assert legacy.name == "12345.encrypted"
    assert moeed != haris
    assert moeed.name.startswith("12345-")
    assert haris.name.startswith("12345-")


@pytest.mark.asyncio
async def test_cached_session_for_previous_kaizen_user_is_not_loaded(monkeypatch, tmp_path):
    import kaizen_form_filer
    import credentials
    from cryptography.fernet import Fernet

    monkeypatch.setattr(kaizen_form_filer, "_SESSION_DIR", tmp_path)
    monkeypatch.setattr(credentials, "FERNET_KEY", Fernet.generate_key())

    class FakeContext:
        async def storage_state(self):
            return {"cookies": [{"name": "sid", "value": "moeed", "domain": "kaizenep.com", "path": "/"}]}

    await save_session_state(FakeContext(), 12345, "moeed@example.com")

    assert load_session_state(12345, "moeed@example.com") is not None
    assert load_session_state(12345, "haris@example.com") is None


def test_invalidate_session_cache_removes_legacy_and_account_scoped_files(monkeypatch, tmp_path):
    import kaizen_form_filer

    monkeypatch.setattr(kaizen_form_filer, "_SESSION_DIR", tmp_path)

    paths = [
        _session_cache_path(12345),
        _session_cache_path(12345, "moeed@example.com"),
        _session_cache_path(12345, "haris@example.com"),
        _session_cache_path(67890, "other@example.com"),
    ]
    for path in paths:
        path.write_bytes(b"cached")

    removed = invalidate_session_cache(12345)

    assert removed == 3
    assert not _session_cache_path(12345).exists()
    assert not _session_cache_path(12345, "moeed@example.com").exists()
    assert not _session_cache_path(12345, "haris@example.com").exists()
    assert _session_cache_path(67890, "other@example.com").exists()


@pytest.mark.asyncio
async def test_cached_session_auth_redirect_reauthenticates_before_form_fill(mock_playwright_ctx):
    mock_page = mock_playwright_ctx
    urls = [
        "https://auth.kaizenep.com/interaction/abc",
        "https://kaizenep.com/events/new-section/some-uuid",
    ]

    async def _goto(url, *args, **kwargs):
        if "new-section" in url:
            mock_page.url = urls.pop(0)
        else:
            mock_page.url = url

    mock_page.goto = AsyncMock(side_effect=_goto)

    with patch("kaizen_form_filer.use_cached_session", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._login", AsyncMock(return_value=True)) as login:
            with patch("kaizen_form_filer.save_session_state", AsyncMock()) as save_state:
                with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
                    result = await file_to_kaizen(
                        "CBD",
                        {"clinical_reasoning": "test"},
                        "user",
                        "pass",
                        telegram_user_id=12345,
                    )

    assert result["status"] != "failed" or "Form page didn't load" not in (result["error"] or "")
    login.assert_awaited_once()
    save_state.assert_awaited_once()


# ─── Section C: Form page navigation ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_form_page_redirect_returns_failed(mock_playwright_ctx):
    mock_page = mock_playwright_ctx
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        mock_page.url = "https://kaizenep.com/dashboard"
        result = await file_to_kaizen("CBD", {"clinical_reasoning": "test"}, "user", "pass")
        assert result["status"] == "failed"
        assert "not available on your Kaizen profile or curriculum" in result["error"]


# ─── Section D: Field filling — core paths ──────────────────────────────────────

@pytest.mark.asyncio
async def test_all_fields_filled_returns_success(mock_playwright_ctx):
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            with patch("kaizen_form_filer._verify_entry_saved", AsyncMock(return_value=True)):
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
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            # Under the new internals, fields not present in the input are NOT
            # counted as skipped. To trigger a partial/skipped status, we must
            # explicitly pass an empty/None value for a mapped field.
            fields = {
                "date_of_encounter": "2026-03-21",
                "stage_of_training": "Higher",
                "clinical_reasoning": None,
            }
            result = await file_to_kaizen("CBD", fields, "user", "pass")
            assert len(result["skipped"]) > 0


@pytest.mark.asyncio
async def test_save_fails_returns_failed_not_partial(mock_playwright_ctx):
    """Regression: save failure must return 'failed', not 'partial'.
    This was the original bug — false success when save failed."""
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=False)):
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
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
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
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
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
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            fields = {"stage_of_training": "ST5", "clinical_reasoning": "test"}
            result = await file_to_kaizen("CBD", fields, "user", "pass")
            assert "stage_of_training" in result["filled"]
            stage_locator = mock_playwright_ctx._locators[
                f'[id="{FORM_FIELD_MAP["CBD"]["stage_of_training"]}"]'
            ]
            stage_locator.select_option.assert_any_call(
                value=STAGE_SELECT_VALUES["Higher"]
            )


@pytest.mark.asyncio
async def test_stage_maps_accs_for_st1(mock_playwright_ctx):
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            fields = {"stage_of_training": "ST1", "clinical_reasoning": "test"}
            result = await file_to_kaizen("CBD", fields, "user", "pass")
            assert "stage_of_training" in result["filled"]
            stage_locator = mock_playwright_ctx._locators[
                f'[id="{FORM_FIELD_MAP["CBD"]["stage_of_training"]}"]'
            ]
            stage_locator.select_option.assert_any_call(
                value=STAGE_SELECT_VALUES["ACCS"]
            )


@pytest.mark.asyncio
async def test_stage_maps_intermediate_for_st3(mock_playwright_ctx):
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            fields = {"stage_of_training": "ST3", "clinical_reasoning": "test"}
            result = await file_to_kaizen("CBD", fields, "user", "pass")
            assert "stage_of_training" in result["filled"]
            stage_locator = mock_playwright_ctx._locators[
                f'[id="{FORM_FIELD_MAP["CBD"]["stage_of_training"]}"]'
            ]
            stage_locator.select_option.assert_any_call(
                value=STAGE_SELECT_VALUES["Intermediate"]
            )


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
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
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
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            with patch("kaizen_form_filer._verify_entry_saved", AsyncMock(return_value=True)):
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
        if form_type.startswith("DOPS"):
            # procedure_name and procedural_skill are intentional aliases in DOPS mapping to the same dropdown
            non_date_ids = [v for k, v in field_map.items() if k not in ("procedure_name",) and v not in ("startDate", "endDate")]
        elif form_type.startswith("PROC_LOG"):
            # higher_procedural_skill_other and procedure_other are intentional aliases in PROC_LOG mapping to the same text field
            non_date_ids = [v for k, v in field_map.items() if k not in ("procedure_other",) and v not in ("startDate", "endDate")]
        assert len(non_date_ids) == len(set(non_date_ids)), (
            f"{form_type} has duplicate UUIDs: "
            f"{[v for v in non_date_ids if non_date_ids.count(v) > 1]}"
        )


# ─── Section I: Curriculum ticking ──────────────────────────────────────────────

# These patch the routing dispatcher _fill_curriculum_for_form (not the inline
# tree writer directly) so they verify the slo_codes/kc_targets threading
# independently of whether CBD routes via the in-form tree or the Add tags
# modal. CBD is tag-based, so the inline writer is never reached for it.
@pytest.mark.asyncio
async def test_curriculum_links_trigger_tick_attempt(mock_playwright_ctx):
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            with patch("kaizen_form_filer._fill_curriculum_for_form", AsyncMock(return_value=([], []))) as mock_fill:
                fields = {"stage_of_training": "Higher", "clinical_reasoning": "test"}
                result = await file_to_kaizen(
                    "CBD", fields, "user", "pass",
                    curriculum_links=["SLO1", "SLO3"],
                )
                mock_fill.assert_called_once()
                args = mock_fill.call_args[0]
                assert args[2] == ["SLO1", "SLO3"]
                assert args[3] == ["SLO1", "SLO3"]


@pytest.mark.asyncio
async def test_key_capabilities_without_curriculum_links_triggers_tick(mock_playwright_ctx):
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            with patch("kaizen_form_filer._fill_curriculum_for_form", AsyncMock(return_value=([], []))) as mock_fill:
                kcs = ["SLO1 KC1: Assess and stabilise the patient"]
                fields = {"stage_of_training": "Higher", "key_capabilities": kcs}
                result = await file_to_kaizen("CBD", fields, "user", "pass")
                mock_fill.assert_called_once()
                args = mock_fill.call_args[0]
                assert args[2] == []
                assert args[3] == kcs


@pytest.mark.asyncio
async def test_no_curriculum_links_skips_tick(mock_playwright_ctx):
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            with patch("kaizen_form_filer._fill_curriculum_links", AsyncMock(return_value=([], []))) as mock_fill:
                fields = {"stage_of_training": "Higher", "clinical_reasoning": "test"}
                result = await file_to_kaizen("CBD", fields, "user", "pass")
                mock_fill.assert_not_called()


@pytest.mark.asyncio
async def test_cbd_defaults_visible_procedural_skill_dropdown_to_na():
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value=[
        {
            "id": "8def931e-3a00-43ac-8529-44cdaf34be2d",
            "label": "ST4-ST6 Higher EM Procedural Skills",
            "selectedText": "",
            "selectedValue": "?",
            "hasNa": True,
        }
    ])

    with patch("kaizen_form_filer._fill_select", AsyncMock(return_value=True)) as fill_select:
        defaulted = await _default_non_applicable_procedural_selects(page, "CBD")

    assert defaulted == ["8def931e-3a00-43ac-8529-44cdaf34be2d"]
    fill_select.assert_awaited_once_with(
        page,
        "8def931e-3a00-43ac-8529-44cdaf34be2d",
        "- n/a -",
    )


@pytest.mark.asyncio
async def test_dops_does_not_default_required_procedural_skill_to_na():
    page = AsyncMock()
    page.evaluate = AsyncMock()

    with patch("kaizen_form_filer._fill_select", AsyncMock(return_value=True)) as fill_select:
        defaulted = await _default_non_applicable_procedural_selects(page, "DOPS")

    assert defaulted == []
    page.evaluate.assert_not_called()
    fill_select.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_to_kaizen_records_cbd_procedural_na_default(mock_playwright_ctx):
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            with patch("kaizen_form_filer._verify_entry_saved", AsyncMock(return_value=True)):
                with patch(
                    "kaizen_form_filer._default_non_applicable_procedural_selects",
                    AsyncMock(return_value=["8def931e-3a00-43ac-8529-44cdaf34be2d"]),
                ) as default_na:
                    fields = {
                        "date_of_encounter": "2026-03-21",
                        "stage_of_training": "Higher",
                        "clinical_reasoning": "Good clinical reasoning",
                        "reflection": "Reflective commentary",
                    }
                    result = await file_to_kaizen("CBD", fields, "user", "pass")

    default_na.assert_awaited_once()
    assert "procedural_skills_n/a (1)" in result["filled"]


@pytest.mark.asyncio
async def test_fill_date_click_non_actionable_fallback(mock_playwright_ctx):
    mock_page = mock_playwright_ctx
    from kaizen_form_filer import _fill_date

    orig_locator = mock_page.locator
    el = AsyncMock()
    el.count = AsyncMock(return_value=1)
    el.evaluate = AsyncMock(return_value="28/03/2026")

    click_calls = []
    async def mock_click(*args, **kwargs):
        click_calls.append((args, kwargs))
        raise Exception("Non-actionable click timeout")

    el.click = mock_click
    el.focus = AsyncMock()
    el.type = AsyncMock()

    def my_locator(selector):
        if "startDate" in selector:
            return el
        return orig_locator(selector)

    mock_page.locator = MagicMock(side_effect=my_locator)

    result = await _fill_date(mock_page, "startDate", "28/03/2026")

    # Normal click and triple click fail -> Try forced click and forced triple click -> Focus fallback
    assert len(click_calls) >= 2
    assert click_calls[0][1].get("timeout") is None
    assert click_calls[1][1].get("force") is True
    el.focus.assert_called()
    assert result is True


@pytest.mark.asyncio
async def test_fill_text_click_non_actionable_fallback(mock_playwright_ctx):
    mock_page = mock_playwright_ctx
    from kaizen_form_filer import _fill_text

    orig_locator = mock_page.locator
    el = AsyncMock()
    el.count = AsyncMock(return_value=1)
    el.first = el

    click_calls = []
    async def mock_click(*args, **kwargs):
        click_calls.append((args, kwargs))
        raise Exception("Non-actionable click timeout")

    el.click = mock_click
    el.focus = AsyncMock()
    el.fill = AsyncMock()

    def my_locator(selector):
        if "some-field" in selector:
            return el
        return orig_locator(selector)

    mock_page.locator = MagicMock(side_effect=my_locator)

    result = await _fill_text(mock_page, "some-field", "some text value")

    # Normal click fails -> Forced click fails -> Focus fallback
    assert len(click_calls) == 2
    assert click_calls[0][1].get("timeout") is None
    assert click_calls[1][1].get("force") is True
    el.focus.assert_called_once()
    assert result is True


@pytest.mark.asyncio
async def test_legacy_field_fill_preserves_selector_plan_for_date():
    from kaizen_form_filer import _fill_field_legacy

    page = MagicMock()
    page.keyboard.press = AsyncMock()
    page.evaluate = AsyncMock(return_value=False)
    date_field = AsyncMock()
    date_field.count = AsyncMock(return_value=1)
    date_field.first = date_field
    date_field.click = AsyncMock()
    date_field.type = AsyncMock()
    date_field.evaluate = AsyncMock(side_effect=["INPUT", "28/03/2026"])
    page.get_by_label.return_value = date_field
    page.locator = MagicMock()

    plan = {
        "candidates": [
            {
                "strategy": "label",
                "kind": "label",
                "value": "Date occurred on",
                "expected_unique": True,
            },
            {
                "strategy": "id",
                "kind": "css",
                "value": '[id="startDate"]',
                "expected_unique": True,
            },
        ]
    }

    result = await _fill_field_legacy(page, plan, "28/03/2026", "date_of_encounter", "CBD")

    assert result is True
    page.get_by_label.assert_called_with("Date occurred on")
    page.locator.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_field_fill_preserves_selector_plan_for_select_without_dom_id():
    from kaizen_form_filer import _fill_field_legacy

    page = MagicMock()
    page.evaluate = AsyncMock()
    select_field = AsyncMock()
    select_field.count = AsyncMock(return_value=1)
    select_field.first = select_field
    select_field.evaluate = AsyncMock(
        side_effect=[
            "SELECT",
            ["1 - Emergency Medicine", "2 - Anaesthetics"],
        ]
    )
    select_field.select_option = AsyncMock(
        side_effect=[Exception("label miss"), Exception("value miss"), None]
    )
    page.get_by_label.return_value = select_field
    page.locator = MagicMock()

    plan = {
        "candidates": [
            {
                "strategy": "label",
                "kind": "label",
                "value": "Placement",
                "expected_unique": True,
            }
        ]
    }

    result = await _fill_field_legacy(page, plan, "Emergency", "placement", "CBD")

    assert result is True
    page.get_by_label.assert_called_with("Placement")
    page.evaluate.assert_not_called()
    select_field.select_option.assert_any_await(label="1 - Emergency Medicine")


# ─── _save_form selectors ────────────────────────────────────────────


def _make_save_page(available_selectors: dict):
    """Build a page whose locator(selector) reports presence per available_selectors."""
    page = MagicMock()
    clicked = []

    def locator(selector):
        loc = MagicMock()
        first = MagicMock()
        text = available_selectors.get(selector)
        if text is None:
            first.count = AsyncMock(return_value=0)
        else:
            first.count = AsyncMock(return_value=1)
            first.inner_text = AsyncMock(return_value=text)

            async def _click():
                clicked.append((selector, text))
            first.click = _click
        loc.first = first
        return loc

    page.locator = locator
    page.inner_text = AsyncMock(return_value="")
    return page, clicked


@pytest.mark.asyncio
async def test_save_form_clicks_plain_save_anchor_on_edit_draft_page():
    """Edit-existing-draft page renders save as <a>Save</a> — the bug behind
    the DOPS "No save button/link found" failure."""
    from kaizen_form_filer import _save_form

    page, clicked = _make_save_page({'a:has-text("Save")': "Save"})
    with patch("kaizen_form_filer.asyncio.sleep", new=AsyncMock()):
        result = await _save_form(page, True)

    assert result is True
    assert clicked == [('a:has-text("Save")', "Save")]


@pytest.mark.asyncio
async def test_save_form_prefers_save_as_draft_over_plain_save():
    """When both exist, the more specific 'Save as draft' anchor wins."""
    from kaizen_form_filer import _save_form

    page, clicked = _make_save_page({
        'a:has-text("Save as draft")': "Save as draft",
        'a:has-text("Save")': "Save",
    })
    with patch("kaizen_form_filer.asyncio.sleep", new=AsyncMock()):
        result = await _save_form(page, True)

    assert result is True
    assert clicked[0][0] == 'a:has-text("Save as draft")'


@pytest.mark.asyncio
async def test_save_form_blocks_dangerous_buttons():
    """A 'Save' anchor whose actual text is destructive (e.g. matched a
    'Save and submit' button) must not be clicked."""
    from kaizen_form_filer import _save_form

    page, clicked = _make_save_page({'a:has-text("Save")': "Save and submit"})
    with patch("kaizen_form_filer.asyncio.sleep", new=AsyncMock()):
        result = await _save_form(page, True)

    assert result is False
    assert clicked == []


# ─── Section J: Post-filing QA verification ───────────────────────────────────

@pytest.mark.asyncio
async def test_verify_filing_qa_returns_three_bucket_structure():
    """The QA function returns a dict with filled, empty_expected, empty_acceptable lists."""
    from kaizen_form_filer import _verify_filing_qa

    page = AsyncMock()
    page.evaluate = AsyncMock(return_value={"tag": "TEXTAREA", "value": "filled text"})

    field_map = {
        "reflection": "uuid-reflection",
        "clinical_reasoning": "uuid-cr",
    }
    expected_fields = {
        "reflection": "expected reflection",
        "clinical_reasoning": "expected reasoning",
    }

    result = await _verify_filing_qa(page, "CBD", expected_fields, field_map)

    assert isinstance(result, dict)
    assert set(result.keys()) >= {"filled", "empty_expected", "empty_acceptable"}
    assert isinstance(result["filled"], list)
    assert isinstance(result["empty_expected"], list)
    assert isinstance(result["empty_acceptable"], list)
    assert "reflection" in result["filled"]
    assert "clinical_reasoning" in result["filled"]


@pytest.mark.asyncio
async def test_verify_filing_qa_categorises_empty_fields():
    """Empty DOM values go to empty_expected if the caller expected a value,
    otherwise to empty_acceptable."""
    from kaizen_form_filer import _verify_filing_qa

    async def evaluate_mock(_js, dom_id):
        if dom_id == "uuid-reflection":
            return {"tag": "TEXTAREA", "value": ""}
        if dom_id == "uuid-optional":
            return {"tag": "INPUT", "value": ""}
        return {"tag": "TEXTAREA", "value": "filled"}

    page = AsyncMock()
    page.evaluate = AsyncMock(side_effect=evaluate_mock)

    field_map = {
        "reflection": "uuid-reflection",
        "clinical_reasoning": "uuid-cr",
        "optional_other": "uuid-optional",
    }
    expected_fields = {
        "reflection": "user supplied this",
        "clinical_reasoning": "user supplied this",
    }

    result = await _verify_filing_qa(page, "CBD", expected_fields, field_map)

    assert "reflection" in result["empty_expected"]
    assert "clinical_reasoning" in result["filled"]
    assert "optional_other" in result["empty_acceptable"]


@pytest.mark.asyncio
async def test_verify_filing_qa_handles_select_via_selected_index():
    """SELECT elements are filled when selectedIndex > 0."""
    from kaizen_form_filer import _verify_filing_qa

    async def evaluate_mock(_js, dom_id):
        if dom_id == "uuid-stage":
            return {"tag": "SELECT", "selectedIndex": 2, "value": "Higher"}
        if dom_id == "uuid-placement":
            return {"tag": "SELECT", "selectedIndex": 0, "value": ""}
        return {"tag": "INPUT", "value": ""}

    page = AsyncMock()
    page.evaluate = AsyncMock(side_effect=evaluate_mock)

    field_map = {
        "stage_of_training": "uuid-stage",
        "placement": "uuid-placement",
    }
    expected_fields = {
        "stage_of_training": "Higher",
        "placement": "ED",
    }

    result = await _verify_filing_qa(page, "DOPS", expected_fields, field_map)

    assert "stage_of_training" in result["filled"]
    assert "placement" in result["empty_expected"]


@pytest.mark.asyncio
async def test_verify_filing_qa_accepts_saved_summary_stage_text():
    """Saved Kaizen summaries can show the stage after the select reads blank."""
    from kaizen_form_filer import _verify_filing_qa

    async def evaluate_mock(js, dom_id=None):
        if dom_id == "uuid-stage":
            return {"tag": "SELECT", "selectedIndex": 0, "value": ""}
        if "document.body" in js:
            return "Stage of training Intermediate / ST3"
        return {"tag": "INPUT", "value": ""}

    page = AsyncMock()
    page.evaluate = AsyncMock(side_effect=evaluate_mock)
    page.url = "https://kaizenep.com/events/view-section/doc-id"

    field_map = {"stage_of_training": "uuid-stage"}
    expected_fields = {"stage_of_training": "Intermediate"}

    result = await _verify_filing_qa(page, "CBD", expected_fields, field_map)

    assert "stage_of_training" in result["filled"]
    assert "stage_of_training" not in result["empty_expected"]
    assert result["gaps"] == []


@pytest.mark.asyncio
async def test_verify_filing_qa_missing_dom_element_is_empty():
    """A missing DOM element logs WARNING and counts as empty."""
    from kaizen_form_filer import _verify_filing_qa

    page = AsyncMock()
    page.evaluate = AsyncMock(return_value={"missing": True})

    field_map = {"reflection": "uuid-missing"}
    expected_fields = {"reflection": "should be filled"}

    result = await _verify_filing_qa(page, "CBD", expected_fields, field_map)

    assert "reflection" not in result["filled"]
    assert "reflection" in result["empty_expected"]


@pytest.mark.asyncio
async def test_file_to_kaizen_includes_filing_qa_in_result(mock_playwright_ctx):
    """filing_qa key appears in the file_to_kaizen result when filing succeeded."""
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            with patch("kaizen_form_filer._verify_entry_saved", AsyncMock(return_value=True)):
                with patch(
                    "kaizen_form_filer._verify_filing_qa",
                    AsyncMock(return_value={
                        "filled": ["reflection"],
                        "empty_expected": [],
                        "empty_acceptable": [],
                    }),
                ):
                    fields = {
                        "date_of_encounter": "2026-03-21",
                        "stage_of_training": "Higher",
                        "clinical_reasoning": "test",
                        "reflection": "test",
                    }
                    result = await file_to_kaizen("CBD", fields, "user", "pass")
                    assert "filing_qa" in result
                    assert result["filing_qa"]["filled"] == ["reflection"]


@pytest.mark.asyncio
async def test_file_to_kaizen_qa_gap_downgrades_false_success(mock_playwright_ctx):
    """A saved draft with a required mapped field still blank must not be reported as clean success."""
    qa_gap = {
        "field": "date_of_event",
        "dom_id": "5391f8de-de63-4db3-9e08-baaa2a380cfe",
        "form_type": "MINI_CEX",
        "kind": "text",
        "missing_dom": False,
        "expected_preview": "27/6/2026",
        "reason": "value_not_persisted",
    }
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            with patch("kaizen_form_filer._verify_entry_saved", AsyncMock(return_value=True)):
                with patch(
                    "kaizen_form_filer._verify_filing_qa",
                    AsyncMock(return_value={
                        "filled": ["date_of_encounter", "end_date"],
                        "empty_expected": ["date_of_event"],
                        "empty_acceptable": [],
                        "gaps": [qa_gap],
                    }),
                ):
                    fields = {
                        "date_of_encounter": "2026-06-27",
                        "clinical_setting": "Emergency Department",
                        "patient_presentation": "Central chest pain.",
                        "reflection": "Repeat ECG earlier next time.",
                    }
                    result = await file_to_kaizen("MINI_CEX", fields, "user", "pass")

    assert result["status"] == "partial"
    assert "date_of_event" in result["skipped"]
    assert result["error"] is None


@pytest.mark.asyncio
async def test_file_to_kaizen_qa_gap_removes_field_from_filled_not_just_skipped(mock_playwright_ctx):
    """A field that was filled pre-save but read back empty by post-save QA
    must be dropped from `filled`, not merely appended to `skipped`.

    Counting it in both lists is exactly how the bot reported "9 fields
    completed" with a saved summary date while the real Kaizen draft had an
    empty date field: `date_of_encounter` was filled before save, then QA
    found it empty after save, but the old code only added it to `skipped`
    and left the stale entry in `filled` — inflating the completed-field
    count and the saved-summary text derived from it.
    """
    qa_gap = {
        "field": "date_of_encounter",
        "dom_id": "startDate",
        "form_type": "MINI_CEX",
        "kind": "text",
        "missing_dom": False,
        "expected_preview": "27/6/2026",
        "reason": "value_not_persisted",
    }
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            with patch("kaizen_form_filer._verify_entry_saved", AsyncMock(return_value=True)):
                with patch(
                    "kaizen_form_filer._verify_filing_qa",
                    AsyncMock(return_value={
                        "filled": ["clinical_setting"],
                        "empty_expected": ["date_of_encounter"],
                        "empty_acceptable": [],
                        "gaps": [qa_gap],
                    }),
                ):
                    fields = {
                        "date_of_encounter": "2026-06-27",
                        "clinical_setting": "Emergency Department",
                        "patient_presentation": "Central chest pain.",
                        "reflection": "Repeat ECG earlier next time.",
                    }
                    result = await file_to_kaizen("MINI_CEX", fields, "user", "pass")

    assert result["status"] == "partial"
    assert "date_of_encounter" not in result["filled"]
    assert result["skipped"].count("date_of_encounter") == 1


@pytest.mark.asyncio
async def test_file_to_kaizen_qa_gap_decrements_kc_count(mock_playwright_ctx):
    """A `kc:` gap must decrement the ticked-KC count in the saved summary,
    not just get appended to `skipped` alongside a stale higher count."""
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            with patch("kaizen_form_filer._verify_entry_saved", AsyncMock(return_value=True)):
                with patch(
                    "kaizen_form_filer._fill_curriculum_for_form",
                    AsyncMock(return_value=(["SLO12-KC6", "SLO12-KC7"], [])),
                ):
                    with patch(
                        "kaizen_form_filer._verify_filing_qa",
                        AsyncMock(return_value={
                            "filled": [],
                            "empty_expected": ["kc:SLO12-KC6"],
                            "empty_acceptable": [],
                            "gaps": [{
                                "field": "kc:SLO12-KC6",
                                "dom_id": None,
                                "form_type": "CBD",
                                "kind": "kc_checkbox",
                                "missing_dom": False,
                                "expected_preview": "SLO12-KC6",
                                "reason": "kc_not_ticked",
                            }],
                        }),
                    ):
                        fields = {"stage_of_training": "Higher", "clinical_reasoning": "test"}
                        result = await file_to_kaizen(
                            "CBD", fields, "user", "pass",
                            curriculum_links=["SLO12"],
                        )

    assert result["status"] == "partial"
    assert "curriculum_links (1 KCs)" in result["filled"]
    assert "curriculum_links (2 KCs)" not in result["filled"]
    assert "kc:SLO12-KC6" in result["skipped"]


@pytest.mark.asyncio
async def test_file_to_kaizen_qa_gap_removes_kc_entry_when_none_remain(mock_playwright_ctx):
    """If every ticked KC turns out empty on QA re-read, the curriculum_links
    summary entry must be removed entirely rather than left at a false
    positive count."""
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            with patch("kaizen_form_filer._verify_entry_saved", AsyncMock(return_value=True)):
                with patch(
                    "kaizen_form_filer._fill_curriculum_for_form",
                    AsyncMock(return_value=(["SLO12-KC6"], [])),
                ):
                    with patch(
                        "kaizen_form_filer._verify_filing_qa",
                        AsyncMock(return_value={
                            "filled": [],
                            "empty_expected": ["kc:SLO12-KC6"],
                            "empty_acceptable": [],
                            "gaps": [{
                                "field": "kc:SLO12-KC6",
                                "dom_id": None,
                                "form_type": "CBD",
                                "kind": "kc_checkbox",
                                "missing_dom": False,
                                "expected_preview": "SLO12-KC6",
                                "reason": "kc_not_ticked",
                            }],
                        }),
                    ):
                        fields = {"stage_of_training": "Higher", "clinical_reasoning": "test"}
                        result = await file_to_kaizen(
                            "CBD", fields, "user", "pass",
                            curriculum_links=["SLO12"],
                        )

    assert not any(entry.startswith("curriculum_links (") for entry in result["filled"])
    assert "kc:SLO12-KC6" in result["skipped"]


@pytest.mark.asyncio
async def test_file_to_kaizen_qa_exception_does_not_fail_filing(mock_playwright_ctx):
    """QA exceptions must not prevent returning a saved filing result."""
    with patch("kaizen_form_filer._login", AsyncMock(return_value=True)):
        with patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)):
            with patch("kaizen_form_filer._verify_entry_saved", AsyncMock(return_value=True)):
                with patch(
                    "kaizen_form_filer._verify_filing_qa",
                    AsyncMock(side_effect=RuntimeError("simulated QA failure")),
                ):
                    fields = {
                        "date_of_encounter": "2026-03-21",
                        "stage_of_training": "Higher",
                        "clinical_reasoning": "test",
                        "reflection": "test",
                    }
                    result = await file_to_kaizen("CBD", fields, "user", "pass")
                    assert result["status"] in ("success", "partial")
                    assert "filing_qa" in result
