"""ops_alert: operator paging + heartbeat must be safe, gated, and rate-limited.

The operator DM is a fixed-template boundary: only the four approved keys can
send, the payload is always the literal template, and nothing a caller passes
(text, key, exception text) may reach the DM or the local log.
"""
import json
import logging
from types import SimpleNamespace

import pytest

import ops_alert

# Stands in for any identifier/PHI a caller might leak. Must never appear in an
# outbound payload or a captured log line.
SENTINEL = "SENTINEL-LEAK-7f3a9c"

LABEL = "Portfolio Guru — support alert"
FILING_TEMPLATE = (
    f"{LABEL}\n\n"
    "A Kaizen draft save could not be confirmed. Check the filing report and "
    "verify the draft in Kaizen before retrying."
)
HANDLER_TEMPLATE = (
    f"{LABEL}\n\n"
    "An unexpected bot error needs investigation. Check the service logs; do "
    "not assume a filing completed."
)
PAYMENT_TEMPLATE = (
    f"{LABEL}\n\n"
    "A payment webhook could not be processed. Check the provider dashboard."
)
EXPECTED_TEMPLATES = {
    "filing_uncertain": FILING_TEMPLATE,
    "handler_error": HANDLER_TEMPLATE,
    "webhook_fail": PAYMENT_TEMPLATE,
    "webhook_unhandled": PAYMENT_TEMPLATE,
}


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


class _RaisingBot:
    async def send_message(self, chat_id, text):
        raise RuntimeError(SENTINEL)


def _reset():
    ops_alert._last_alert.clear()


def _capture_sync(monkeypatch):
    """Route the sync Telegram HTTP path into a list; no real network."""
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token-not-real")
    payloads = []

    def fake_urlopen(req, timeout=5):
        payloads.append(json.loads(req.data.decode()))

    monkeypatch.setattr(ops_alert.urllib.request, "urlopen", fake_urlopen)
    return payloads


async def test_notify_operator_sends_then_rate_limits(monkeypatch):
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    bot = _Bot()

    await ops_alert.notify_operator(bot, "boom", key="handler_error")
    await ops_alert.notify_operator(bot, "boom again", key="handler_error")

    assert len(bot.sent) == 1
    assert bot.sent[0][0] == 123
    assert bot.sent[0][1] == HANDLER_TEMPLATE


async def test_notify_operator_noop_without_operator_id(monkeypatch):
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 0)
    bot = _Bot()
    await ops_alert.notify_operator(bot, "boom", key="handler_error")
    assert bot.sent == []


async def test_notify_operator_noop_without_bot(monkeypatch):
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    # Must not raise and must not consume the cooldown window.
    await ops_alert.notify_operator(None, key="handler_error")
    bot = _Bot()
    await ops_alert.notify_operator(bot, key="handler_error")
    assert len(bot.sent) == 1


async def test_distinct_keys_each_send(monkeypatch):
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    bot = _Bot()
    await ops_alert.notify_operator(bot, "a", key="filing_uncertain")
    await ops_alert.notify_operator(bot, "b", key="handler_error")
    assert len(bot.sent) == 2


