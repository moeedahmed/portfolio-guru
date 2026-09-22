import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import telegram_live_harness as harness

REPO_ROOT = Path(__file__).resolve().parents[2]
BOT_QA = REPO_ROOT / "scripts" / "telegram_bot_qa.sh"
TARGET_REFUSED_EXIT = 21


def _set_base_live_env(monkeypatch):
    monkeypatch.setenv("TELETHON_SESSION", "session")
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")


def test_live_env_requires_explicit_approval(monkeypatch):
    _set_base_live_env(monkeypatch)

    assert harness.has_telethon_env() is False
    with pytest.raises(RuntimeError, match="explicitly approves"):
        harness.assert_live_telegram_guardrails()


def test_live_env_allows_default_portfolio_bot_after_approval(monkeypatch):
    _set_base_live_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_LIVE_APPROVED", harness.LIVE_APPROVAL_VALUE)

    assert harness.has_telethon_env() is True
    harness.assert_live_telegram_guardrails()


def test_live_env_blocks_non_allowlisted_bot(monkeypatch):
    _set_base_live_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_LIVE_APPROVED", harness.LIVE_APPROVAL_VALUE)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "unrelated_bot")

    assert harness.has_telethon_env() is False
    with pytest.raises(RuntimeError, match="not allowlisted"):
        harness.assert_live_telegram_guardrails()


def test_live_env_accepts_explicit_allowlisted_bot(monkeypatch):
    _set_base_live_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_LIVE_APPROVED", harness.LIVE_APPROVAL_VALUE)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "@portfolio_guru_staging_bot")
    monkeypatch.setenv("TELEGRAM_LIVE_ALLOWED_BOTS", "portfolio_guru_bot,portfolio_guru_staging_bot")

    assert harness.has_telethon_env() is True
    harness.assert_live_telegram_guardrails()


def test_guardrails_refuse_runtime_target_mismatch(monkeypatch):
    _set_base_live_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_LIVE_APPROVED", harness.LIVE_APPROVAL_VALUE)

    with pytest.raises(RuntimeError, match="Refusing to send"):
        harness.assert_live_telegram_guardrails("@different_bot")


# --- the approved live target must survive the QA script's own dotenv load ---
#
# scripts/telegram_bot_qa.sh reads backend/.env after it starts. Before this
# guard, a TELEGRAM_BOT_USERNAME in that file silently replaced the target the
# release approval named, so an approved live proof could have messaged a
# different bot. These run the real script; each one exits at the guard, before
# any pytest step and long before anything live.


def _fake_backend(tmp_path, env_lines):
    backend = tmp_path / "backend"
    (backend / "venv" / "bin").mkdir(parents=True)
    # Give the script a real interpreter for its dotenv reader without letting it
    # find this repo's backend.
    (backend / "venv" / "bin" / "python3").symlink_to(sys.executable)
    (backend / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    return backend


def _run_bot_qa(tmp_path, env_lines, **env):
    _fake_backend(tmp_path, env_lines)
    return subprocess.run(
        ["bash", str(BOT_QA)],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
            "PORTFOLIO_GURU_APP_DIR": str(tmp_path),
            "TELEGRAM_BOT_QA_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
            **env,
        },
    )


def test_dotenv_cannot_redirect_an_approved_live_target(tmp_path):
    result = _run_bot_qa(
        tmp_path,
        ["TELEGRAM_BOT_USERNAME=attacker_bot"],
        RELEASE_LIVE_TARGET="portfolio_guru_bot",
        TELEGRAM_BOT_USERNAME="portfolio_guru_bot",
    )

    assert result.returncode == TARGET_REFUSED_EXIT
    assert "changed the live Telegram target" in result.stderr
    assert "@attacker_bot" in result.stderr
    assert "Nothing was sent" in result.stderr
    assert "Running" not in result.stdout, "it must refuse before running any step"


def test_dotenv_cannot_narrow_the_allowlist_out_from_under_an_approved_target(tmp_path):
    result = _run_bot_qa(
        tmp_path,
        ["TELEGRAM_BOT_USERNAME=portfolio_guru_bot", "TELEGRAM_LIVE_ALLOWED_BOTS=some_other_bot"],
        RELEASE_LIVE_TARGET="portfolio_guru_bot",
        TELEGRAM_BOT_USERNAME="portfolio_guru_bot",
    )

    assert result.returncode == TARGET_REFUSED_EXIT
    assert "not on the allowlist" in result.stderr
    assert "Nothing was sent" in result.stderr
    assert "Running" not in result.stdout


