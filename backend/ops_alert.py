"""Operator alerting + liveness heartbeat for Portfolio Guru.

Everything here is best-effort and env-gated: functions no-op when the relevant
configuration is absent, so tests, CI and local runs are unaffected. The point
is that the operator finds out when the bot is unhealthy WITHOUT watching logs.

- notify_operator / notify_operator_sync: DM the operator (Telegram). Rate-
  limited per ``key`` so an error storm doesn't spam. The outbound text is
  NEVER caller-supplied: ``key`` selects one fixed template from
  ``ALERT_TEMPLATES`` and any other key is suppressed. This is a fail-closed
  privacy boundary — no identifiers, error text or form details can reach the
  operator DM even if a future caller passes them.
- heartbeat: ping an external uptime monitor (Healthchecks.io-style URL). A
  wedged-but-alive poller is then detected by the ABSENCE of pings (a dead-man
  switch launchd's crash-restart can't provide).

Scope note: no retries, aggregation, cooldown persistence or events beyond the
fixed set below. Cooldown is per key, so distinct incidents inside one window
collapse into a single message — this is a dedup, not a queue or incident list.
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

_ALERT_LABEL = "Portfolio Guru — support alert"
_PAYMENT_WEBHOOK_TEXT = (
    f"{_ALERT_LABEL}\n\n"
    "A payment webhook could not be processed. Check the provider dashboard."
)

# The complete, fixed set of operator events. Adding an event means adding a
# literal here; there is no formatter and no free-text path.
ALERT_TEMPLATES: dict[str, str] = {
    "filing_uncertain": (
        f"{_ALERT_LABEL}\n\n"
        "A Kaizen draft save could not be confirmed. Check the filing report "
        "and verify the draft in Kaizen before retrying."
    ),
    "handler_error": (
        f"{_ALERT_LABEL}\n\n"
        "An unexpected bot error needs investigation. Check the service logs; "
        "do not assume a filing completed."
    ),
    "webhook_fail": _PAYMENT_WEBHOOK_TEXT,
    "webhook_unhandled": _PAYMENT_WEBHOOK_TEXT,
}

# Fixed log line for unknown keys. The key itself is deliberately NOT logged:
# an adversarial or buggy caller could put an identifier in it.
_UNKNOWN_EVENT_LOG = "Operator notification suppressed: unknown event"


def render_alert(key: str) -> str | None:
    """Return the fixed template for ``key`` or ``None`` when unknown."""
    return ALERT_TEMPLATES.get(key)


def _should_send(key: str, cooldown: int) -> bool:
    now = time.monotonic()
    last = _last_alert.get(key)
    if last is not None and (now - last) < cooldown:
        return False
    _last_alert[key] = now
    return True


async def notify_operator(bot, text: str = "", *, key: str = "generic", cooldown: int = _ALERT_COOLDOWN_S) -> None:
    """Async path — used inside the bot where a PTB ``bot`` is available.

    ``text`` is accepted for call-site compatibility and ignored; the payload
    is always ``ALERT_TEMPLATES[key]``. Unknown keys are suppressed.
    """
    message = render_alert(key)
    if message is None:
        logger.warning(_UNKNOWN_EVENT_LOG)
        return
    if not OPERATOR_CHAT_ID or bot is None:
        return
    if not _should_send(key, cooldown):
        return
    try:
        await bot.send_message(chat_id=OPERATOR_CHAT_ID, text=message)
    except Exception as exc:
        # Class name only: transport errors can echo the request payload.
        logger.warning("notify_operator failed: %s", type(exc).__name__)


def notify_operator_sync(text: str = "", *, key: str = "generic", cooldown: int = _ALERT_COOLDOWN_S) -> None:
    """Sync path — used in the FastAPI webhook server (no PTB bot in scope).

    Same contract as :func:`notify_operator`: ``text`` is ignored and only a
    known ``key`` can send.
    """
    message = render_alert(key)
    if message is None:
        logger.warning(_UNKNOWN_EVENT_LOG)
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not OPERATOR_CHAT_ID or not token:
        return
    if not _should_send(key, cooldown):
        return
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=_json.dumps(
                {"chat_id": OPERATOR_CHAT_ID, "text": message}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        # Class name only: urllib errors can include the URL (bot token).
        logger.warning("notify_operator_sync failed: %s", type(exc).__name__)


def ping_check(url: str, suffix: str = "") -> None:
    """Ping one Healthchecks.io check. No-op when the URL is unset.

    Monitoring must never break the job it monitors, so every failure here is
    swallowed. Jobs other than the bot's own liveness heartbeat have their own
    check URL, which is why this takes the URL rather than reading a global.
    """
    if not url:
        return
    try:
        urllib.request.urlopen(url + suffix, timeout=5)
    except Exception:
        logger.debug("healthcheck ping failed", exc_info=True)


def heartbeat(suffix: str = "") -> None:
    """Ping the external uptime monitor. No-op if PG_HEARTBEAT_URL is unset."""
    ping_check(HEARTBEAT_URL, suffix)
