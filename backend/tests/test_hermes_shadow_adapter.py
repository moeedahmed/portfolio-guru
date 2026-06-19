"""Repo-owned Hermes shadow adapter — focused contract tests.

The adapter replaces the Hermes-profile local mock ``pg`` CLI with a
repo-owned shadow-mode runner that:

* accepts a Hermes-shaped payload dict;
* routes through ``hermes_bridge_contract.inbound_from_payload``;
* converts HANDLE decisions into a vNext ``IngestEvent`` and applies
  it through ``conversational_case_engine.apply_event``;
* returns a JSON-safe metadata dict suitable for shadow logging plus
  the workspace for cross-turn continuity (workspace stays in-process
  and is never serialised into the log payload).

These tests pin the shadow-mode invariants documented in
``docs/hermes/INTEGRATION_GUIDE.md``: no Telegram sends, no Kaizen
writes, no live token references, no clinical content in shadow output.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"


def _valid_payload(**overrides) -> dict:
    base = {
        "channel": "telegram",
        "conversation_id": "tg:chat:42",
        "gateway_user_id": "hermes-shadow-user",
        "scope": "direct",
        "text": "62M chest pain in resus, RSI with the consultant, possible STEMI",
        "media": [],
        "private": True,
    }
    base.update(overrides)
    return base


# --- HANDLE: clinical case text -------------------------------------------


def test_clinical_case_text_returns_handle_with_case_action_metadata():
    from hermes_shadow_adapter import process_payload

    result = process_payload(_valid_payload())

    assert result.metadata["disposition"] == "handle"
    assert result.metadata["ingest_kind"] == "possible_case_detail"
    assert result.metadata["source_type"] == "text"
    # The engine must surface at least one case-handling action.
    action_kinds = {a["kind"] for a in result.metadata["actions"]}
    assert action_kinds & {
        "ack_case_details",
        "request_case_confirmation",
        "offer_draft",
    }
    # Workspace is returned for continuity but never carries clinical text in metadata.
    assert result.workspace is not None
    assert result.workspace.facts  # facts captured in-process
    assert "facts" not in result.metadata
    assert "chat_turns" not in result.metadata


def test_clinical_case_with_rich_text_reaches_offer_draft():
    from hermes_shadow_adapter import process_payload

    payload = _valid_payload(
        text=(
            "Had a difficult airway case with a 62M in resus, "
            "managed RSI with the consultant after intubation."
        )
    )
    result = process_payload(payload)

    assert result.metadata["disposition"] == "handle"
    assert result.metadata["state"] == "draft_ready"
    assert any(a["kind"] == "offer_draft" for a in result.metadata["actions"])
    # Only fact keys exposed — never values.
    assert "fact_keys" in result.metadata
    assert isinstance(result.metadata["fact_keys"], list)
    for key in result.metadata["fact_keys"]:
        assert isinstance(key, str)


# --- HANDLE: portfolio question ------------------------------------------


def test_portfolio_question_routes_to_answer_chat():
    from hermes_shadow_adapter import process_payload

    payload = _valid_payload(
        text="What forms would this case support for my portfolio?"
    )
    result = process_payload(payload)

    assert result.metadata["disposition"] == "handle"
    assert result.metadata["ingest_kind"] == "side_question"
    action_kinds = [a["kind"] for a in result.metadata["actions"]]
    assert "answer_chat" in action_kinds


# --- REFUSE: empty / group -----------------------------------------------


def test_empty_payload_returns_refuse_empty_with_safe_metadata():
    from hermes_shadow_adapter import process_payload

    result = process_payload(_valid_payload(text=None, media=[]))

    assert result.metadata["disposition"] == "refuse_empty"
    # No engine state on a refusal — workspace untouched.
    assert result.workspace is None
    assert "actions" not in result.metadata
    assert "state" not in result.metadata


def test_group_scope_refusal_does_not_echo_inbound_content():
    from hermes_shadow_adapter import process_payload

    secret_marker = "ZZSECRETCASEMARKERZZ chest pain"
    result = process_payload(_valid_payload(scope="group", text=secret_marker))

    assert result.metadata["disposition"] == "refuse_group"
    # Refusal body is the static contract copy — never the inbound text.
    blob = json.dumps(result.metadata)
    assert secret_marker not in blob
    assert "ZZSECRETCASEMARKERZZ" not in blob
    assert "refusal" in result.metadata
    assert result.metadata["refusal"]["body"]


# --- HANDLE: save-before-draft -------------------------------------------


def test_file_as_cbd_before_draft_returns_draft_not_ready():
    from hermes_shadow_adapter import process_payload

    payload = _valid_payload(text="File this as a CBD")
    result = process_payload(payload)

    assert result.metadata["disposition"] == "handle"
    assert result.metadata["ingest_kind"] == "request_save"
    action_kinds = [a["kind"] for a in result.metadata["actions"]]
    assert "draft_not_ready" in action_kinds
    # The blocking reason is exposed as safe metadata (no clinical content).
    not_ready = next(
        a for a in result.metadata["actions"] if a["kind"] == "draft_not_ready"
    )
    assert "reason" in not_ready["payload"]


# --- Safety: no raw clinical text in metadata ----------------------------


def test_shadow_metadata_never_contains_raw_clinical_text():
    from hermes_shadow_adapter import process_payload

    marker = "UNIQUEMARKERWHEEL"
    payload = _valid_payload(
        text=f"{marker} 62M chest pain in resus, RSI with the consultant"
    )
    result = process_payload(payload)

    blob = json.dumps(result.metadata)
    assert marker not in blob
    assert "chest pain" not in blob
    assert "consultant" not in blob


# --- Cross-turn continuity -----------------------------------------------


def test_workspace_continuity_across_two_turns():
    from hermes_shadow_adapter import process_payload

    first = process_payload(_valid_payload(text="62M presented with chest pain"))
    assert first.workspace is not None

    second_payload = _valid_payload(
        conversation_id="tg:chat:42",
        text="Diagnosis was STEMI, taken to cath lab",
    )
    second = process_payload(second_payload, workspace=first.workspace)

    assert second.workspace is not None
    # Facts accumulate across turns rather than being thrown away.
    assert len(second.workspace.facts) >= len(first.workspace.facts)


# --- Media payloads ------------------------------------------------------


def test_image_only_payload_marks_image_source_with_unconfirmed_stricter():
    from hermes_shadow_adapter import process_payload

    payload = _valid_payload(
        text=None,
        media=[{"kind": "photo", "uri": "gw://blob/photo-1"}],
    )
    result = process_payload(payload)

    assert result.metadata["disposition"] == "handle"
    assert result.metadata["source_type"] == "image"
    # Image text content is the placeholder — not clinical.
    blob = json.dumps(result.metadata)
    assert "62M" not in blob
    assert "chest pain" not in blob


def test_voice_only_payload_marks_voice_source_as_side_question():
    from hermes_shadow_adapter import process_payload

    payload = _valid_payload(
        text=None,
        media=[{"kind": "voice", "uri": "gw://blob/voice-1"}],
    )
    result = process_payload(payload)

    assert result.metadata["disposition"] == "handle"
    assert result.metadata["source_type"] == "voice"
    assert result.metadata["ingest_kind"] == "side_question"


# --- Import-safety: no telegram / secrets in the adapter ----------------


def test_shadow_adapter_imports_without_telegram_or_secrets():
    """The shadow adapter must be safe to load inside a Hermes process
    that has neither python-telegram-bot nor BWS available."""
    forbidden = ("telegram", "bws", "boto3", "keyring", "bitwarden")
    check = "; ".join(
        f"sys.exit(1) if {m!r} in sys.modules else None" for m in forbidden
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys, hermes_shadow_adapter; {check}",
        ],
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"hermes_shadow_adapter imported a forbidden module:\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_shadow_adapter_does_not_send_telegram_or_call_kaizen():
    """Static-source guard: the adapter file must not import or reference
    live Telegram send / Kaizen filer surfaces."""
    src = (BACKEND_DIR / "hermes_shadow_adapter.py").read_text(encoding="utf-8")
    # Never import the live bot, the filer, the Stripe webhook, or telegram client.
    forbidden_imports = (
        "from bot ",
        "import bot",
        "from filer ",
        "import filer",
        "from browser_filer ",
        "import browser_filer",
        "from filer_router ",
        "import filer_router",
        "from telegram ",
        "import telegram",
    )
    for needle in forbidden_imports:
        assert needle not in src, (
            f"hermes_shadow_adapter.py must not reference {needle!r}"
        )
    # Never name the live beta bot token in adapter code.
    assert "PORTFOLIO_GURU_TELEGRAM_BOT_TOKEN" not in src