def test_matching_dotenv_target_and_allowlist_are_accepted(tmp_path):
    result = _run_bot_qa(
        tmp_path,
        [
            "TELEGRAM_BOT_USERNAME=@portfolio_guru_staging_bot",
            "TELEGRAM_LIVE_ALLOWED_BOTS=portfolio_guru_bot, portfolio_guru_staging_bot",
        ],
        RELEASE_LIVE_TARGET="portfolio_guru_staging_bot",
        TELEGRAM_BOT_USERNAME="portfolio_guru_staging_bot",
    )

    assert result.returncode != TARGET_REFUSED_EXIT
    assert "Running collect-live-tests" in result.stdout


def test_the_guard_is_scoped_to_release_proofs(tmp_path):
    """Without an approved target there is nothing to enforce, and the script's
    own direct-call guard is unchanged."""
    result = _run_bot_qa(tmp_path, ["TELEGRAM_BOT_USERNAME=some_local_bot"])

    assert result.returncode != TARGET_REFUSED_EXIT
    assert "Running collect-live-tests" in result.stdout


@pytest.mark.parametrize("key,value", [
    ("APPROVED_LIVE_TARGET", "attacker_bot"),
    ("APPROVED_LIVE_ALLOWLIST", "attacker_bot"),
    ("FOCUSED_RELEASE", "0"), ("PY", "/bin/false"),
    ("DEFAULT_LIVE_ALLOWLIST", "attacker_bot"),
    ("BASH_ENV", "/tmp/not-a-real-file"),
])
def test_dotenv_cannot_replace_captured_approval_or_execution_controls(tmp_path, key, value):
    result = _run_bot_qa(
        tmp_path,
        [f"{key}={value}", "TELEGRAM_BOT_USERNAME=attacker_bot", "TELEGRAM_LIVE_ALLOWED_BOTS=attacker_bot"],
        RELEASE_LIVE_TARGET="portfolio_guru_bot",
        RELEASE_LIVE_ALLOWLIST="portfolio_guru_bot",
        TELEGRAM_BOT_USERNAME="portfolio_guru_bot",
    )
    assert result.returncode == TARGET_REFUSED_EXIT
    assert "Nothing was sent" in result.stderr
    assert "Running" not in result.stdout


class _FakeButton:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text, buttons=(), *, message_id=1, out=False):
        self.id = message_id
        self.raw_text = text
        self.out = out
        self.buttons = [[_FakeButton(label) for label in row] for row in buttons]
        self.reply_markup = bool(buttons)


class _FakeClient:
    def __init__(self, history_batches):
        self.history_batches = list(history_batches)

    async def get_messages(self, chat_id, limit=5):
        if len(self.history_batches) > 1:
            return self.history_batches.pop(0)
        return self.history_batches[0]


class _FlakyThenWorkingClient:
    """Raises a transient network error once, then serves the given message."""

    def __init__(self, error, message):
        self._error = error
        self._raised = False
        self._message = message

    async def get_messages(self, chat_id, limit=5):
        if not self._raised:
            self._raised = True
            raise self._error
        return [self._message]


class _AlwaysRaisingClient:
    def __init__(self, error):
        self._error = error

    async def get_messages(self, chat_id, limit=5):
        raise self._error


def test_matches_expectation_requires_expected_text_and_button():
    step = harness.TelegramStep(
        name="case",
        message="case",
        expect_text_any=("CBD", "Case-Based"),
        expect_button_any=("Use best fit",),
    )
    message = _FakeMessage("This looks suitable for CBD", (("Use best fit", "See all forms"),))

    assert harness._matches_expectation(message, step) is True


def test_matches_expectation_blocks_forbidden_text_and_buttons():
    step = harness.TelegramStep(
        name="case",
        message="case",
        forbid_text_any=("traceback",),
        forbid_button_any=("danger",),
    )

    assert harness._matches_expectation(_FakeMessage("traceback shown", (("Use best fit",),)), step) is False
    assert harness._matches_expectation(_FakeMessage("Looks fine", (("Danger action",),)), step) is False


