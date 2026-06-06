"""Focused offline regression tests for Kaizen login/browser/session classification.

These pin invariants from the post-9e70f54 reliability slice:

1. Bad credentials surface as user-facing "Login failed" (provider returns False,
   bot shows the password-check copy).
2. Browser-harness / CDP / subprocess failure surfaces as
   ``KaizenInfrastructureError`` and the bot shows the "couldn't reach Kaizen"
   copy — *never* "Login failed", or users will retype passwords that are fine.
3. ``_resolve_cdp_ws`` honors ``BU_CDP_WS``, falls back to ``KAIZEN_CDP_URL``,
   defaults to the managed ``http://localhost:18800`` (NOT Chrome's stock 9222),
   and returns ``None`` on any failure rather than blowing up.

If any of these flip, the next live login will silently re-misclassify
infra outages as bad creds — exactly the regression we just fixed.
"""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── _resolve_cdp_ws ─────────────────────────────────────────────────────


def test_resolve_cdp_ws_honors_existing_bu_cdp_ws():
    from engine.providers.kaizen import _resolve_cdp_ws

    env = {"BU_CDP_WS": "ws://example.invalid:18800/devtools/browser/abc"}
    assert _resolve_cdp_ws(env) == "ws://example.invalid:18800/devtools/browser/abc"


def test_resolve_cdp_ws_uses_managed_18800_default(monkeypatch):
    """No env overrides → must hit the managed port (18800), not 9222."""
    from engine.providers.kaizen import _resolve_cdp_ws

    seen = {}

    class _Resp:
        def read(self):
            return b'{"webSocketDebuggerUrl": "ws://localhost:18800/devtools/browser/x"}'

    def fake_urlopen(url, timeout=3):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ws = _resolve_cdp_ws({})
    assert ws == "ws://localhost:18800/devtools/browser/x"
    assert "18800" in seen["url"]
    assert "9222" not in seen["url"]


def test_resolve_cdp_ws_honors_kaizen_cdp_url(monkeypatch):
    from engine.providers.kaizen import _resolve_cdp_ws

    seen = {}

    class _Resp:
        def read(self):
            return b'{"webSocketDebuggerUrl": "ws://elsewhere:9999/devtools/browser/y"}'

    def fake_urlopen(url, timeout=3):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    ws = _resolve_cdp_ws({"KAIZEN_CDP_URL": "http://elsewhere:9999"})
    assert ws == "ws://elsewhere:9999/devtools/browser/y"
    assert seen["url"] == "http://elsewhere:9999/json/version"


def test_resolve_cdp_ws_returns_none_on_connection_failure(monkeypatch):
    """A dead CDP endpoint must not raise — caller proceeds without BU_CDP_WS."""
    from engine.providers.kaizen import _resolve_cdp_ws

    def boom(url, timeout=3):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert _resolve_cdp_ws({}) is None


def test_resolve_cdp_ws_returns_none_when_response_has_no_ws(monkeypatch):
    from engine.providers.kaizen import _resolve_cdp_ws

    class _Resp:
        def read(self):
            return b'{"something": "else"}'

    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=3: _Resp())
    assert _resolve_cdp_ws({}) is None


# ─── KaizenInfrastructureError vs credential failure ─────────────────────


def test_provider_run_file_raises_infrastructure_error_on_nonzero_exit(monkeypatch):
    """subprocess returncode != 0 must surface as KaizenInfrastructureError,
    NOT as a generic RuntimeError that downstream code can confuse with a
    credentials rejection."""
    from engine.providers.kaizen import KaizenProvider, KaizenInfrastructureError

    fake_result = MagicMock(returncode=1, stderr="cdp: connection refused", stdout="")
    monkeypatch.setattr("engine.providers.kaizen._resolve_cdp_ws", lambda env: None)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: fake_result)

    provider = KaizenProvider("u", "p")
    with pytest.raises(KaizenInfrastructureError):
        provider._run_file("print('hi')")


def test_provider_run_file_raises_infrastructure_error_when_binary_missing(monkeypatch):
    from engine.providers.kaizen import KaizenProvider, KaizenInfrastructureError

    def missing(*a, **k):
        raise FileNotFoundError("browser-harness not on PATH")

    monkeypatch.setattr("engine.providers.kaizen._resolve_cdp_ws", lambda env: None)
    monkeypatch.setattr("subprocess.run", missing)

    provider = KaizenProvider("u", "p")
    with pytest.raises(KaizenInfrastructureError):
        provider._run_file("print('hi')")


