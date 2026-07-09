"""Repo-owned CLI for the Hermes test bot (`@portfolio_guru_test_bot`).

This module is the single entry point the Hermes profile shim calls into.
It replaces the toy ``recommend.py`` / ``draft.py`` / ``health.py`` /
``save.py`` scripts that used to live inside the Hermes profile folder.
All real intelligence lives here, in the repo, so the Hermes profile is
reduced to a thin delegating shim that can be reconstructed from
``scripts/hermes-profile/`` without losing engine behaviour.

JSON command shape
------------------
Every command prints one JSON object on stdout::

    {"status": "ok" | "blocked" | "error", "data": {...}, "error": "..."}

* ``ok`` — the command produced a useful answer in ``data``.
* ``blocked`` — the command is intentionally inert here (e.g. Kaizen
  writes are forbidden in shadow mode); ``data`` explains why and how to
  reach the real engine instead.
* ``error`` — the input was malformed; ``error`` carries a short reason
  string. ``data`` may still be present with diagnostic detail.

Commands
--------
``status``
    Engine identity, version, list of supported commands. No clinical
    surfaces; safe to call without arguments.

``shadow --payload '<json>'`` / ``--payload-file <path|->``
    Run a Hermes-shaped payload through the deterministic engine via
    :mod:`hermes_shadow_adapter`. Returns the JSON-safe shadow metadata
    (disposition, state, action kinds, fact keys) — never raw clinical
    text.

``preview --payload '<json>'`` / ``--payload-file <path|->``
    Run the same payload through the deterministic engine and return a
    user-visible local draft preview. This is the command the Hermes
    test bot calls after the user selects an engine-backed form option.
    It may include source-tied clinical content because it is rendered
    back to the user, not written to a shadow log. Kaizen writes remain
    blocked.

``recommend`` / ``draft`` / ``health``
    Returns ``blocked``. These responsibilities belong to the
    deterministic engine reached through ``shadow``; the CLI intentionally
    does not host its own heuristics so that the test bot cannot drift
    from the live engine's behaviour.

``save``
    Returns ``blocked`` with an explicit Kaizen-safety reason. Kaizen
    drafts are saved only by the live engine process after explicit user
    Approve, never from this offline CLI.

Safety
------
* No Telegram client import, no live-bot token reference, no Kaizen API
  call, no Stripe call, no BWS read. The module is importable inside a
  Hermes process that has none of those available.
* ``shadow`` output is JSON-safe metadata only; raw inbound text is never
  echoed there. ``preview`` is the deliberate user-visible exception and
  still performs no network, Telegram, Kaizen, Stripe, or BWS work.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ENGINE_VERSION = "1.0.0-hermes-test"
SUPPORTED_COMMANDS = (
    "status",
    "shadow",
    "preview",
    "whatsapp-reply",
    "recommend",
    "draft",
    "health",
    "save",
)
DEFERRED_COMMANDS = ("recommend", "draft", "health")


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def cmd_status() -> dict[str, Any]:
    return {
        "status": "ok",
        "data": {
            "engine": "portfolio-guru repo-owned engine",
            "engine_version": ENGINE_VERSION,
            "supported_commands": list(SUPPORTED_COMMANDS),
            "shadow_only": True,
            "kaizen_writes": False,
            "guide": "docs/hermes/INTEGRATION_GUIDE.md",
        },
    }


def cmd_shadow(
    *, payload_json: str | None = None, payload_path: str | None = None
) -> dict[str, Any]:
    try:
        payload = _load_payload(
            command="shadow",
            payload_json=payload_json,
            payload_path=payload_path,
        )
    except _PayloadError as exc:
        return {"status": "error", "error": str(exc)}

    # Lazy import keeps `status` cheap and avoids loading the engine
    # graph when Hermes is only probing the CLI.
    from hermes_shadow_adapter import process_payload

    try:
        result = process_payload(payload)
    except ValueError as exc:
        return {
            "status": "error",
            "error": f"invalid Hermes payload: {exc}",
        }
    return {"status": "ok", "data": result.metadata}


def cmd_preview(
    *, payload_json: str | None = None, payload_path: str | None = None
) -> dict[str, Any]:
    """Return a user-visible, source-tied local preview for the Hermes bot.

    This intentionally differs from ``shadow``: ``shadow`` is safe for logs
    and never echoes clinical text; ``preview`` is for the reply sent back to
    the same user who supplied the case. It still performs no external writes
    and does not touch Kaizen.
    """
    try:
        payload = _load_payload(
            command="preview",
            payload_json=payload_json,
            payload_path=payload_path,
        )
    except _PayloadError as exc:
        return {"status": "error", "error": str(exc)}

    from hermes_shadow_adapter import process_payload
    from vnext_draft_preview import build_draft_preview
    from vnext_form_recommender import FormRecommendation, recommend

    try:
        result = process_payload(payload)
    except ValueError as exc:
        return {
            "status": "error",
            "error": f"invalid Hermes payload: {exc}",
        }

    metadata = result.metadata
    if metadata.get("disposition") != "handle" or result.workspace is None:
        return {
            "status": "blocked",
            "data": {
                "reason": "payload was not accepted for draft preview",
                "disposition": metadata.get("disposition"),
                "kaizen_writes": False,
            },
        }

    facts = tuple(result.workspace.draft_eligible_facts())
    recommendation = recommend(facts)
    preview_text = build_draft_preview(facts, recommendation)
    data: dict[str, Any] = {
        "preview_text": preview_text,
        "fact_count": len(facts),
        "kaizen_writes": False,
        "source": "vnext_draft_preview",
    }
    if isinstance(recommendation, FormRecommendation):
        data["form_type"] = recommendation.form_type
        data["confidence"] = recommendation.confidence
    return {"status": "ok", "data": data}


def cmd_whatsapp_reply(
    *, payload_json: str | None = None, payload_path: str | None = None
) -> dict[str, Any]:
    """Render the Portfolio Guru reply Hermes should send on WhatsApp.

    This is the deterministic runtime path for the Hermes WhatsApp transport:
    validate the channel-neutral inbound contract, select the same first-turn
    reply the HTTP bridge would use, and return rendered WhatsApp-safe text.
    It performs no WhatsApp send and no Kaizen write.
    """
    try:
        payload = _load_payload(
            command="whatsapp-reply",
            payload_json=payload_json,
            payload_path=payload_path,
        )
    except _PayloadError as exc:
        return {"status": "error", "error": str(exc)}

    from channel_actions import render_numbered
    from hermes_bridge_contract import inbound_from_payload, serialise_decision

    try:
        decision = inbound_from_payload(payload)
    except ValueError as exc:
        return {
            "status": "error",
            "error": f"invalid Hermes payload: {exc}",
        }

    if decision.refusal is not None:
        return {
            "status": "ok",
            "data": {
                "disposition": decision.disposition.value,
                "rendered_reply": render_numbered(decision.refusal),
                "reply_kind": "refusal",
                "kaizen_writes": False,
            },
        }

    if decision.message is None:
        return {
            "status": "blocked",
            "data": {
                **serialise_decision(decision),
                "reason": "payload produced no user-visible reply",
                "kaizen_writes": False,
            },
        }

    reply = asyncio.run(_select_whatsapp_reply(payload))
    return {
        "status": "ok",
        "data": {
            "disposition": decision.disposition.value,
            "rendered_reply": render_numbered(reply),
            "reply_kind": "portfolio_reply",
            "kaizen_writes": False,
        },
    }


def _state_path() -> Path:
    explicit = os.environ.get("PORTFOLIO_GURU_WHATSAPP_STATE_PATH")
    if explicit:
        return Path(explicit).expanduser()
    return (
        Path.home()
        / ".openclaw"
        / "data"
        / "portfolio-guru"
        / "hermes-whatsapp-workflows.json"
    )


def _state_ttl_seconds() -> int:
    raw = os.environ.get("PORTFOLIO_GURU_WHATSAPP_STATE_TTL_SECONDS", "").strip()
    if not raw:
        return 60 * 60
    try:
        return max(60, int(raw))
    except ValueError:
        return 60 * 60


def _load_whatsapp_state() -> dict[str, Any]:
    path = _state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    conversations = data.get("conversations")
    if not isinstance(conversations, dict):
        conversations = {}
    now = time.time()
    ttl = _state_ttl_seconds()
    kept = {
        key: value
        for key, value in conversations.items()
        if isinstance(value, dict)
        and now - float(value.get("updated_at") or value.get("created_at") or 0) <= ttl
    }
    return {"conversations": kept}


def _save_whatsapp_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _conversation_key(payload: dict[str, Any]) -> str:
    return str(payload.get("conversation_id") or "").strip()


def _conversation_record(state: dict[str, Any], key: str) -> dict[str, Any] | None:
    conversations = state.setdefault("conversations", {})
    record = conversations.get(key)
    return record if isinstance(record, dict) else None


def _ensure_conversation_record(state: dict[str, Any], key: str) -> dict[str, Any]:
    conversations = state.setdefault("conversations", {})
    now = time.time()
    record = conversations.get(key)
    if not isinstance(record, dict):
        record = {"created_at": now, "updated_at": now, "parts": []}
        conversations[key] = record
    record["updated_at"] = now
    record.setdefault("parts", [])
    return record


def _append_case_part(record: dict[str, Any], text: str | None) -> None:
    cleaned = (text or "").strip()
    if not cleaned:
        return
    parts = record.setdefault("parts", [])
    if isinstance(parts, list):
        parts.append(cleaned)
    record["updated_at"] = time.time()


def _combined_case_text(record: dict[str, Any] | None) -> str:
    if not record:
        return ""
    parts = record.get("parts")
    if not isinstance(parts, list):
        return ""
    return "\n\n".join(str(part).strip() for part in parts if str(part).strip())


def _remember_actions(record: dict[str, Any] | None, reply) -> None:
    if record is None:
        return
    actions = getattr(reply, "actions", ()) or ()
    if actions:
        record["last_actions"] = [
            {"action_id": action.action_id, "label": action.label}
            for action in actions
        ]
    else:
        record.pop("last_actions", None)
    record["updated_at"] = time.time()


def _resolve_stored_action(record: dict[str, Any] | None, text: str | None) -> str | None:
    if not record:
        return None
    actions = record.get("last_actions")
    if not isinstance(actions, list) or not actions:
        return None
    from channel_actions import ChannelAction, ChannelReply, resolve_numbered_choice

    reply = ChannelReply(
        body="",
        actions=tuple(
            ChannelAction(
                action_id=str(action.get("action_id") or ""),
                label=str(action.get("label") or ""),
            )
            for action in actions
            if action.get("action_id") and action.get("label")
        ),
    )
    return resolve_numbered_choice(reply, text)


def _is_unmatched_plain_choice(text: str | None) -> bool:
    stripped = (text or "").strip()
    return bool(stripped) and stripped.isdigit()


def _looks_like_case_start_request(text: str | None) -> bool:
    lowered = " ".join((text or "").strip().lower().split())
    if not lowered:
        return False
    starters = (
        "start a case",
        "write up a case",
        "write this up",
        "draft a case",
        "draft this case",
        "file a case",
        "new case",
    )
    return any(starter in lowered for starter in starters)


async def _answer_whatsapp_side_question(text: str) -> str:
    from channel_reply_policy import select_deterministic_reply
    from message_policy import render_message

    reply = select_deterministic_reply(text, include_first_contact=False)
    if reply is None:
        return render_message("capability_overview")
    return reply.full_text()


def _make_gathering_captured_reply():
    from channel_actions import ChannelReply
    from conversation_supervisor import DRAFT_NOW_ACTION
    from message_policy import render_message

    return ChannelReply(
        body=render_message("gathering_captured"),
        actions=(DRAFT_NOW_ACTION,),
    )


def _mark_case_recommended(record: dict[str, Any] | None, reply) -> None:
    if record is None:
        return
    body = getattr(reply, "body", "") or ""
    if "recommended WPBA form is" not in body:
        return
    record["stage"] = "recommended"
    lowered = body.lower()
    if "case-based discussion" in lowered or "(cbd)" in lowered:
        record["recommended_form_type"] = "CBD"
    record["updated_at"] = time.time()


def _looks_like_recommended_form_confirmation(
    text: str | None,
    record: dict[str, Any] | None,
) -> bool:
    if not record:
        return False
    normalised = " ".join((text or "").strip().lower().split())
    if not normalised:
        return False
    aliases = {
        "cbd": {"cbd", "case based discussion", "case-based discussion"},
    }
    form_type = str(record.get("recommended_form_type") or "").lower()
    if not form_type and _combined_case_text(record):
        return any(normalised in values for values in aliases.values())
    if record.get("stage") != "recommended":
        return False
    return normalised in aliases.get(form_type, {form_type})


def _make_local_draft_preview_reply(record: dict[str, Any] | None):
    from channel_actions import ChannelReply
    from vnext_draft_preview import build_draft_preview
    from vnext_form_recommender import recommend
    from vnext_text_extractor import extract_text_facts

    case_text = _combined_case_text(record)
    facts = extract_text_facts(case_text or "")
    case_facts = tuple(
        _case_fact_from_text(key, value, turn_index=index)
        for index, (key, value) in enumerate(facts, start=1)
    )
    preview = build_draft_preview(case_facts, recommend(case_facts))
    if record is not None:
        record["stage"] = "previewed"
        record["updated_at"] = time.time()
    return ChannelReply(
        body=(
            f"{preview}\n\n"
            "WhatsApp preview only: nothing has been saved to Kaizen. "
            "Use the Telegram bot for the full approval-gated Kaizen draft save."
        )
    )


async def _make_finish_reply(record: dict[str, Any] | None):
    from channel_actions import ChannelReply

    case_text = _combined_case_text(record)
    if not case_text.strip():
        return ChannelReply(
            body=(
                "📋 Case details needed\n\n"
                "I do not have a case captured for that option yet. Send "
                "anonymised case details, then choose Draft now."
            )
        )
    if record is not None and record.get("stage") == "recommended":
        return _make_local_draft_preview_reply(record)
    reply = _make_local_case_insight_reply(case_text)
    _mark_case_recommended(record, reply)
    return reply


def _make_local_case_insight_reply(text: str):
    """Return a source-tied WhatsApp case recommendation without network I/O.

    Hermes invokes this CLI in a fresh process for each WhatsApp message. That
    path must not depend on the live webhook's model-backed extractor
    credentials, and it must not touch Kaizen or the refined draft generator.
    """
    from channel_actions import ChannelReply
    from form_display import public_form_name
    from vnext_form_recommender import FormRecommendation, recommend
    from vnext_text_extractor import extract_text_facts
    from webhook_server import _make_initial_gathering_reply

    facts = extract_text_facts(text or "")
    if not facts:
        return _make_initial_gathering_reply()

    case_facts = tuple(
        _case_fact_from_text(key, value, turn_index=index)
        for index, (key, value) in enumerate(facts, start=1)
    )
    recommendation = recommend(case_facts)
    if not isinstance(recommendation, FormRecommendation):
        return ChannelReply(
            body=(
                "📋 I have started the case, but I need one more detail before "
                "choosing the best WPBA form.\n\n"
                f"{recommendation.missing_prompt}\n\n"
                "Reply with that detail and I'll prepare your portfolio entry."
            )
        )

    form_label = public_form_name(recommendation.form_type) or recommendation.form_type
    body = (
        f"Based on your description, the recommended WPBA form is:\n"
        f"{form_label} ({recommendation.form_type})\n\n"
        f"{recommendation.reason}\n\n"
        "To complete your draft I need a few details:\n"
        "- Date of the activity (dd/mm/yyyy)\n"
        "- Your training grade and current placement\n"
        "- Your specific role or contribution\n"
        "- Supervisor / assessor name and grade\n"
        "- Key metrics or outcomes (if applicable)\n\n"
        "Reply with the above and I'll prepare your portfolio entry."
    )
    from conversation_supervisor import DRAFT_NOW_ACTION

    return ChannelReply(body=body, actions=(DRAFT_NOW_ACTION,))


def _case_fact_from_text(key: str, value: str, *, turn_index: int):
    from conversational_case_engine import CaseFact, SourceType

    return CaseFact(
        key=key,
        value=value,
        source_type=SourceType.TEXT,
        source_turn_id=f"hermes-whatsapp-{turn_index}",
    )


async def _select_active_whatsapp_reply(
    text: str | None,
    *,
    record: dict[str, Any],
):
    from conversation_supervisor import GatheringTurnKind, decide_gathering_turn

    if _is_unmatched_plain_choice(text):
        from channel_actions import ChannelReply

        if _combined_case_text(record):
            return await _make_finish_reply(record)
        return ChannelReply(
            body=(
                "That option is no longer available.\n\n"
                "Send anonymised case details, or type done when you are ready "
                "for me to check the best-fit form."
            )
        )

    if _looks_like_recommended_form_confirmation(text, record):
        return _make_local_draft_preview_reply(record)

    decision = await decide_gathering_turn(
        text,
        answer_question=_answer_whatsapp_side_question,
    )
    if decision.kind is GatheringTurnKind.FINISH_CASE:
        return await _make_finish_reply(record)
    if decision.add_to_case:
        _append_case_part(record, text)
        if record.get("stage") == "recommended":
            return _make_local_draft_preview_reply(record)
        return _make_gathering_captured_reply()
    assert decision.reply is not None
    return decision.reply


async def _select_whatsapp_reply(payload: dict[str, Any]):
    """Pick a WhatsApp reply without collapsing every short turn to intake.

    The HTTP inbound bridge still owns first-contact/case-intake replies.
    WhatsApp adds one extra layer: short user questions must use the same
    deterministic conversational intent contract as Telegram, otherwise every
    non-case message looks like "please describe the clinical case".
    """
    from channel_reply_policy import select_deterministic_reply
    from conversation_supervisor import DRAFT_NOW_ACTION
    from webhook_server import (
        _has_rich_case_content,
        _make_initial_gathering_reply,
        _make_resolved_action_reply,
    )

    text = payload.get("text")
    key = _conversation_key(payload)
    state = _load_whatsapp_state()
    record = _conversation_record(state, key)
    action_id = _resolve_stored_action(record, text)
    if action_id == DRAFT_NOW_ACTION.action_id:
        reply = await _make_finish_reply(record)
        _remember_actions(record, reply)
        _save_whatsapp_state(state)
        return reply
    if action_id is not None:
        reply = _make_resolved_action_reply(action_id)
        _remember_actions(record, reply)
        _save_whatsapp_state(state)
        return reply

    if record is not None:
        reply = await _select_active_whatsapp_reply(text, record=record)
        _remember_actions(record, reply)
        _save_whatsapp_state(state)
        return reply

    reply = select_deterministic_reply(text, include_first_contact=True)
    if reply is not None and not _looks_like_case_start_request(text):
        if key:
            record = _ensure_conversation_record(state, key)
            _remember_actions(record, reply)
            _save_whatsapp_state(state)
        return reply

    record = _ensure_conversation_record(state, key) if key else None
    if _has_rich_case_content(text):
        if record is not None:
            _append_case_part(record, text)
        reply = _make_local_case_insight_reply(text or "")
        _mark_case_recommended(record, reply)
        _remember_actions(record, reply)
        _save_whatsapp_state(state)
        return reply

    reply = _make_initial_gathering_reply()
    _remember_actions(record, reply)
    _save_whatsapp_state(state)
    return reply


def cmd_deferred(name: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "data": {
            "command": name,
            "reason": (
                f"'{name}' is owned by the deterministic engine. Send a "
                "Hermes-shaped payload through the 'shadow' command to "
                "receive engine actions instead of running a separate "
                "heuristic here."
            ),
            "route_via": "shadow",
            "guide": "docs/hermes/INTEGRATION_GUIDE.md",
        },
    }


def cmd_save() -> dict[str, Any]:
    return {
        "status": "blocked",
        "data": {
            "command": "save",
            "reason": (
                "Kaizen draft writes are never performed by the Hermes "
                "test path. The live engine (backend/bot.py + "
                "backend/filer_router.py) is the only surface that "
                "writes to Kaizen, and only after an explicit user "
                "Approve in that process."
            ),
            "kaizen_writes": False,
            "guide": "docs/hermes/INTEGRATION_GUIDE.md",
        },
    }


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


class _PayloadError(ValueError):
    pass


def _load_payload(
    *,
    command: str,
    payload_json: str | None,
    payload_path: str | None,
) -> dict[str, Any]:
    if payload_json is None and payload_path is None:
        raise _PayloadError(
            f"{command} requires --payload <json> or --payload-file <path|->"
        )
    try:
        if payload_path is not None:
            if payload_path == "-":
                payload = json.loads(sys.stdin.read())
            else:
                with open(payload_path, encoding="utf-8") as fh:
                    payload = json.load(fh)
        else:
            assert payload_json is not None
            payload = json.loads(payload_json)
    except (OSError, json.JSONDecodeError) as exc:
        raise _PayloadError(
            f"could not load payload: {exc.__class__.__name__}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise _PayloadError("payload must be a JSON object")
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes_pg_cli",
        description=(
            "Repo-owned offline CLI for the Hermes test bot. Returns "
            "JSON for every command. Never sends Telegram messages, "
            "calls Kaizen, or reads BWS secrets."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Engine identity + supported commands.")

    shadow = sub.add_parser(
        "shadow",
        help="Run a Hermes-shaped payload through the engine.",
    )
    shadow.add_argument("--payload", help="Inline JSON payload string.")
    shadow.add_argument(
        "--payload-file",
        help="Path to a JSON payload file, or '-' for stdin.",
    )

    preview = sub.add_parser(
        "preview",
        help="Build a user-visible local draft preview from a Hermes payload.",
    )
    preview.add_argument("--payload", help="Inline JSON payload string.")
    preview.add_argument(
        "--payload-file",
        help="Path to a JSON payload file, or '-' for stdin.",
    )

    whatsapp_reply = sub.add_parser(
        "whatsapp-reply",
        help="Render the Portfolio Guru reply for Hermes WhatsApp transport.",
    )
    whatsapp_reply.add_argument("--payload", help="Inline JSON payload string.")
    whatsapp_reply.add_argument(
        "--payload-file",
        help="Path to a JSON payload file, or '-' for stdin.",
    )

    for name in DEFERRED_COMMANDS:
        sub.add_parser(
            name,
            help=f"Deferred to the engine via 'shadow' (returns blocked).",
        )

    sub.add_parser("save", help="Always blocked — Kaizen writes happen in the live engine only.")

    return parser


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "status":
        return cmd_status()
    if args.command == "shadow":
        return cmd_shadow(
            payload_json=args.payload,
            payload_path=args.payload_file,
        )
    if args.command == "preview":
        return cmd_preview(
            payload_json=args.payload,
            payload_path=args.payload_file,
        )
    if args.command == "whatsapp-reply":
        return cmd_whatsapp_reply(
            payload_json=args.payload,
            payload_path=args.payload_file,
        )
    if args.command in DEFERRED_COMMANDS:
        return cmd_deferred(args.command)
    if args.command == "save":
        return cmd_save()
    return {
        "status": "error",
        "error": f"unknown command: {args.command!r}",
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    response = _dispatch(args)
    sys.stdout.write(json.dumps(response, indent=2, sort_keys=True))
    sys.stdout.write("\n")
    if response.get("status") == "error":
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
