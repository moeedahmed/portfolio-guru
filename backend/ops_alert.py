"""Operator alerting + liveness heartbeat for Portfolio Guru.

Everything here is best-effort and env-gated: functions no-op when the relevant
configuration is absent, so tests, CI and local runs are unaffected. The point
is that the operator finds out when the bot is unhealthy WITHOUT watching logs.

- notify_operator / notify_operator_sync: DM the operator (Telegram). Rate-
  limited per ``key`` so an error storm doesn't spam.
- heartbeat: ping an external uptime monitor (Healthchecks.io-style URL). A
  wedged-but-alive poller is then detected by the ABSENCE of pings (a dead-man
  switch launchd's crash-restart can't provide).
"""
from __future__ import annotations

import json as _json
import logging
import os
import time
import urllib.request

logger = logging.getLogger(__name__)

# Operator's Telegram chat id. Defaults to the known ADMIN id but is overridable.
OPERATOR_CHAT_ID = int(os.environ.get("PG_OPERATOR_CHAT_ID", "6912896590") or 0)
# External uptime monitor URL (e.g. https://hc-ping.com/<uuid>). Unset -> no-op.
HEARTBEAT_URL = os.environ.get("PG_HEARTBEAT_URL", "")

_ALERT_COOLDOWN_S = 300  # at most one alert per key per 5 minutes
_last_alert: dict[str, float] = {}


def _should_send(key: str, cooldown: int) -> bool:
    now = time.monotonic()
    last = _last_alert.get(key)
    if last is not None and (now - last) < cooldown:
        return False
    _last_alert[key] = now
    return True


async def notify_operator(bot, text: str, *, key: str = "generic", cooldown: int = _ALERT_COOLDOWN_S) -> None:
    """Async path — used inside the bot where a PTB ``bot`` is available."""
    if not OPERATOR_CHAT_ID or bot is None:
        return
    if not _should_send(key, cooldown):
        return
    try:
        await bot.send_message(chat_id=OPERATOR_CHAT_ID, text=f"🚨 Portfolio Guru: {text}")
    except Exception:
        logger.warning("notify_operator failed", exc_info=True)


def notify_operator_sync(text: str, *, key: str = "generic", cooldown: int = _ALERT_COOLDOWN_S) -> None:
    """Sync path — used in the FastAPI webhook server (no PTB bot in scope)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not OPERATOR_CHAT_ID or not token:
        return
    if not _should_send(key, cooldown):
        return
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=_json.dumps(
                {"chat_id": OPERATOR_CHAT_ID, "text": f"🚨 Portfolio Guru: {text}"}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        logger.warning("notify_operator_sync failed", exc_info=True)


def heartbeat(suffix: str = "") -> None:
    """Ping the external uptime monitor. No-op if PG_HEARTBEAT_URL is unset."""
    if not HEARTBEAT_URL:
        return
    try:
        urllib.request.urlopen(HEARTBEAT_URL + suffix, timeout=5)
    except Exception:
        logger.debug("heartbeat ping failed", exc_info=True)
