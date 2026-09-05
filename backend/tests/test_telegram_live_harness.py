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
