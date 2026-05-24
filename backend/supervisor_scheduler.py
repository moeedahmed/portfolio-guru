"""Periodic supervisor poll tick.

The scheduler is the only piece of the supervisor stack that runs without
user input. To keep the rest of the bot — and trainee accounts in
particular — completely unaffected by Clinical Supervisor work in
progress, the tick is deliberately defensive:

1. **No assessor users → no-op.** The scheduler is wired into the
   :class:`telegram.ext.JobQueue` from ``bot.main`` but does nothing
   until at least one Telegram user has been classified as ``assessor``
   in ``profile_store.kaizen_role``.
2. **Assessor without credentials → skipped.** We never try to drive a
   fresh Kaizen login from a background job. The persistent Chrome
   session on the Mac Mini handles auth.
3. **CDP unavailable → graceful return.** A missing ``localhost:18800``
   logs one warning and returns. The bot keeps serving trainees.
4. **Per-user failures isolated.** A poll that raises for one user must
   not poison the others. Errors land in the log; siblings continue.

The tick takes its dependencies via keyword arguments so the
``JobQueue`` adapter and the tests can both call it without trampolines:
the queue passes a Telegram bot + the live ``connect_cdp_page``
helper; tests pass ``AsyncMock`` stand-ins.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Awaitable, Callable

import supervisor_workflow
from credentials import has_credentials
from profile_store import list_users_by_kaizen_role
from supervisor_workflow import SupervisorNotificationPayload

logger = logging.getLogger(__name__)

SUPERVISOR_STATE_DIR = Path(
    os.environ.get(
        "PORTFOLIO_GURU_SUPERVISOR_STATE_DIR",
        os.path.expanduser("~/.openclaw/data/portfolio-guru/supervisor"),
    )
)

# 5-minute cadence matches the architecture brief — Kaizen is not real-time
# critical and we'd rather pay a few extra polls than spam the supervisor.
SUPERVISOR_POLL_INTERVAL_SECONDS = 300
# Wait one interval before the first tick so a noisy startup never triggers
# a notification dispatch for a freshly-seeded state file.
SUPERVISOR_POLL_FIRST_RUN_SECONDS = 300


NotifyFn = Callable[..., Awaitable[None]]
ConnectCdpFn = Callable[[], Awaitable[object]]


def _state_path_for(telegram_user_id: int) -> Path:
    return SUPERVISOR_STATE_DIR / f"supervisor_state_{telegram_user_id}.json"


async def supervisor_poll_tick(
    *,
    bot,
    connect_cdp: ConnectCdpFn,
    notify: NotifyFn,
) -> None:
    """Run one supervisor poll tick across every assessor user.

    Args:
        bot: Telegram ``bot`` (e.g. ``application.bot`` or ``context.bot``)
            forwarded to ``notify`` for message sending.
        connect_cdp: Awaitable that yields a Playwright ``Page`` already
            attached to the persistent Chrome session. Raises when CDP
            is unavailable; the scheduler treats that as inert.
        notify: Coroutine that delivers one supervisor payload to the
            user, e.g. ``supervisor_bot.send_supervisor_notification``.
            Injected so tests can verify dispatch without rendering.
    """
    assessor_user_ids = list_users_by_kaizen_role("assessor")
    if not assessor_user_ids:
        return

    eligible_users = [uid for uid in assessor_user_ids if has_credentials(uid)]
    if not eligible_users:
        return

    try:
        page = await connect_cdp()
    except Exception as exc:
        logger.warning("Supervisor poll tick: CDP unavailable (%s)", exc)
        return

    SUPERVISOR_STATE_DIR.mkdir(parents=True, exist_ok=True)

    for user_id in eligible_users:
        state_path = _state_path_for(user_id)
        try:
            outcome = await supervisor_workflow.run_supervisor_poll(
                user_id,
                page=page,
                state_path=state_path,
                refresh_role=False,
            )
        except Exception as exc:
            logger.warning("Supervisor poll failed for user %s: %s", user_id, exc)
            continue

        if outcome.error:
            logger.info("Supervisor poll surfaced error for %s: %s", user_id, outcome.error)
            continue

        for payload in outcome.payloads:
            try:
                await notify(bot=bot, telegram_user_id=user_id, payload=payload)
            except Exception as exc:
                logger.warning(
                    "Supervisor notification dispatch failed for %s/%s: %s",
                    user_id,
                    payload.ticket_uuid,
                    exc,
                )
