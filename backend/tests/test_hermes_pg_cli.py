"""Tests for the repo-owned Hermes test bot CLI (``backend/hermes_pg_cli.py``).

These tests pin the contract the Hermes profile shim relies on:

* JSON-only output, one object per invocation, with a top-level
  ``status`` field of ``ok|blocked|error``.
* ``shadow`` runs the deterministic engine; output is JSON-safe shadow
  metadata and never contains raw clinical text.
* ``preview`` returns a user-visible, source-tied local draft preview for
  the same user conversation while still blocking Kaizen writes.
* ``whatsapp-reply`` renders the Portfolio Guru reply for a Hermes WhatsApp
  transport, without sending WhatsApp messages or writing to Kaizen.
* ``recommend`` / ``draft`` / ``health`` always return ``blocked`` so
  the test bot cannot drift away from the live engine via local
  heuristics.
* ``save`` always returns ``blocked``; the CLI never writes to Kaizen.
* Empty / group / malformed inputs are refused safely.
* The CLI source must not reference the live beta bot token or import
  the live Telegram client.
* The tracked profile shim under ``scripts/hermes-profile/`` is thin
  (no recommend/draft heuristic, no Kaizen logic).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
CLI_PATH = BACKEND_DIR / "hermes_pg_cli.py"
PROFILE_SHIM_PATH = REPO_ROOT / "scripts" / "hermes-profile" / "pg"


def _run_cli(*args: str, stdin: str | None = None) -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, "-m", "hermes_pg_cli", *args],
        cwd=str(BACKEND_DIR),
        input=stdin,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        raise AssertionError(
            f"CLI produced no stdout. stderr={result.stderr!r}"
        )
    payload = json.loads(result.stdout)
    return result.returncode, payload


def _valid_payload(**overrides) -> dict:
    base = {
        "channel": "telegram",
        "conversation_id": "tg:chat:99",
        "gateway_user_id": "hermes-cli-user",
        "scope": "direct",
        "text": "62M chest pain in resus, RSI with the consultant, possible STEMI",
        "media": [],
        "private": True,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# status — honest self-description
# ---------------------------------------------------------------------------


def test_status_returns_ok_with_engine_identity():
    code, payload = _run_cli("status")

    assert code == 0
    assert payload["status"] == "ok"
    data = payload["data"]
    assert data["engine"]
    assert data["engine_version"]
    assert "shadow" in data["supported_commands"]
    assert "preview" in data["supported_commands"]
    assert data["kaizen_writes"] is False
    assert data["shadow_only"] is True


# ---------------------------------------------------------------------------
# shadow — real engine path
# ---------------------------------------------------------------------------


def test_shadow_with_inline_payload_runs_engine_and_returns_metadata():
    payload_arg = json.dumps(_valid_payload())
    code, response = _run_cli("shadow", "--payload", payload_arg)

    assert code == 0
    assert response["status"] == "ok"
    metadata = response["data"]
    assert metadata["disposition"] == "handle"
    assert metadata["ingest_kind"] == "possible_case_detail"
    assert metadata["source_type"] == "text"
    assert isinstance(metadata["actions"], list)
    # Engine surfaces at least one case-handling action.
    action_kinds = {a["kind"] for a in metadata["actions"]}
    assert action_kinds & {
        "ack_case_details",
        "request_case_confirmation",
        "offer_draft",
    }


def test_shadow_metadata_never_echoes_raw_clinical_text():
    marker = "ZZUNIQUEZZMARKER"
    payload = _valid_payload(
        text=f"{marker} 62M chest pain in resus, RSI with the consultant"
    )
    code, response = _run_cli("shadow", "--payload", json.dumps(payload))

    assert code == 0
    blob = json.dumps(response)
    assert marker not in blob
    assert "chest pain" not in blob
    assert "consultant" not in blob


def test_shadow_with_group_scope_returns_refuse_group_without_echoing_text():
    secret = "ZZGROUPSECRETZZ"
    payload = _valid_payload(scope="group", text=secret)
    code, response = _run_cli("shadow", "--payload", json.dumps(payload))

    assert code == 0
    assert response["status"] == "ok"
    metadata = response["data"]
    assert metadata["disposition"] == "refuse_group"
    blob = json.dumps(response)
    assert secret not in blob


def test_shadow_with_empty_payload_returns_refuse_empty():
    payload = _valid_payload(text=None, media=[])
    code, response = _run_cli("shadow", "--payload", json.dumps(payload))

    assert code == 0
    metadata = response["data"]
    assert metadata["disposition"] == "refuse_empty"
    assert "actions" not in metadata


def test_shadow_reads_payload_from_stdin():
    payload = _valid_payload(text="What forms would this case support?")
    code, response = _run_cli(
        "shadow", "--payload-file", "-", stdin=json.dumps(payload)
    )

    assert code == 0
    metadata = response["data"]
    assert metadata["disposition"] == "handle"
    assert metadata["ingest_kind"] == "side_question"


def test_shadow_with_invalid_json_returns_error_status():
    code, response = _run_cli("shadow", "--payload", "{not-json")

    assert code == 1
    assert response["status"] == "error"
    assert "could not load payload" in response["error"]


def test_shadow_with_missing_conversation_id_returns_error_status():
    payload = _valid_payload()
    payload.pop("conversation_id")
    code, response = _run_cli("shadow", "--payload", json.dumps(payload))

    assert code == 1
    assert response["status"] == "error"
    assert "conversation_id" in response["error"]


def test_shadow_without_payload_argument_returns_error():
    code, response = _run_cli("shadow")

    assert code == 1
    assert response["status"] == "error"


# ---------------------------------------------------------------------------
# preview — user-visible local draft path
# ---------------------------------------------------------------------------


def test_preview_with_inline_payload_returns_user_visible_draft():
    payload = _valid_payload(
        text=(
            "55-year-old male in ED resus with central chest pain radiating "
            "to left arm, sweating, and anterior ST elevation on ECG. "
            "I assessed him, gave aspirin, arranged analgesia, escalated "
            "early to cardiology for primary PCI, updated his wife, and "
            "reflected that repeating the ECG sooner would have shortened "
            "decision time."
        )
    )
    code, response = _run_cli("preview", "--payload", json.dumps(payload))

    assert code == 0
    assert response["status"] == "ok"
    data = response["data"]
    assert data["kaizen_writes"] is False
    assert data["source"] == "vnext_draft_preview"
    assert data["form_type"] == "CBD"
    assert data["confidence"] in {"high", "medium"}
    preview = data["preview_text"]
    assert "vNext local preview" in preview
    assert "not a Kaizen draft" in preview
    assert "55" in preview
    assert "ED" in preview
    assert "CBD" in preview


def test_preview_with_refused_payload_returns_blocked_without_echoing_text():
    secret = "ZZPREVIEWGROUPSECRETZZ"
    payload = _valid_payload(scope="group", text=secret)
    code, response = _run_cli("preview", "--payload", json.dumps(payload))

    assert code == 0
    assert response["status"] == "blocked"
    blob = json.dumps(response)
    assert secret not in blob
    assert response["data"]["kaizen_writes"] is False


def test_preview_without_payload_argument_returns_error():
    code, response = _run_cli("preview")

    assert code == 1
    assert response["status"] == "error"


# ---------------------------------------------------------------------------
# whatsapp-reply — Hermes WhatsApp transport rendering
# ---------------------------------------------------------------------------


def test_whatsapp_reply_greeting_returns_portfolio_onboarding():
    payload = _valid_payload(
        channel="whatsapp",
        conversation_id="447000000000@s.whatsapp.net",
        gateway_user_id="447000000000@s.whatsapp.net",
        text="hi",
    )

    code, response = _run_cli("whatsapp-reply", "--payload", json.dumps(payload))

    assert code == 0
    assert response["status"] == "ok"
    data = response["data"]
    assert data["disposition"] == "handle"
    assert data["reply_kind"] == "portfolio_reply"
    assert data["kaizen_writes"] is False
    rendered = data["rendered_reply"]
    assert "Welcome to Portfolio Guru" in rendered
    assert "Kaizen" in rendered


@pytest.mark.parametrize(
    ("text", "expected_terms"),
    [
        (
            "do you not want to connect to my kaizen account?",
            ("Connect Kaizen", "secure setup", "review and approve"),
        ),
        (
            "What forms would this support for my portfolio?",
            ("RCEM", "WPBA", "drafts"),
        ),
        (
            "what dose morphine should I give?",
            ("advise", "prescribing", "portfolio draft"),
        ),
        (
            "why are you sending the same message to every question",
            ("draft portfolio evidence", "portfolio questions", "Kaizen"),
        ),
    ],
)
def test_whatsapp_reply_routes_short_questions_instead_of_repeating_case_prompt(
    text, expected_terms
):
    payload = _valid_payload(
        channel="whatsapp",
        conversation_id="447000000000@s.whatsapp.net",
        gateway_user_id="447000000000@s.whatsapp.net",
        text=text,
    )

    code, response = _run_cli("whatsapp-reply", "--payload", json.dumps(payload))

    assert code == 0
    assert response["status"] == "ok"
    rendered = response["data"]["rendered_reply"]
    assert "Please describe the clinical case you want to document" not in rendered
    for term in expected_terms:
        assert term in rendered


def test_whatsapp_reply_group_scope_returns_private_refusal_without_echo():
    secret = "ZZWHATSAPPGROUPSECRETZZ"
    payload = _valid_payload(
        channel="whatsapp",
        conversation_id="120000000000000000@g.us",
        gateway_user_id="447000000000@s.whatsapp.net",
        scope="group",
        text=secret,
    )

    code, response = _run_cli("whatsapp-reply", "--payload", json.dumps(payload))

    assert code == 0
    assert response["status"] == "ok"
    data = response["data"]
    assert data["disposition"] == "refuse_group"
    assert data["reply_kind"] == "refusal"
    assert data["kaizen_writes"] is False
    rendered = data["rendered_reply"]
    assert "one-to-one" in rendered
    assert secret not in json.dumps(response)


def test_whatsapp_reply_empty_payload_blocks_without_reply():
    payload = _valid_payload(
        channel="whatsapp",
        conversation_id="447000000000@s.whatsapp.net",
        text=None,
        media=[],
    )

    code, response = _run_cli("whatsapp-reply", "--payload", json.dumps(payload))

    assert code == 0
    assert response["status"] == "blocked"
    assert response["data"]["disposition"] == "refuse_empty"
    assert response["data"]["kaizen_writes"] is False


# ---------------------------------------------------------------------------
# Deferred / blocked commands — never fake the engine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["recommend", "draft", "health"])
def test_deferred_command_returns_blocked_pointing_to_shadow(command):
    code, response = _run_cli(command)

    assert code == 0
    assert response["status"] == "blocked"
    assert response["data"]["command"] == command
    assert response["data"]["route_via"] == "shadow"


def test_save_returns_blocked_with_kaizen_safety_reason():
    code, response = _run_cli("save")

    assert code == 0
    assert response["status"] == "blocked"
    data = response["data"]
    assert data["command"] == "save"
    assert data["kaizen_writes"] is False
    assert "Kaizen" in data["reason"]


# ---------------------------------------------------------------------------
# Source-level guardrails
# ---------------------------------------------------------------------------


def test_cli_source_does_not_reference_live_beta_token():
    src = CLI_PATH.read_text(encoding="utf-8")
    assert "PORTFOLIO_GURU_TELEGRAM_BOT_TOKEN" not in src


def test_cli_source_does_not_import_live_telegram_or_filer():
    src = CLI_PATH.read_text(encoding="utf-8")
    forbidden = (
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
    for needle in forbidden:
        assert needle not in src, f"CLI must not reference {needle!r}"


def test_cli_imports_cleanly_without_telegram_or_bws():
    """The CLI must be importable inside a Hermes process that has
    neither python-telegram-bot nor BWS available."""
    forbidden = ("telegram", "bws", "boto3", "keyring", "bitwarden")
    check = "; ".join(
        f"sys.exit(1) if {m!r} in sys.modules else None" for m in forbidden
    )
    result = subprocess.run(
        [sys.executable, "-c", f"import sys, hermes_pg_cli; {check}"],
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"hermes_pg_cli imported a forbidden module:\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Profile shim — repo-tracked source must be thin
# ---------------------------------------------------------------------------


def test_profile_shim_source_is_thin_and_delegates():
    assert PROFILE_SHIM_PATH.exists(), (
        "scripts/hermes-profile/pg must be tracked in repo"
    )
    src = PROFILE_SHIM_PATH.read_text(encoding="utf-8")

    # It must delegate to the repo CLI rather than hosting product logic.
    assert "hermes_pg_cli" in src
    assert "execvpe" in src or "execv" in src or "subprocess" in src

    # It must not reintroduce the toy heuristics that used to live in
    # the profile folder.
    for forbidden in (
        "FORMS = {",
        "TAG_MAP =",
        "DRAFT_TEMPLATES",
        "_score_notes",
    ):
        assert forbidden not in src, (
            f"profile shim must not contain {forbidden!r} — the engine "
            "owns recommend/draft logic"
        )

    # It must never name the live beta bot token.
    assert "PORTFOLIO_GURU_TELEGRAM_BOT_TOKEN" not in src