def test_find_button_selects_expected_inline_button():
    message = _FakeMessage("Choose", (("Use best fit",), ("See all forms",)))

    button = harness._find_button(message.buttons, ("all forms",))

    assert button is not None
    assert button.text == "See all forms"


@pytest.mark.asyncio
async def test_wait_for_matching_message_observes_edited_recent_message():
    stale = _FakeMessage("Old recommendation", (("Use best fit",),), message_id=10)
    edited = _FakeMessage("Forms that fit your case", (("See all forms",),), message_id=11)
    client = _FakeClient([
        [stale],
        [edited],
    ])

    match = await harness.wait_for_matching_message(
        client,
        "portfolio_guru_bot",
        timeout_seconds=2,
        expect_text_any=("Forms that fit",),
        expect_button_any=("See all forms",),
        min_id=11,
    )

    assert match is edited


@pytest.mark.asyncio
async def test_wait_for_matching_message_ignores_stale_pre_click_match():
    stale = _FakeMessage("Draft preview", (("Save as draft",),), message_id=20)
    fresh = _FakeMessage("Kaizen draft saved", (("File another case",),), message_id=21)
    client = _FakeClient([[stale, fresh]])

    match = await harness.wait_for_matching_message(
        client,
        "portfolio_guru_bot",
        timeout_seconds=2,
        expect_text_any=("draft",),
        expect_button_any=("File another",),
        min_id=21,
    )

    assert match is fresh


# --- fingerprint-based change detection (id + text + buttons), not id ordering alone ---
#
# Portfolio Guru's bot often edits a message in place after a button click
# rather than sending a new one, so the reply keeps the same id. Ordering on
# id alone cannot tell that edited-in-place reply apart from the identical
# pre-click message still being returned by a poll before the edit lands.


def test_message_fingerprint_changes_when_text_or_buttons_change():
    base = _FakeMessage("Ready to save your CBD draft", (("Save to Kaizen", "Cancel"),), message_id=30)
    edited_text = _FakeMessage("Ready to save your amended draft", (("Save to Kaizen", "Cancel"),), message_id=30)
    edited_buttons = _FakeMessage("Ready to save your CBD draft", (("Save to Kaizen",),), message_id=30)
    identical = _FakeMessage("Ready to save your CBD draft", (("Save to Kaizen", "Cancel"),), message_id=30)

    assert harness.message_fingerprint(base) == harness.message_fingerprint(identical)
    assert harness.message_fingerprint(base) != harness.message_fingerprint(edited_text)
    assert harness.message_fingerprint(base) != harness.message_fingerprint(edited_buttons)


@pytest.mark.asyncio
async def test_wait_for_matching_message_accepts_edited_same_id_reply():
    """A same-id in-place edit must be accepted once its fingerprint diverges
    from the known pre-click state, even though id ordering alone (min_id)
    would never distinguish it from the stale copy."""
    stale = _FakeMessage("Choose a form for this case", (("CBD", "Forms"),), message_id=40)
    edited = _FakeMessage("Draft ready — CBD", (("Save to Kaizen", "Cancel"),), message_id=40)
    client = _FakeClient([[stale], [edited]])

    match = await harness.wait_for_matching_message(
        client,
        "portfolio_guru_bot",
        timeout_seconds=2,
        expect_text_any=("Draft ready",),
        expect_button_any=("Save to Kaizen",),
        reject_fingerprint=harness.message_fingerprint(stale),
    )

    assert match is edited


@pytest.mark.asyncio
async def test_wait_for_matching_message_rejects_stale_pre_click_fingerprint_without_min_id():
    """Fingerprint rejection must work even with no min_id at all — proving the
    guard is not merely message-id ordering in disguise. The pre-click message
    already satisfies the text/button expectations (it is the same screen
    reappearing on a poll), so only the fingerprint match can catch it."""
    preclick = _FakeMessage("Ready to save your CBD draft", (("Save to Kaizen", "Cancel"),), message_id=50)
    client = _FakeClient([[preclick]])

    with pytest.raises(TimeoutError):
        await harness.wait_for_matching_message(
            client,
            "portfolio_guru_bot",
            timeout_seconds=1,
            expect_text_any=("Ready to save",),
            expect_button_any=("Save to Kaizen",),
            reject_fingerprint=harness.message_fingerprint(preclick),
        )


