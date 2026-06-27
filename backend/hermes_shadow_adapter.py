"""Repo-owned shadow-mode adapter for the Hermes test bot.

This module lets a Hermes process (or any offline harness) push a
Hermes-shaped payload through the deterministic Portfolio Guru engine
without sending Telegram messages, calling Kaizen, or touching any
credentials.  It replaces the toy local ``pg`` CLI in the Hermes profile
as the source of truth for shadow validation.

Pipeline::

    payload (dict)
      → hermes_bridge_contract.inbound_from_payload
      → channel_contract.accept_inbound   (HANDLE / REFUSE_*)
      → telegram_vnext_adapter.event_from_telegram_message  (via internal
        channel-neutral stand-in built from the InboundMessage)
      → conversational_case_engine.apply_event
      → ShadowResult(metadata, workspace)

Design invariants
-----------------
* **No network, LLM, Kaizen, Stripe, or Telegram sends.**  The module is
  importable inside a Hermes process that has never loaded
  ``python-telegram-bot``.
* **No BWS / secrets access.**  Bot tokens are owned exclusively by the
  Hermes profile (test bot token) and the live ``backend/bot.py``
  process; this adapter never reads, names, or logs either.
* **No clinical content in shadow output.**  ``ShadowResult.metadata``
  exposes disposition, state, action kinds, counts, and reason strings —
  never raw user text, fact values, or chat turns.
* **Workspace stays in-process.**  ``ShadowResult.workspace`` is the
  engine state needed for cross-turn continuity; callers must keep it in
  memory and never serialise it into a shadow log.

CLI harness
-----------
For quick offline experimentation::

    python -m hermes_shadow_adapter --payload '{"channel":"telegram",...}'

Each invocation prints only the JSON-safe metadata; the workspace stays
inside the process and is discarded when the CLI exits.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from channel_actions import ChannelAction, ChannelReply, to_telegram_button_rows
from channel_contract import (
    InboundDecision,
    InboundDisposition,
    InboundMessage,
)
from conversational_case_engine import (
    CaseWorkspace,
    EngineSnapshot,
    IngestEvent,
    apply_event,
    new_workspace,
)
from hermes_bridge_contract import inbound_from_payload
from telegram_vnext_adapter import (
    SUPPORTED_DOCUMENT_EXTENSIONS,
    event_from_telegram_message,
)
from vnext_form_recommender import (
    FormRecommendation,
    InsufficientFacts,
    recommend as recommend_form,
)


@dataclass(frozen=True)
class ShadowResult:
    """Outcome of one shadow-mode payload pass.

    ``metadata`` is JSON-safe and contains no clinical content; it is the
    only field the caller may persist to a shadow log.  ``workspace`` is
    the deterministic engine state for the next turn — it carries source
    text and is kept in-process only.
    """

    metadata: dict[str, Any]
    workspace: CaseWorkspace | None = None


def process_payload(
    payload: dict[str, Any],
    *,
    workspace: CaseWorkspace | None = None,
) -> ShadowResult:
    """Run one Hermes payload through bridge + engine.

    Returns a :class:`ShadowResult` whose ``metadata`` is safe to log and
    whose ``workspace`` is the new engine state.  No network, no Telegram
    send, no Kaizen write — only dict → dataclass → dict translation.
    """
    decision = inbound_from_payload(payload)

    if decision.disposition is not InboundDisposition.HANDLE:
        return ShadowResult(
            metadata=_refusal_metadata(decision),
            workspace=None,
        )

    assert decision.message is not None
    stand_in = _stand_in_from_inbound(decision.message)
    event = event_from_telegram_message(stand_in)

    starting = workspace if workspace is not None else new_workspace()
    snapshot = apply_event(starting, event)

    return ShadowResult(
        metadata=_handle_metadata(decision, event, snapshot),
        workspace=snapshot.workspace,
    )


# ---------------------------------------------------------------------------
# Metadata builders — JSON-safe, no clinical content
# ---------------------------------------------------------------------------


def _refusal_metadata(decision: InboundDecision) -> dict[str, Any]:
    md: dict[str, Any] = {
        "disposition": decision.disposition.value,
        "fresh_start": decision.fresh_start,
    }
    refusal = decision.refusal
    if refusal is not None:
        md["refusal"] = {
            "body": refusal.body,
            "continuation": refusal.continuation,
            "actions": [
                {"action_id": a.action_id, "label": a.label}
                for a in refusal.actions
            ],
        }
    return md


def _handle_metadata(
    decision: InboundDecision,
    event: IngestEvent,
    snapshot: EngineSnapshot,
) -> dict[str, Any]:
    workspace = snapshot.workspace
    fact_keys = sorted({fact.key for fact in workspace.facts})
    eligible = workspace.draft_eligible_facts()
    eligible_keys = sorted({fact.key for fact in eligible})
    extracted_keys = sorted({key for key, _ in event.extracted_facts})
    recommendation = _recommendation_metadata(tuple(eligible))
    form_options = _form_options(recommendation)
    form_reply = _form_reply(form_options)

    return {
        "disposition": decision.disposition.value,
        "fresh_start": decision.fresh_start,
        "turn_id": event.turn_id,
        "source_type": event.source_type.value,
        "ingest_kind": event.kind.value,
        "extracted_fact_keys": extracted_keys,
        "state": workspace.state.value,
        "fact_keys": fact_keys,
        "fact_count": len(workspace.facts),
        "eligible_fact_keys": eligible_keys,
        "eligible_fact_count": len(eligible),
        "has_unconfirmed_stricter_facts": workspace.has_unconfirmed_stricter_facts(),
        "chat_turn_count": len(workspace.chat_turns),
        "pending_clarification": workspace.pending_clarification,
        "recommendation": recommendation,
        "form_options": form_options,
        "form_reply": form_reply,
        "actions": [
            {
                "kind": action.kind.value,
                "payload": dict(action.payload),
            }
            for action in snapshot.actions
        ],
    }


def _recommendation_metadata(facts: tuple[Any, ...]) -> dict[str, Any]:
    """Return safe recommendation metadata without source values.

    The recommender may produce source-derived reasons. Shadow metadata is
    allowed to expose the form code and confidence only, so Hermes can render
    selectable options without seeing or logging clinical fact values.
    """
    if not facts:
        return {"status": "insufficient"}
    result = recommend_form(facts)
    if isinstance(result, FormRecommendation):
        return {
            "status": "recommended",
            "form_type": result.form_type,
            "confidence": result.confidence,
        }
    if isinstance(result, InsufficientFacts):
        return {"status": "insufficient"}
    return {"status": "insufficient"}


def _form_options(recommendation: dict[str, Any]) -> list[dict[str, str]]:
    if recommendation.get("status") != "recommended":
        return []
    form_type = recommendation.get("form_type")
    confidence = recommendation.get("confidence")
    if not isinstance(form_type, str):
        return []
    option = {"form_type": form_type}
    if isinstance(confidence, str):
        option["confidence"] = confidence
    return [option]


def _form_reply(form_options: list[dict[str, str]]) -> dict[str, Any] | None:
    """Build a JSON-safe, Telegram-ready reply for engine form choices.

    Hermes can use ``telegram_button_rows`` directly for Telegram inline
    keyboards. Plain-text channels can still render the same action labels as a
    numbered fallback, but Telegram should not downgrade these options to text.
    """
    if not form_options:
        return None
    actions: list[ChannelAction] = []
    for option in form_options:
        form_type = option.get("form_type")
        if not form_type:
            continue
        confidence = option.get("confidence")
        label = form_type if not confidence else f"{form_type} — {confidence} confidence"
        actions.append(
            ChannelAction(action_id=f"FORM|{form_type}", label=label)
        )
    if not actions:
        return None
    reply = ChannelReply(
        body="Portfolio Guru found a likely portfolio form.",
        continuation="Choose the engine-backed option to preview the draft. Nothing is saved to Kaizen from this test bot.",
        actions=tuple(actions),
    )
    return {
        "body": reply.body,
        "continuation": reply.continuation,
        "actions": [
            {"action_id": action.action_id, "label": action.label}
            for action in reply.actions
        ],
        "telegram_button_rows": to_telegram_button_rows(reply),
    }


# ---------------------------------------------------------------------------
# Channel-neutral stand-in for the Telegram-shaped adapter
# ---------------------------------------------------------------------------


def _stand_in_from_inbound(message: InboundMessage) -> SimpleNamespace:
    """Build a duck-typed Telegram-shaped object from an InboundMessage.

    ``telegram_vnext_adapter.event_from_telegram_message`` is already
    duck-typed (it uses ``getattr`` on a Telegram-like message); this
    helper exists so the shadow path doesn't drag in
    ``python-telegram-bot`` and so the InboundMessage media tuple maps
    cleanly onto the adapter's single-media expectation.
    """
    primary = message.media[0] if message.media else None

    voice = None
    audio = None
    photo: list[Any] = []
    document = None

    if primary is not None:
        kind = (primary.kind or "").strip().lower()
        if kind == "voice":
            voice = SimpleNamespace(file_id=primary.uri or "voice")
        elif kind == "audio":
            audio = SimpleNamespace(file_id=primary.uri or "audio")
        elif kind in {"photo", "image"}:
            photo = [SimpleNamespace(file_id=primary.uri or "photo")]
        elif kind == "document":
            document = SimpleNamespace(
                file_name=_document_filename(primary.uri, primary.mime_type),
                mime_type=primary.mime_type,
            )

    return SimpleNamespace(
        text=message.text,
        caption=primary.caption if primary is not None else None,
        voice=voice,
        audio=audio,
        photo=photo,
        document=document,
        message_id=None,
        chat=SimpleNamespace(id=message.session.conversation_id),
    )


def _document_filename(uri: str | None, mime_type: str | None) -> str:
    """Derive a filename whose extension the adapter will recognise.

    The adapter only inspects the extension via
    :data:`telegram_vnext_adapter.SUPPORTED_DOCUMENT_EXTENSIONS`; we
    therefore default to ``.pdf`` (the most common supported format) when
    we cannot extract one from the URI.
    """
    if uri:
        lowered = uri.lower()
        for ext in SUPPORTED_DOCUMENT_EXTENSIONS:
            if lowered.endswith(ext):
                return uri.rsplit("/", 1)[-1]
    if mime_type:
        mime_to_ext = {
            "application/pdf": ".pdf",
            "application/msword": ".doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
            "text/plain": ".txt",
            "text/markdown": ".md",
        }
        ext = mime_to_ext.get(mime_type.lower())
        if ext is not None:
            return f"document{ext}"
    return "document.pdf"


# ---------------------------------------------------------------------------
# CLI harness — offline only, no network, no Telegram
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes_shadow_adapter",
        description=(
            "Offline shadow-mode harness: feed a Hermes-shaped JSON "
            "payload through the deterministic Portfolio Guru engine "
            "and print the JSON-safe shadow metadata. No Telegram, "
            "Kaizen, or BWS calls are made."
        ),
    )
    parser.add_argument(
        "--payload",
        help="Inline JSON string for one payload.",
    )
    parser.add_argument(
        "--payload-file",
        help="Path to a JSON file containing one payload, or '-' for stdin.",
    )
    return parser


def _read_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload:
        return json.loads(args.payload)
    if args.payload_file:
        if args.payload_file == "-":
            return json.loads(sys.stdin.read())
        with open(args.payload_file, encoding="utf-8") as fh:
            return json.load(fh)
    raise SystemExit("provide --payload or --payload-file")


def _main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    payload = _read_payload(args)
    result = process_payload(payload)
    sys.stdout.write(json.dumps(result.metadata, indent=2, sort_keys=True))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(_main())
