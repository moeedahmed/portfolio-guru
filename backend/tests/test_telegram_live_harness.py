import pytest

from tests import telegram_live_harness as harness


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