# --- parsing the rendered SLO->KC hierarchy (bot.py's `_format_curriculum_hierarchy`) ---
#
# The draft preview renders a parent "• *SLOn — label*" line followed by one or
# more child "  ↳ KCm: summary" lines. A regex like r"SLO\w*\s*KC\d+" cannot
# match this — the SLO and KC live on separate lines. `parse_visible_kc_selections`
# reconstructs the (SLO number, KC number) pairs a doctor actually sees.

_REALISTIC_HIERARCHY_TEXT = (
    "📚 *Curriculum:*\n"
    "• *SLO3 — Resuscitation & stabilisation*\n"
    "  ↳ KC2: recognising and escalating a deteriorating patient\n"
    "• *SLO8 — Lead the ED shift*\n"
    "  ↳ KC1: delegating tasks to the team\n"
    "• *SLO7 — Complex & challenging situations*\n"
    "  ↳ KC1: communicating uncertainty to patients and family\n"
)


def test_parse_visible_kc_selections_reads_hierarchy_pairs_in_order():
    pairs = harness.parse_visible_kc_selections(_REALISTIC_HIERARCHY_TEXT)

    assert pairs == [(3, 2), (8, 1), (7, 1)]


def test_parse_visible_kc_selections_keeps_same_kc_number_distinct_across_slos():
    # SLO8 KC1 and SLO7 KC1 share a KC number but are different canonical pairs.
    pairs = harness.parse_visible_kc_selections(_REALISTIC_HIERARCHY_TEXT)

    assert len(set(pairs)) == 3
    assert (8, 1) in pairs and (7, 1) in pairs and (8, 1) != (7, 1)


def test_parse_visible_kc_selections_ignores_kc_mentions_outside_hierarchy_lines():
    text = (
        "Reflection: I want to develop KC3 further next time.\n"
        "• *SLO3 — Resuscitation & stabilisation*\n"
        "  ↳ KC2: recognising and escalating a deteriorating patient\n"
    )

    pairs = harness.parse_visible_kc_selections(text)

    assert pairs == [(3, 2)]


def test_parse_visible_kc_selections_detects_duplicate_child_lines():
    """A rendering bug that repeats the same child line under one parent must
    be visible as a duplicate pair, not silently deduplicated by the parser
    itself — callers decide whether duplicates are acceptable."""
    text = (
        "• *SLO3 — Resuscitation & stabilisation*\n"
        "  ↳ KC2: recognising and escalating a deteriorating patient\n"
        "  ↳ KC2: recognising and escalating a deteriorating patient\n"
    )

    pairs = harness.parse_visible_kc_selections(text)

    assert pairs == [(3, 2), (3, 2)]
    assert len(pairs) != len(set(pairs))


def test_parse_visible_kc_selections_drops_child_line_with_no_parent_yet():
    text = "  ↳ KC1: orphaned child line with no preceding SLO header\n"

    assert harness.parse_visible_kc_selections(text) == []


# --- transcript retention (reused by the focused live journey) ---