def test_provider_run_file_raises_infrastructure_error_on_timeout(monkeypatch):
    from engine.providers.kaizen import KaizenProvider, KaizenInfrastructureError

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="browser-harness", timeout=1)

    monkeypatch.setattr("engine.providers.kaizen._resolve_cdp_ws", lambda env: None)
    monkeypatch.setattr("subprocess.run", timeout)

    provider = KaizenProvider("u", "p")
    with pytest.raises(KaizenInfrastructureError):
        provider._run_file("print('hi')")


def test_provider_connect_propagates_infrastructure_error(monkeypatch):
    """If the browser layer is broken, connect() must NOT swallow it as
    ``return False`` — callers need to tell the user it's not their password."""
    from engine.providers.kaizen import KaizenProvider, KaizenInfrastructureError

    provider = KaizenProvider("u", "p")

    def boom(self, code, timeout=60):
        raise KaizenInfrastructureError("cdp gone")

    monkeypatch.setattr(KaizenProvider, "_run_file", boom)
    with pytest.raises(KaizenInfrastructureError):
        provider.connect()


def test_provider_connect_returns_false_on_credential_failure(monkeypatch):
    """Browser ran fine but landed on a non-dashboard URL → bad credentials."""
    from engine.providers.kaizen import KaizenProvider

    provider = KaizenProvider("u", "p")

    def login_page(self, code, timeout=60):
        return "https://eportfolio.rcem.ac.uk/login"

    monkeypatch.setattr(KaizenProvider, "_run_file", login_page)
    assert provider.connect() is False


def test_provider_connect_returns_true_on_dashboard_redirect(monkeypatch):
    from engine.providers.kaizen import KaizenProvider

    provider = KaizenProvider("u", "p")

    def dashboard(self, code, timeout=60):
        return "https://kaizenep.com/dashboard"

    monkeypatch.setattr(KaizenProvider, "_run_file", dashboard)
    # detect_role calls _run_file again; stub it after the connect() URL probe.
    monkeypatch.setattr(KaizenProvider, "detect_role", lambda self: "hst")
    assert provider.connect() is True
    assert provider.portfolio_type == "hst"


# ─── setup_password user-facing split ────────────────────────────────────


def _make_setup_password_harness():
    """Build the minimum scaffolding setup_password needs to run end-to-end
    in a unit test: an update with a deletable message, a context with the
    captured username, and a sink that records every _flow_edit call."""
    from tests.bot_simulator import BotSimulator

    sim = BotSimulator()
    update = sim._make_text_update("safe-password")
    update.message.delete = AsyncMock()
    context = sim._make_context()
    context.user_data["setup_username"] = "doctor@example.com"
    return sim, update, context


@pytest.mark.asyncio
async def test_setup_password_credential_failure_shows_login_failed(monkeypatch):
    from bot import setup_password, AWAIT_USERNAME

    sim, update, context = _make_setup_password_harness()

    with patch("bot._test_kaizen_login", new=AsyncMock(return_value=False)), \
         patch("bot.store_credentials") as store_creds:
        result = await setup_password(update, context)

    assert result == AWAIT_USERNAME
    store_creds.assert_not_called()
    last = sim.get_last_text().lower()
    assert "login failed" in last
    assert "couldn't reach" not in last  # explicitly NOT the infra copy
    assert ("🔄 Try again", "ACTION|setup") in sim.get_last_buttons()
    assert ("❌ Cancel", "ACTION|cancel") in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_setup_password_infra_failure_does_not_show_login_failed(monkeypatch):
    """If _test_kaizen_login raises KaizenInfrastructureError, the user must
    see the "couldn't reach Kaizen" copy and credentials must NOT be stored.
    The "Login failed" wording must not appear — it would train the doctor to
    retype a password that is actually fine."""
    from bot import setup_password, AWAIT_USERNAME
    from engine.providers.kaizen import KaizenInfrastructureError

    sim, update, context = _make_setup_password_harness()

    async def infra_boom(u, p):
        raise KaizenInfrastructureError("CDP unreachable on localhost:18800")

    with patch("bot._test_kaizen_login", new=infra_boom), \
         patch("bot.store_credentials") as store_creds:
        result = await setup_password(update, context)

    assert result == AWAIT_USERNAME
    store_creds.assert_not_called()
    last = sim.get_last_text().lower()
    assert "couldn't reach kaizen" in last
    assert "login failed" not in last  # the exact regression we're guarding
    assert ("🔄 Try again", "ACTION|setup") in sim.get_last_buttons()
    assert ("❌ Cancel", "ACTION|cancel") in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_test_kaizen_login_propagates_infrastructure_error(monkeypatch):
    """The bot-side wrapper must NOT downgrade KaizenInfrastructureError to
    a False return value — that would re-create the misclassification bug."""
    from bot import _test_kaizen_login
    from engine.providers.kaizen import KaizenInfrastructureError

    async def fake_connect():
        raise KaizenInfrastructureError("cdp died")

    monkeypatch.setattr("kaizen_form_filer.connect_cdp_browser", fake_connect)
    with pytest.raises(KaizenInfrastructureError):
        await _test_kaizen_login("u", "p")


