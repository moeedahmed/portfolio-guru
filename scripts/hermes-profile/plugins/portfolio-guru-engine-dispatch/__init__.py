"""Hermes profile hook for Portfolio Guru WhatsApp transport.

Hermes owns the WhatsApp connection; Portfolio Guru owns the reply.  This hook
intercepts WhatsApp DMs before they enter the generic Hermes agent turn, calls
the repo-owned ``pg whatsapp-reply`` command, sends the rendered response
through the active Hermes WhatsApp adapter, then skips LLM dispatch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
PLUGIN_REASON = "portfolio-guru-engine-dispatch"
PG_TIMEOUT_SECONDS = 45


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)


def _pre_gateway_dispatch(event, gateway, **_kwargs) -> dict[str, str] | None:
    source = getattr(event, "source", None)
    if _platform_name(getattr(source, "platform", None)) != "whatsapp":
        return None
    if getattr(event, "internal", False):
        return None
    if getattr(source, "chat_type", "") != "dm":
        return None

    adapter = getattr(gateway, "adapters", {}).get(getattr(source, "platform", None))
    if adapter is None:
        LOGGER.warning("Portfolio Guru WhatsApp dispatch skipped: adapter unavailable")
        return {"action": "allow"}

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        LOGGER.warning("Portfolio Guru WhatsApp dispatch skipped: no running event loop")
        return {"action": "allow"}

    loop.create_task(_render_and_send(event, adapter))
    return {"action": "skip", "reason": PLUGIN_REASON}


async def _render_and_send(event, adapter) -> None:
    payload = _payload_from_event(event)
    try:
        response = await asyncio.to_thread(_run_pg_whatsapp_reply, payload)
    except Exception as exc:
        LOGGER.warning("Portfolio Guru WhatsApp engine call failed: %s", exc)
        return

    if response.get("status") != "ok":
        LOGGER.info(
            "Portfolio Guru WhatsApp produced no reply: status=%s disposition=%s",
            response.get("status"),
            (response.get("data") or {}).get("disposition"),
        )
        return

    rendered = str((response.get("data") or {}).get("rendered_reply") or "").strip()
    if not rendered:
        LOGGER.info("Portfolio Guru WhatsApp produced an empty reply")
        return

    source = event.source
    result = await adapter.send(
        chat_id=source.chat_id,
        content=rendered,
        reply_to=getattr(event, "message_id", None),
        metadata={"notify": True, "portfolio_guru_engine_dispatch": True},
    )
    if getattr(result, "success", False):
        LOGGER.info("Portfolio Guru WhatsApp reply sent")
    else:
        LOGGER.warning(
            "Portfolio Guru WhatsApp reply send failed: %s",
            getattr(result, "error", "unknown error"),
        )


def _run_pg_whatsapp_reply(payload: dict[str, Any]) -> dict[str, Any]:
    shim = _pg_shim_path()
    env = os.environ.copy()
    env.setdefault("PORTFOLIO_GURU_REPO", str(_repo_root()))
    completed = subprocess.run(
        [str(shim), "whatsapp-reply", "--payload", json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=PG_TIMEOUT_SECONDS,
        env=env,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise RuntimeError(stderr or f"pg exited with {completed.returncode}")
    return json.loads(completed.stdout)


def _payload_from_event(event) -> dict[str, Any]:
    source = event.source
    media = []
    urls = list(getattr(event, "media_urls", None) or [])
    types = list(getattr(event, "media_types", None) or [])
    for index, uri in enumerate(urls):
        media.append(
            {
                "kind": _media_kind(types[index] if index < len(types) else ""),
                "uri": uri,
                "mime_type": types[index] if index < len(types) else None,
            }
        )

    return {
        "channel": "whatsapp",
        "conversation_id": source.chat_id,
        "gateway_user_id": source.user_id,
        "scope": "direct",
        "text": getattr(event, "text", None),
        "media": media,
        "private": True,
    }


def _media_kind(mime_type: str | None) -> str:
    value = (mime_type or "").lower()
    if value.startswith("image/"):
        return "photo"
    if value.startswith("audio/"):
        return "voice"
    if value.startswith("video/"):
        return "video"
    return "document"


def _platform_name(platform) -> str:
    return str(getattr(platform, "value", platform) or "").lower()


def _pg_shim_path() -> Path:
    explicit = os.environ.get("PORTFOLIO_GURU_PG_SHIM")
    if explicit:
        return Path(explicit).expanduser()
    return (
        Path.home()
        / ".hermes"
        / "profiles"
        / "portfolio-guru"
        / "scripts"
        / "portfolio-guru"
        / "bin"
        / "pg"
    )


def _repo_root() -> Path:
    explicit = os.environ.get("PORTFOLIO_GURU_REPO")
    if explicit:
        return Path(explicit).expanduser()
    return Path.home() / "projects" / "portfolio-guru"