def test_write_transcript_artifact_records_sent_and_received_content(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_E2E_ARTIFACT_DIR", str(tmp_path))
    transcript = [
        harness.TelegramExchange(step="reset", action="send:/cancel", received="Cancelled.", buttons=[]),
        harness.TelegramExchange(
            step="case",
            action="send:File this as a CBD. Synthetic case only.",
            received="I'll use CBD for this entry.",
            buttons=["CBD", "Forms", "Cancel"],
        ),
        harness.TelegramExchange(
            step="case",
            action="click_button",
            received="Draft ready — CBD",
            buttons=["Save to Kaizen", "Cancel"],
            clicked_button="CBD",
        ),
        harness.TelegramExchange(
            step="cancel",
            action="click_button",
            received="Cancelled.",
            buttons=[],
            clicked_button="Cancel",
        ),
    ]

    harness.write_transcript_artifact(transcript)

    written = json.loads((tmp_path / "portfolio-guru-telegram-transcript.json").read_text())
    assert [entry["step"] for entry in written] == ["reset", "case", "case", "cancel"]
    assert written[1]["action"].startswith("send:")
    assert written[2]["clicked_button"] == "CBD"
    assert written[3]["clicked_button"] == "Cancel"


def test_write_transcript_artifact_is_a_noop_without_artifact_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_E2E_ARTIFACT_DIR", raising=False)

    harness.write_transcript_artifact([harness.TelegramExchange(step="x", action="y", received="z")])

    assert list(tmp_path.iterdir()) == []


# --- classifying the post-click state: ready draft vs. bounded missing-essentials ---
#
# A live run on 2026-09-22 showed clicking the CBD form button can land on
# `_ask_for_missing_essentials`'s "Before I draft this, I still need: Level of
# Supervision." prompt (Cancel-only) instead of going straight to the ready
# draft. The journey has to recognise exactly these two bot.py-defined states
# and fail closed on anything else, rather than assuming Save is always next.


def test_classify_post_click_draft_state_recognises_ready_draft():
    message = _FakeMessage("Draft ready — CBD", (("Save to Kaizen", "Cancel"),))

    assert harness.classify_post_click_draft_state(message) == "ready"


def test_classify_post_click_draft_state_recognises_missing_essentials_prompt():
    message = _FakeMessage(
        "📋 Before I draft this, I still need: Level of Supervision.\n\n"
        "Send it as text, voice, photo, or a document and I'll add it to your case.",
        (("❌ Cancel",),),
    )

    assert harness.classify_post_click_draft_state(message) == "missing_essentials"


def test_classify_post_click_draft_state_rejects_missing_essentials_with_extra_buttons():
    message = _FakeMessage(
        "📋 Before I draft this, I still need: Level of Supervision.",
        (("❌ Cancel", "🔁 Retry"),),
    )

    with pytest.raises(AssertionError, match="Cancel only"):
        harness.classify_post_click_draft_state(message)


def test_classify_post_click_draft_state_rejects_unrecognised_state():
    """Neither the ready-draft marker nor the missing-essentials marker is
    present — e.g. a different bounded prompt (a missing-reflection gate) —
    so this must fail closed rather than being treated as either state."""
    message = _FakeMessage("I still need your reflection before this is ready.", (("❌ Cancel",),))

    with pytest.raises(AssertionError, match="unexpected post-click state"):
        harness.classify_post_click_draft_state(message)


def test_classify_post_click_draft_state_rejects_contradictory_message():
    """A message that somehow carries both markers must not be silently
    treated as ready — that would risk asserting Save/Cancel-only and KC
    counts against a draft that was never actually completed."""
    message = _FakeMessage(
        "📋 Before I draft this, I still need: Level of Supervision.",
        (("Save to Kaizen", "Cancel"),),
    )

    with pytest.raises(AssertionError, match="unexpected post-click state"):
        harness.classify_post_click_draft_state(message)


# --- wait_for_matching_message must not swallow every exception ---
#
# The polling loop used to wrap the whole body (get_messages call and match
# evaluation) in a bare `except Exception: pass`, so a real bug or a
# non-transient Telegram error looked exactly like an ordinary timeout. Only
# a narrow set of clearly transient network errors from `get_messages` itself
# should be retried; everything else must propagate.


@pytest.mark.asyncio
async def test_wait_for_matching_message_retries_after_transient_connection_error():
    message = _FakeMessage("Draft ready", (("Save to Kaizen",),), message_id=60)
    client = _FlakyThenWorkingClient(ConnectionError("temporary network hiccup"), message)

    match = await harness.wait_for_matching_message(
        client,
        "portfolio_guru_bot",
        timeout_seconds=2,
        expect_text_any=("Draft ready",),
        expect_button_any=("Save to Kaizen",),
    )

    assert match is message


@pytest.mark.asyncio
async def test_wait_for_matching_message_reraises_non_transient_errors():
    client = _AlwaysRaisingClient(RuntimeError("harness bug, not a network hiccup"))

    with pytest.raises(RuntimeError, match="harness bug"):
        await harness.wait_for_matching_message(
            client,
            "portfolio_guru_bot",
            timeout_seconds=1,
            expect_text_any=("anything",),
        )