@pytest.mark.asyncio
async def test_test_kaizen_login_returns_false_on_credential_rejection(monkeypatch):
    from bot import _test_kaizen_login

    class FakeContext:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class FakePW:
        def __init__(self):
            self.stopped = False

        async def stop(self):
            self.stopped = True

    context = FakeContext()
    pw = FakePW()
    page = MagicMock(context=context)

    async def fake_connect():
        return page, pw

    async def fake_login(_page, _username, _password):
        return False

    monkeypatch.setattr("kaizen_form_filer.connect_cdp_browser", fake_connect)
    monkeypatch.setattr("kaizen_form_filer._login", fake_login)
    result = await _test_kaizen_login("u", "bad")
    assert result is False
    assert context.closed
    assert pw.stopped


@pytest.mark.asyncio
async def test_test_kaizen_login_detects_role_from_isolated_logged_in_page(monkeypatch):
    from bot import _test_kaizen_login

    class FakeContext:
        async def close(self):
            pass

    class FakePW:
        async def stop(self):
            pass

    class FakeLocator:
        async def inner_text(self, timeout=5000):
            return "Non-Trainee Higher / CESR-Portfolio Pathway"

    class FakePage:
        context = FakeContext()

        async def title(self):
            return "Higher Trainee Dashboard"

        def locator(self, _selector):
            return FakeLocator()

    async def fake_connect():
        return FakePage(), FakePW()

    async def fake_login(_page, username, password):
        assert (username, password) == ("sana@example.com", "secret")
        return True

    monkeypatch.setattr("kaizen_form_filer.connect_cdp_browser", fake_connect)
    monkeypatch.setattr("kaizen_form_filer._login", fake_login)

    assert await _test_kaizen_login("sana@example.com", "secret") == "non_training_higher"


# ─── password-message delete + visible testing feedback ──────────────────────


@pytest.mark.asyncio
async def test_setup_password_warns_when_delete_fails_in_private_chat():
    """When message deletion fails in a private chat the user must see a
    privacy warning. Before the fix the exception was swallowed silently so
    the password stayed visible with zero feedback."""
    from bot import setup_password

    sim, update, context = _make_setup_password_harness()
    update.message.delete = AsyncMock(side_effect=Exception("Telegram API error"))
    update.effective_chat.type = "private"

    with patch("bot._test_kaizen_login", new=AsyncMock(return_value=False)), \
         patch("bot.store_credentials"):
        await setup_password(update, context)

    texts = [t for _, t, _ in sim.messages_sent if t]
    assert any("couldn't delete" in t.lower() for t in texts), (
        "No deletion-failure warning in private chat — password exposed silently"
    )


@pytest.mark.asyncio
async def test_setup_password_delete_failure_clears_anchor_so_testing_is_visible():
    """When delete fails the flow anchor must be cleared so _flow_edit sends a
    fresh bottom message for 'Testing...' rather than editing an older message
    above the user's still-visible password."""
    from bot import setup_password

    sim, update, context = _make_setup_password_harness()
    update.message.delete = AsyncMock(side_effect=Exception("Telegram API error"))
    update.effective_chat.type = "private"
    # Seed an anchor to prove it gets cleared on delete failure.
    context.user_data["_flow_anchor_setup"] = (99999999, 42)

    with patch("bot._test_kaizen_login", new=AsyncMock(return_value=False)), \
         patch("bot.store_credentials"):
        await setup_password(update, context)

    assert any(
        kind == "send" and text and "testing your kaizen login" in text.lower()
        for kind, text, _ in sim.messages_sent
    ), (
        "Testing feedback was not sent as a fresh bottom message after delete failure"
    )


@pytest.mark.asyncio
async def test_setup_password_shows_testing_feedback_after_submission():
    """After password submission the bot must emit a visible 'Testing...'
    status so the user knows validation is in progress."""
    from bot import setup_password

    sim, update, context = _make_setup_password_harness()
    # Successful delete — normal happy path.
    update.message.delete = AsyncMock()

    with patch("bot._test_kaizen_login", new=AsyncMock(return_value=False)), \
         patch("bot.store_credentials"):
        await setup_password(update, context)

    texts = [t for _, t, _ in sim.messages_sent if t]
    assert any("🔄" in t or "testing" in t.lower() for t in texts), (
        "No 'Testing...' feedback emitted after password submission"
    )
