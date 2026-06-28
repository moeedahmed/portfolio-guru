from types import SimpleNamespace

from telegram.error import NetworkError


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