def test_heartbeat_noop_without_url(monkeypatch):
    monkeypatch.setattr(ops_alert, "HEARTBEAT_URL", "")
    called = {"n": 0}
    monkeypatch.setattr(ops_alert.urllib.request, "urlopen", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    ops_alert.heartbeat()
    assert called["n"] == 0


def test_heartbeat_pings_when_url_set(monkeypatch):
    monkeypatch.setattr(ops_alert, "HEARTBEAT_URL", "https://hc-ping.com/abc")
    hits = []
    monkeypatch.setattr(ops_alert.urllib.request, "urlopen", lambda url, timeout=5: hits.append(url))
    ops_alert.heartbeat()
    assert hits == ["https://hc-ping.com/abc"]


def test_notify_sync_noop_without_token(monkeypatch):
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    hits = []
    monkeypatch.setattr(ops_alert.urllib.request, "urlopen", lambda *a, **k: hits.append(1))
    ops_alert.notify_operator_sync("x", key="webhook_fail")
    assert hits == []


def test_notify_sync_sends_template_then_rate_limits(monkeypatch):
    payloads = _capture_sync(monkeypatch)
    ops_alert.notify_operator_sync("first", key="webhook_fail")
    ops_alert.notify_operator_sync("second", key="webhook_fail")
    assert payloads == [{"chat_id": 123, "text": PAYMENT_TEMPLATE}]


# ---------------------------------------------------------------------------
# Fixed template set
# ---------------------------------------------------------------------------

def test_template_set_is_exactly_the_four_approved_events():
    assert ops_alert.ALERT_TEMPLATES == EXPECTED_TEMPLATES


def test_render_alert_unknown_key_is_none():
    assert ops_alert.render_alert("generic") is None
    assert ops_alert.render_alert("") is None
    assert ops_alert.render_alert(f"filing_uncertain:{SENTINEL}") is None


# ---------------------------------------------------------------------------
# Symmetric async/sync boundary: identical sentinel in every caller field
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("channel", ["async", "sync"])
@pytest.mark.parametrize("key,template", sorted(EXPECTED_TEMPLATES.items()))
async def test_known_key_sends_exact_template_and_ignores_text(monkeypatch, caplog, channel, key, template):
    caplog.set_level(logging.DEBUG)
    if channel == "async":
        _reset()
        monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
        bot = _Bot()
        await ops_alert.notify_operator(bot, SENTINEL, key=key)
        payloads = [{"chat_id": c, "text": t} for c, t in bot.sent]
    else:
        payloads = _capture_sync(monkeypatch)
        ops_alert.notify_operator_sync(SENTINEL, key=key)

    assert payloads == [{"chat_id": 123, "text": template}]
    assert SENTINEL not in payloads[0]["text"]
    assert SENTINEL not in caplog.text


@pytest.mark.parametrize("channel", ["async", "sync"])
@pytest.mark.parametrize("bad_key", [
    "generic",
    "",
    f"kaizen_filing_failure:CBD:failed:{SENTINEL}",
    f"handler_error:{SENTINEL}",
    "Filing_Uncertain",
])
async def test_unknown_key_never_sends_and_logs_fixed_line_only(monkeypatch, caplog, channel, bad_key):
    caplog.set_level(logging.DEBUG)
    if channel == "async":
        _reset()
        monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
        bot = _Bot()
        await ops_alert.notify_operator(bot, SENTINEL, key=bad_key)
        sent = bot.sent
    else:
        sent = _capture_sync(monkeypatch)
        ops_alert.notify_operator_sync(SENTINEL, key=bad_key)

    assert sent == []
    assert "Operator notification suppressed: unknown event" in caplog.text
    assert SENTINEL not in caplog.text
    # The raw key is never logged either — only the fixed line above.
    if bad_key:
        assert bad_key not in caplog.text


@pytest.mark.parametrize("channel", ["async", "sync"])
async def test_failed_send_is_swallowed_without_logging_exception_text(monkeypatch, caplog, channel):
    caplog.set_level(logging.DEBUG)
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    if channel == "async":
        key = "handler_error"
        await ops_alert.notify_operator(_RaisingBot(), key=key)
        assert "notify_operator failed" in caplog.text
    else:
        key = "webhook_fail"
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", f"token-{SENTINEL}")

        def boom(req, timeout=5):
            raise RuntimeError(SENTINEL)

        monkeypatch.setattr(ops_alert.urllib.request, "urlopen", boom)
        ops_alert.notify_operator_sync(key=key)
        assert "notify_operator_sync failed" in caplog.text

    assert SENTINEL not in caplog.text
    # A failed send still consumes the window (known, accepted; no retry).
    assert key in ops_alert._last_alert


# ---------------------------------------------------------------------------
# bot._alert_filing_failure classification (status first, reason second)
# ---------------------------------------------------------------------------

async def test_routine_form_unavailable_never_pages_admin(monkeypatch):
    from types import SimpleNamespace
    import bot as bot_module
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    bot = _Bot()
    await bot_module._alert_filing_failure(
        SimpleNamespace(bot=bot), form_type="US_CASE", status="failed",
        reason="FORM_UNAVAILABLE", user_id=99999999,
    )
    assert bot.sent == []


@pytest.mark.parametrize("status,reason", [
    # Generic failed branch: routine classifications stay quiet.
    ("failed", "FORM_UNAVAILABLE"),
    ("failed", "LOGIN_FAILED"),
    ("failed", "FIELD_FAILURE"),
    ("failed", "UNKNOWN"),
    ("failed", None),
    # Unexpected status/reason outside the known set: quiet, not paged.
    ("failed", "SOMETHING_NEW"),
    ("weird", "SAVE_FAILURE"),
    ("success", "SAVE_FAILURE"),
    ("", ""),
])
async def test_routine_and_unknown_filing_outcomes_stay_quiet(monkeypatch, status, reason):
    import bot as bot_module
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    bot = _Bot()
    await bot_module._alert_filing_failure(
        SimpleNamespace(bot=bot), form_type="CBD", status=status,
        reason=reason, user_id=99999999,
    )
    assert bot.sent == []


@pytest.mark.parametrize("status,reason", [
    ("failed", "SAVE_FAILURE"),
    # Partial + error is an uncertain save irrespective of reason, including
    # FORM_UNAVAILABLE (form vanished after a write may have happened).
    ("partial", "FORM_UNAVAILABLE"),
    ("partial", "UNKNOWN"),
    ("partial", "LOGIN_FAILED"),
    ("partial", None),
    ("timeout", "timeout"),
    ("exception", "RuntimeError"),
    ("exception", SENTINEL),
])
async def test_uncertain_save_pages_with_exact_template(monkeypatch, caplog, status, reason):
    import bot as bot_module
    caplog.set_level(logging.DEBUG)
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    bot = _Bot()
    await bot_module._alert_filing_failure(
        SimpleNamespace(bot=bot), form_type=f"CBD_{SENTINEL}", status=status,
        reason=reason, user_id=99999999,
    )
    assert bot.sent == [(123, FILING_TEMPLATE)]
    assert "99999999" not in bot.sent[0][1]
    assert SENTINEL not in bot.sent[0][1]
    assert SENTINEL not in caplog.text


async def test_filing_uncertain_cooldown_is_category_wide(monkeypatch):
    """Distinct users/forms within 900 s collapse into one message (dedup,
    not a queue). The second incident is dropped, by design."""
    import bot as bot_module
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    bot = _Bot()
    await bot_module._alert_filing_failure(
        SimpleNamespace(bot=bot), form_type="CBD", status="failed",
        reason="SAVE_FAILURE", user_id=1,
    )
    await bot_module._alert_filing_failure(
        SimpleNamespace(bot=bot), form_type="ESLE_ASSESS", status="timeout",
        reason="timeout", user_id=2,
    )
    assert len(bot.sent) == 1


async def test_filing_helper_tolerates_missing_bot(monkeypatch):
    import bot as bot_module
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    await bot_module._alert_filing_failure(
        SimpleNamespace(), form_type="CBD", status="timeout",
        reason="timeout", user_id=1,
    )


# ---------------------------------------------------------------------------
# Real classifier fixtures: unmatched-but-routine strings must stay quiet
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("error,skipped,filled", [
    ("Browser unavailable", [], []),
    (
        "CBD on kaizen has no deterministic DOM mapping, and the browser-use "
        "fallback is off by default in this beta. Ask the operator to enable "
        "the fallback explicitly.",
        ["date_of_encounter"],
        [],
    ),
    ("Unknown platform 'horus' — no login URL configured", [], []),
    ("No deterministic filer implemented for horus", [], []),
    ("Something entirely new from a Kaizen UI change", [], []),
    (None, [], []),
])
async def test_unmatched_routine_errors_classify_unknown_and_stay_quiet(monkeypatch, error, skipped, filled):
    import bot as bot_module
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    classification = bot_module._classify_filing_failure(error, skipped, "failed", filled)
    assert classification in {"UNKNOWN", "FIELD_FAILURE"}
    if not skipped:
        assert classification == "UNKNOWN"

    bot = _Bot()
    await bot_module._alert_filing_failure(
        SimpleNamespace(bot=bot), form_type="CBD", status="failed",
        reason=classification, user_id=99999999,
    )
    assert bot.sent == []


def test_real_classifier_fixtures_for_paging_and_quiet_buckets():
    import bot as bot_module
    # Save not confirmed after fields filled -> SAVE_FAILURE (pages on failed).
    assert bot_module._classify_filing_failure(
        "Save button not found", [], "failed", ["date_of_encounter"]
    ) == "SAVE_FAILURE"
    # Same marker with nothing filled cannot be a save failure -> UNKNOWN.
    assert bot_module._classify_filing_failure(
        "Save button not found", [], "failed", []
    ) == "UNKNOWN"
    # Live-observed routine outcome.
    assert bot_module._classify_filing_failure(
        "US_CASE is not available on your Kaizen profile or curriculum right now; "
        "Kaizen redirected to https://kaizenep.com/events/list instead of opening the form. "
        "No draft was written.",
        [], "failed", [],
    ) == "FORM_UNAVAILABLE"
    assert bot_module._classify_filing_failure(
        "Login failed", [], "failed", []
    ) == "LOGIN_FAILED"
