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
import sys
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
    from webhook_server import _select_inbound_reply

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

    reply = asyncio.run(_select_inbound_reply(payload.get("text")))
    return {
        "status": "ok",
        "data": {
            "disposition": decision.disposition.value,
            "rendered_reply": render_numbered(reply),
            "reply_kind": "portfolio_reply",
            "kaizen_writes": False,
        },
    }


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
