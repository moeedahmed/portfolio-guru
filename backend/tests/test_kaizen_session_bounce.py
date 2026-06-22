"""Offline regression tests for the 2025 CBD stale-session bounce failure.

Reproduction of the live beta incident: Portfolio Guru filed a CBD with a
stale cached Kaizen session. Kaizen silently rejected the cookies and
redirected the /events/new-section/<uuid> navigation to the in-app
/events/list activities page. Because /events/list is NOT an auth URL, the
filer missed the bounce, returned a generic UNKNOWN failure, did not re-login,
did not invalidate the poisoned cache, and the early return bypassed filing
telemetry entirely.

These tests pin the fixed behaviour:

1. A /events/list bounce on a cached session triggers re-auth + a single
   navigation retry (recoverable path).
2. A persistent bounce (still /events/list after re-auth) invalidates the
   cached session and classifies the failure as a session/login failure with
   a 'session expired' marker the bot routes to 'Reconnect Kaizen'.
3. Every early failure (including the bounce) emits filing telemetry so beta
   support can see the reason.

If any of these flip, the next stale session will silently re-loop the doctor
into the same poisoned cache with 'fill manually' copy — the exact regression.
"""

from __future__ import annotations

import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import kaizen_form_filer
from kaizen_form_filer import (
    _is_form_navigation_bounce,
    _early_filing_failure,
    file_to_kaizen,
)


NEW_SECTION_URL = "https://kaizenep.com/events/new-section/some-uuid"
EVENTS_LIST_URL = "https://kaizenep.com/events/list"


# ─── _is_form_navigation_bounce ──────────────────────────────────────────────


def test_form_navigation_bounce_detects_events_list():
    assert _is_form_navigation_bounce(EVENTS_LIST_URL) is True


def test_form_navigation_bounce_detects_auth_redirect():
    assert _is_form_navigation_bounce("https://auth.kaizenep.com/interaction/x") is True


def test_form_navigation_bounce_false_on_real_form_url():
    assert _is_form_navigation_bounce(NEW_SECTION_URL) is False


def test_form_navigation_bounce_true_on_empty_url():
    assert _is_form_navigation_bounce("") is True


# ─── _early_filing_failure telemetry ─────────────────────────────────────────


def test_early_filing_failure_logs_telemetry_and_returns_failed():
    with patch("filing_result_logger.log_filing_result") as log:
        result = _early_filing_failure("CBD", "Form page didn't load — redirected to x")

    assert result["status"] == "failed"
    assert "Form page didn't load" in result["error"]
    log.assert_called_once()
    kwargs = log.call_args.kwargs
    assert kwargs["form_type"] == "CBD"
    assert kwargs["status"] == "failed"
    assert "Form page didn't load" in kwargs["error_hint"]


# ─── file_to_kaizen bounce behaviour ─────────────────────────────────────────


@pytest.fixture
def bounce_ctx():
    """Patch async_playwright + instant sleep so file_to_kaizen runs offline.

    Yields the mock page; tests configure ``page.goto`` to script the
    navigation bounce(s).
    """
    page = AsyncMock()
    page.url = "https://kaizenep.com/activities"
    page.context = AsyncMock()
    locator = MagicMock()
    locator.count = AsyncMock(return_value=0)
    locator.input_value = AsyncMock(return_value="")
    locator.select_option = AsyncMock()
    page.locator = MagicMock(return_value=locator)

    browser = AsyncMock()
    browser.new_page = AsyncMock(return_value=page)
    browser.close = AsyncMock()
    browser.contexts = []

    pw = AsyncMock()
    pw.chromium.launch = AsyncMock(return_value=browser)
    pw.stop = AsyncMock()

    ap = MagicMock()
    ap.start = AsyncMock(return_value=pw)

    async def _noop_sleep(*args, **kwargs):
        pass

    orig_sleep = asyncio.sleep
    with patch("kaizen_form_filer.async_playwright", return_value=ap), \
         patch("kaizen_form_filer.KAIZEN_USE_CDP", False):
        asyncio.sleep = _noop_sleep
        try:
            yield page
        finally:
            asyncio.sleep = orig_sleep


