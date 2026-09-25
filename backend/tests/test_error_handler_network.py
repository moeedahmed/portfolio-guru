from types import SimpleNamespace

from telegram.error import NetworkError

SENTINEL = "SENTINEL-LEAK-7f3a9c"


async def test_polling_network_error_does_not_page_operator(monkeypatch):
    import bot
    import ops_alert

    sent = []

    async def fake_notify_operator(*args, **kwargs):
        sent.append((args, kwargs))

    monkeypatch.setattr(ops_alert, "notify_operator", fake_notify_operator)

    context = SimpleNamespace(error=NetworkError("httpx.ConnectError:"), bot=object())

    await bot.error_handler(None, context)

    assert sent == []


async def test_real_handler_error_still_pages_operator(monkeypatch):
    import bot
    import ops_alert

    sent = []

    async def fake_notify_operator(*args, **kwargs):
        sent.append((args, kwargs))

    monkeypatch.setattr(ops_alert, "notify_operator", fake_notify_operator)

    update = SimpleNamespace(effective_message=None)
    context = SimpleNamespace(error=RuntimeError("boom"), bot=object())

    await bot.error_handler(update, context)

    assert len(sent) == 1
    assert sent[0][1]["key"] == "handler_error"
    # The raw error text is never forwarded to the alert boundary.
    assert "boom" not in repr(sent)


async def test_handler_error_payload_is_fixed_template(monkeypatch):
    """End-to-end through the real ops_alert boundary: an exception carrying
    an identifier-like string reaches the operator only as the literal
    handler_error template."""
    import bot
    import ops_alert

    ops_alert._last_alert.clear()
    monkeypatch.setattr(ops_alert, "OPERATOR_CHAT_ID", 123)

    class _Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))

    fake_bot = _Bot()
    update = SimpleNamespace(effective_message=None)
    context = SimpleNamespace(error=RuntimeError(f"user 12345 {SENTINEL}"), bot=fake_bot)

    await bot.error_handler(update, context)

    assert fake_bot.sent == [(123, ops_alert.ALERT_TEMPLATES["handler_error"])]
    assert SENTINEL not in fake_bot.sent[0][1]
    assert "12345" not in fake_bot.sent[0][1]
