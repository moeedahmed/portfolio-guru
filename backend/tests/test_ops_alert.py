"""ops_alert: operator paging + heartbeat must be safe, gated, and rate-limited."""
import ops_alert


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


def _reset():
    ops_alert._last_alert.clear()


async def test_notify_operator_sends_then_rate_limits(monkeypatch):
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    bot = _Bot()

    await ops_alert.notify_operator(bot, "boom", key="k")
    await ops_alert.notify_operator(bot, "boom again", key="k")  # within cooldown -> suppressed

    assert len(bot.sent) == 1
    assert bot.sent[0][0] == 123
    assert "boom" in bot.sent[0][1]


async def test_notify_operator_noop_without_operator_id(monkeypatch):
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 0)
    bot = _Bot()
    await ops_alert.notify_operator(bot, "boom", key="k")
    assert bot.sent == []


async def test_distinct_keys_each_send(monkeypatch):
    _reset()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)
    bot = _Bot()
    await ops_alert.notify_operator(bot, "a", key="ka")
    await ops_alert.notify_operator(bot, "b", key="kb")
    assert len(bot.sent) == 2


def test_heartbeat_noop_without_url(monkeypatch):
    monkeypatch.setattr(ops_alert, "HEARTBEAT_URL", "")
    # Must not raise and must not attempt any network call.
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
    ops_alert.notify_operator_sync("x", key="sk")
    assert hits == []