@pytest.mark.asyncio
async def test_events_list_bounce_reauths_and_retries(bounce_ctx):
    """First /events/list bounce on a cached session → re-auth + retry that
    recovers onto the real form. Cache must NOT be invalidated."""
    page = bounce_ctx
    landings = [EVENTS_LIST_URL, NEW_SECTION_URL]

    async def _goto(url, *args, **kwargs):
        page.url = landings.pop(0) if landings else NEW_SECTION_URL

    page.goto = AsyncMock(side_effect=_goto)

    with patch("kaizen_form_filer.use_cached_session", AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._login", AsyncMock(return_value=True)) as login, \
         patch("kaizen_form_filer.save_session_state", AsyncMock()) as save_state, \
         patch("kaizen_form_filer.invalidate_session_cache") as invalidate, \
         patch("kaizen_form_filer._fill_field_legacy", AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._save_form", AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._verify_entry_saved", AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._verify_filing_qa", AsyncMock(return_value=None)), \
         patch("filing_result_logger.log_filing_result"):
        result = await file_to_kaizen(
            "CBD",
            {"clinical_reasoning": "test"},
            "doctor@example.com",
            "pass",
            telegram_user_id=12345,
        )

    login.assert_awaited_once()  # exactly one re-auth, no infinite retry
    save_state.assert_awaited()
    invalidate.assert_not_called()
    assert "session expired" not in (result.get("error") or "").lower()
    assert "redirecting" not in (result.get("error") or "").lower()


@pytest.mark.asyncio
async def test_persistent_events_list_bounce_invalidates_cache_and_classifies_session_failure(bounce_ctx):
    """Still /events/list after re-auth → invalidate the poisoned cache and
    return a session-expired failure (never a generic UNKNOWN), with telemetry."""
    page = bounce_ctx

    async def _goto(url, *args, **kwargs):
        page.url = EVENTS_LIST_URL  # bounces every time

    page.goto = AsyncMock(side_effect=_goto)

    with patch("kaizen_form_filer.use_cached_session", AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._login", AsyncMock(return_value=True)) as login, \
         patch("kaizen_form_filer.save_session_state", AsyncMock()), \
         patch("kaizen_form_filer.invalidate_session_cache") as invalidate, \
         patch("filing_result_logger.log_filing_result") as log:
        result = await file_to_kaizen(
            "CBD",
            {"clinical_reasoning": "test"},
            "doctor@example.com",
            "pass",
            telegram_user_id=12345,
        )

    login.assert_awaited_once()  # one re-auth attempt, bounded
    invalidate.assert_called_once_with(12345, "doctor@example.com")
    assert result["status"] == "failed"
    assert "session expired" in result["error"].lower()
    # Telemetry must record the early bounce failure for beta support.
    log.assert_called()
    assert any(
        call.kwargs.get("status") == "failed"
        and "session expired" in (call.kwargs.get("error_hint") or "").lower()
        for call in log.call_args_list
    )


@pytest.mark.asyncio
async def test_failed_initial_login_emits_telemetry(bounce_ctx):
    """A flat login failure (no cached session) must also emit telemetry —
    this early return used to exit before the filing-result log."""
    page = bounce_ctx
    page.goto = AsyncMock()

    with patch("kaizen_form_filer.use_cached_session", AsyncMock(return_value=False)), \
         patch("kaizen_form_filer._login", AsyncMock(return_value=False)), \
         patch("filing_result_logger.log_filing_result") as log:
        result = await file_to_kaizen(
            "CBD",
            {"clinical_reasoning": "test"},
            "doctor@example.com",
            "pass",
            telegram_user_id=12345,
        )

    assert result["status"] == "failed"
    assert "Could not log in to Kaizen" in result["error"]
    log.assert_called_once()
    assert log.call_args.kwargs["status"] == "failed"


# ─── bot recovery-copy routing ───────────────────────────────────────────────


def test_persistent_bounce_error_routes_to_reconnect_copy():
    """The session-bounce error the filer returns must be classified by the bot
    as a session failure → 'Reconnect Kaizen', not 'Try Again' / 'fill manually'."""
    from bot import _is_session_failure_error, _classify_filing_failure

    bounce_error = (
        "Kaizen session expired — the form kept redirecting to "
        "https://kaizenep.com/events/list instead of loading. The stale "
        "session has been cleared; use /settings to reconnect Kaizen."
    )
    assert _is_session_failure_error(bounce_error) is True
    # And the generic field/save buckets must not swallow it.
    assert _classify_filing_failure(bounce_error, [], "failed", []) == "LOGIN_FAILED"


def test_session_failure_helper_ignores_field_failures():
    from bot import _is_session_failure_error

    assert _is_session_failure_error("Save button not found") is False
    assert _is_session_failure_error("Form page didn't load — redirected to /dashboard") is False
    assert _is_session_failure_error(None) is False
