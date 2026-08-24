"""Portfolio Guru profile plugin — hybrid Telegram, deterministic WhatsApp.

Two channels, two ownership models, one plugin.

**Telegram (the test bot).** Hermes owns the conversation. Ordinary authorised
private text is left alone: the hook takes no dispatch decision, so the generic
agent sees the message and answers in its own words. What the agent cannot
improvise, it calls: three narrowly named tools that reach the repo-owned
engine through the ``pg`` shim for fact extraction, form recommendation, the
draft preview, and the guarded mobile Kaizen login handoff.

**WhatsApp.** Unchanged. Hermes owns the transport, Portfolio Guru owns the
reply, and the hook still renders and sends it before skipping LLM dispatch.

Approval evidence
-----------------
The handoff needs proof that the *trainee* approved the *exact* draft they
were shown, not that the model believes they did. ``portfolio_draft_preview``
returns an approval phrase derived from a hash of the reviewed draft and keeps
a bounded in-memory receipt. The hook then watches raw inbound Telegram text
for that phrase — a channel the model cannot write to — and only a receipt
carrying an observed confirmation can be spent, once. Everything else fails
closed. No clinical content is logged anywhere in this file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
PLUGIN_REASON = "portfolio-guru-engine-dispatch"
PG_TIMEOUT_SECONDS = 45
PG_TOOLSET = "portfolio_guru"

# Bounded ephemeral binding state. This is deliberately the only Telegram
# state the plugin keeps: enough to tie an approved handoff to the preview the
# trainee actually read, and nothing that would make Python the conversational
# owner again.
RECEIPT_TTL_SECONDS = 15 * 60
MAX_RECEIPTS = 20

_RECEIPTS: dict[str, dict[str, dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    for name, schema, handler, emoji in _TOOL_SPECS:
        ctx.register_tool(
            name=name,
            toolset=PG_TOOLSET,
            schema=schema,
            handler=handler,
            emoji=emoji,
            description=schema["description"],
        )


# ---------------------------------------------------------------------------
# Gateway hook
# ---------------------------------------------------------------------------


def _pre_gateway_dispatch(event, gateway, **_kwargs) -> dict[str, str] | None:
    source = getattr(event, "source", None)
    platform_name = _platform_name(getattr(source, "platform", None))
    if platform_name not in {"telegram", "whatsapp"}:
        return None
    if getattr(event, "internal", False):
        return None
    if str(getattr(source, "chat_type", "")).lower() not in {"dm", "private"}:
        return None

    if platform_name == "telegram":
        # Hermes answers Telegram. The only thing this hook does is notice an
        # approval phrase in the trainee's own words before the message
        # continues to the normal agent turn. Returning None rather than
        # "allow" leaves other plugins' directives untouched.
        authorizer = getattr(gateway, "_is_user_authorized", None)
        if callable(authorizer) and authorizer(source):
            _observe_approval(event)
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


# ---------------------------------------------------------------------------
# Conversation identity — from the gateway session, never from the model
# ---------------------------------------------------------------------------


def _session_conversation() -> dict[str, str] | None:
    """Return the private Telegram conversation running this tool call.

    Reads Hermes' own task-local session context, so a tool cannot be pointed
    at another chat by anything the model writes. Returns ``None`` — and the
    tools then fail closed — on any other surface.
    """
    try:
        from gateway.session_context import get_session_env
    except Exception:
        return None

    platform = get_session_env("HERMES_SESSION_PLATFORM", "").lower()
    chat_type = get_session_env("HERMES_SESSION_CHAT_TYPE", "").lower()
    chat_id = get_session_env("HERMES_SESSION_CHAT_ID", "").strip()
    user_id = get_session_env("HERMES_SESSION_USER_ID", "").strip()
    if platform != "telegram" or chat_type not in {"dm", "private"}:
        return None
    if not chat_id or not user_id:
        return None
    return {
        "platform": platform,
        "chat_type": chat_type,
        "chat_id": chat_id,
        "user_id": user_id,
    }


def _conversation_key(chat_id: Any, user_id: Any) -> str:
    return f"{chat_id}:{user_id}"


def _payload_for(conversation: dict[str, str], text: str) -> dict[str, Any]:
    return {
        "channel": "telegram",
        "conversation_id": f"tg:{conversation['chat_id']}",
        "gateway_user_id": conversation["user_id"],
        "scope": "direct",
        "private": True,
        "text": text,
        "media": [],
    }


# ---------------------------------------------------------------------------
# Receipts and approval evidence
# ---------------------------------------------------------------------------


def _prune_receipts() -> None:
    now = time.time()
    for key in list(_RECEIPTS):
        live = {
            preview_id: receipt
            for preview_id, receipt in _RECEIPTS[key].items()
            if now - float(receipt.get("issued_at") or 0) <= RECEIPT_TTL_SECONDS
            and not receipt.get("spent")
        }
        if live:
            _RECEIPTS[key] = live
        else:
            _RECEIPTS.pop(key, None)
    total = sum(len(entries) for entries in _RECEIPTS.values())
    if total <= MAX_RECEIPTS:
        return
    ordered = sorted(
        (
            (float(receipt.get("issued_at") or 0), key, preview_id)
            for key, entries in _RECEIPTS.items()
            for preview_id, receipt in entries.items()
        )
    )
    for _issued, key, preview_id in ordered[: total - MAX_RECEIPTS]:
        _RECEIPTS.get(key, {}).pop(preview_id, None)
        if not _RECEIPTS.get(key):
            _RECEIPTS.pop(key, None)


def _store_receipt(key: str, receipt: dict[str, Any]) -> None:
    _prune_receipts()
    _RECEIPTS.setdefault(key, {})[receipt["preview_id"]] = receipt


def _observe_approval(event) -> None:
    """Record an approval phrase seen in raw inbound text for this chat."""
    text = str(getattr(event, "text", "") or "").upper()
    if not text:
        return
    source = event.source
    key = _conversation_key(
        getattr(source, "chat_id", ""), getattr(source, "user_id", "")
    )
    _prune_receipts()
    for receipt in _RECEIPTS.get(key, {}).values():
        if receipt.get("spent") or receipt.get("confirmed_at"):
            continue
        if receipt["approval_phrase"] in text:
            receipt["confirmed_at"] = time.time()


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _tool_blocked(reason: str) -> str:
    return json.dumps({"status": "blocked", "reason": reason})


def _no_conversation_reason() -> str:
    return (
        "Portfolio Guru tools only run in a private Telegram conversation with "
        "the test bot. Nothing was analysed, drafted, or filed."
    )


def _handle_case_analyze(args: dict, **_kwargs) -> str:
    conversation = _session_conversation()
    if conversation is None:
        return _tool_blocked(_no_conversation_reason())
    case_text = str((args or {}).get("case_text") or "").strip()
    if not case_text:
        return _tool_blocked("Pass the trainee's own case text as case_text.")
    return json.dumps(
        _run_pg_command("case-analyze", _payload_for(conversation, case_text))
    )


def _handle_draft_preview(args: dict, **_kwargs) -> str:
    conversation = _session_conversation()
    if conversation is None:
        return _tool_blocked(_no_conversation_reason())
    case_text = str((args or {}).get("case_text") or "").strip()
    if not case_text:
        return _tool_blocked("Pass the trainee's own case text as case_text.")

    response = _run_pg_command("draft-preview", _payload_for(conversation, case_text))
    if response.get("status") != "ok":
        return json.dumps(response)

    data = dict(response.get("data") or {})
    key = _conversation_key(conversation["chat_id"], conversation["user_id"])
    _store_receipt(
        key,
        {
            "preview_id": data["preview_id"],
            "preview_hash": data["preview_hash"],
            "approval_phrase": data["approval_phrase"],
            "case_text": case_text,
            "issued_at": time.time(),
            "confirmed_at": None,
            "spent": False,
        },
    )
    data["next_step"] = (
        "Show this preview, say nothing has been saved, and ask the trainee to "
        f"reply with {data['approval_phrase']} if they want the one-time Kaizen "
        "login. Only their own reply counts as approval."
    )
    return json.dumps({"status": "ok", "data": data})


def _handle_handoff_create(args: dict, **_kwargs) -> str:
    conversation = _session_conversation()
    if conversation is None:
        return _tool_blocked(_no_conversation_reason())
    preview_id = str((args or {}).get("preview_id") or "").strip()
    if not preview_id:
        return _tool_blocked("Pass the preview_id returned by the draft preview.")

    _prune_receipts()
    key = _conversation_key(conversation["chat_id"], conversation["user_id"])
    receipt = _RECEIPTS.get(key, {}).get(preview_id)
    if receipt is None:
        return _tool_blocked(
            "That draft preview has expired or was never reviewed here. Show a "
            "fresh preview before asking for approval."
        )
    if receipt.get("spent"):
        return _tool_blocked(
            "That approval has already been used. Nothing further was filed."
        )
    if not receipt.get("confirmed_at"):
        return _tool_blocked(
            "The trainee has not confirmed this draft yet. Ask them to reply "
            f"with {receipt['approval_phrase']} first; nothing was filed."
        )

    payload = _payload_for(conversation, receipt["case_text"])
    payload["preview_hash"] = receipt["preview_hash"]
    payload["confirmation_phrase"] = receipt["approval_phrase"]
    response = _run_pg_command("handoff-create", payload)
    if response.get("status") == "ok":
        receipt["spent"] = True
    return json.dumps(response)


_CASE_TEXT_SCHEMA = {
    "type": "string",
    "description": (
        "The trainee's own case wording, verbatim and accumulated across the "
        "conversation. Never paraphrase or add clinical detail."
    ),
}

_ANALYZE_SCHEMA = {
    "name": "portfolio_case_analyze",
    "description": (
        "Read a trainee's case text with the deterministic Portfolio Guru "
        "engine. Returns source-tied facts, the recommended RCEM form, and the "
        "questions still open. Use it before recommending any form."
    ),
    "parameters": {
        "type": "object",
        "properties": {"case_text": _CASE_TEXT_SCHEMA},
        "required": ["case_text"],
    },
}

_PREVIEW_SCHEMA = {
    "name": "portfolio_draft_preview",
    "description": (
        "Build the source-tied draft preview for a case and return the "
        "approval phrase the trainee must send. Nothing is saved to Kaizen."
    ),
    "parameters": {
        "type": "object",
        "properties": {"case_text": _CASE_TEXT_SCHEMA},
        "required": ["case_text"],
    },
}

_HANDOFF_SCHEMA = {
    "name": "portfolio_handoff_create",
    "description": (
        "Create the one-time mobile Kaizen login for a reviewed draft. Only "
        "succeeds after the trainee has sent the approval phrase themselves. "
        "The link is not proof that a draft was saved."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "preview_id": {
                "type": "string",
                "description": "preview_id returned by portfolio_draft_preview.",
            }
        },
        "required": ["preview_id"],
    },
}

_TOOL_SPECS = (
    ("portfolio_case_analyze", _ANALYZE_SCHEMA, _handle_case_analyze, "🩺"),
    ("portfolio_draft_preview", _PREVIEW_SCHEMA, _handle_draft_preview, "📋"),
    ("portfolio_handoff_create", _HANDOFF_SCHEMA, _handle_handoff_create, "🔐"),
)

TOOL_HANDLERS = {name: handler for name, _schema, handler, _emoji in _TOOL_SPECS}


# ---------------------------------------------------------------------------
# Repo bridge
# ---------------------------------------------------------------------------


def _run_pg_command(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    shim = _pg_shim_path()
    env = os.environ.copy()
    env.setdefault("PORTFOLIO_GURU_REPO", str(_repo_root()))
    try:
        completed = subprocess.run(
            [str(shim), command, "--payload-file", "-"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=PG_TIMEOUT_SECONDS,
            env=env,
        )
        return json.loads(completed.stdout)
    except Exception as exc:
        # Deliberately type-only: the payload carries clinical text.
        LOGGER.warning("Portfolio Guru %s failed: %s", command, type(exc).__name__)
        return {
            "status": "error",
            "error": (
                "The Portfolio Guru engine is unavailable. Nothing was "
                "analysed, drafted, or filed."
            ),
        }


# ---------------------------------------------------------------------------
# WhatsApp transport (unchanged)
# ---------------------------------------------------------------------------


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
