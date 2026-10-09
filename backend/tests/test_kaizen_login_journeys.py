"""Offline handler journeys complementary to the routed fake-browser lane.

These tests exercise local onboarding, erasure and handoff expiry without
starting a browser or contacting a provider. Browser-backed login/save
journeys live beside ``fake_browser`` in ``test_kaizen_fake_browser.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot
import credentials
import kaizen_form_filer as filer
import mobile_kaizen_handoff as handoff
import profile_store
from tests.bot_simulator import BotSimulator
from tests.helpers import isolate_bot_storage, unstamp


USERNAME = "synthetic.doctor@example.test"
PASSWORD = "synthetic-password-only"
CASE = (
    "Synthetic training scenario: I assessed a fictional adult with chest pain, "
    "reviewed the ECG and explained escalation with my supervisor."
)
CONNECT_BUTTONS = [
    ("🔑 Share my login (recommended)", "ACTION|setup_password"),
    ("🔒 Sign in on Kaizen's page", "ACTION|connect_passwordless"),
    ("❌ Cancel", "ACTION|cancel"),
]
PASSWORD_PROMPT = (
    "🔐 Step 2 of 3: verify your login\n\n"
    "What's your Kaizen password?\n\n"
    "_I'll delete your password message right after you send it._"
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    isolate_bot_storage(monkeypatch, tmp_path)
    monkeypatch.setenv("PORTFOLIO_GURU_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PORTFOLIO_GURU_PROACTIVE_PATH", str(tmp_path / "reminders.json"))
    monkeypatch.setenv("PG_ENABLE_PASSWORDLESS_CONNECT", "1")
    monkeypatch.setenv("PG_PASSWORDLESS_ALLOWLIST", "*")
    monkeypatch.setattr(filer, "_SESSION_DIR", tmp_path / "sessions")


def latest_buttons(sim):
    """Read this step's actual markup, never an earlier non-empty keyboard."""
    markup = sim.messages_sent[-1][2]
    if markup is None:
        return []
    return [
        (button.text, unstamp(button.callback_data) if button.callback_data else button.url)
        for row in markup.inline_keyboard
        for button in row
    ]


def assert_private(sim, caplog):
    assert PASSWORD not in repr(sim.messages_sent)
    assert PASSWORD not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("typo", ["doctor at example dot test", "doctor@example", "doctor.example.test"])
async def test_username_typo_can_be_corrected_without_stale_connect_buttons(typo, caplog):
    sim = BotSimulator()
    context = sim._make_context()
    assert await bot.setup_start(sim._make_text_update("/setup"), context) == bot.AWAIT_USERNAME
    assert sim.get_last_text() == bot._CONNECT_CHOICE_TEXT
    assert latest_buttons(sim) == CONNECT_BUTTONS

    update = sim._make_text_update(typo)
    update.message.delete = AsyncMock(return_value=True)
    assert await bot.setup_username(update, context) == bot.AWAIT_USERNAME
    update.message.delete.assert_awaited_once()
    assert sim.get_last_text() == "⚠️ That doesn't look like an email. What's your Kaizen username?"
    assert latest_buttons(sim) == []
    assert "setup_username" not in context.user_data

    update = sim._make_text_update(USERNAME)
    update.message.delete = AsyncMock(return_value=True)
    assert await bot.setup_username(update, context) == bot.AWAIT_PASSWORD
    assert sim.get_last_text() == PASSWORD_PROMPT
    assert latest_buttons(sim) == []
    assert context.user_data["setup_username"] == USERNAME
    assert_private(sim, caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("first_message", [USERNAME, f"My Kaizen email is {USERNAME}"])
async def test_email_as_first_message_skips_the_username_question(first_message, caplog):
    sim = BotSimulator()
    context = sim._make_context()
    assert await bot.handle_case_input(sim._make_text_update(first_message), context) == bot.AWAIT_PASSWORD
    assert sim.get_last_text() == PASSWORD_PROMPT
    assert latest_buttons(sim) == []
    assert context.user_data["setup_username"] == USERNAME
    assert context.user_data["_setup_state_hint"] == "password"
    assert not bot.has_credentials(sim.user_id)
    assert "case_text" not in context.user_data
    assert_private(sim, caplog)


@pytest.mark.asyncio
@pytest.mark.consent_gate
async def test_reset_erases_local_login_and_profile_then_restarts_linking(monkeypatch, caplog):
    import consent
    import supabase_sync

    # The only substituted reset boundary is the disabled external mirror.
    mirror_delete = MagicMock(return_value=None)
    monkeypatch.setattr(supabase_sync, "delete_user_data", mirror_delete)
    sim = BotSimulator()
    context = sim._make_context()
    credentials.store_credentials(sim.user_id, USERNAME, PASSWORD)
    profile_store.store_training_level(sim.user_id, "HIGHER")
    profile_store.store_curriculum(sim.user_id, "2025")
    await consent.record_consent(sim.user_id)
    context.user_data["case_text"] = CASE
    context.user_data["draft_data"] = {"form_type": "CBD", "fields": {"reflection": "Synthetic"}}

    await bot.reset_data(sim._make_text_update("/reset"), context)
    assert sim.get_last_text() == bot._RESET_CONFIRM_TEXT
    assert latest_buttons(sim) == [
        ("🗑️ Delete data", "CONFIRM|reset"), ("🛡️ Keep data", "CONFIRM|keep"),
    ]
    assert credentials.get_credentials(sim.user_id) == (USERNAME, PASSWORD)
    assert context.user_data["case_text"] == CASE
    assert await consent.has_current_consent(sim.user_id)

    assert await bot.handle_reset_confirm(sim._make_callback_update("CONFIRM|reset"), context) == bot.AWAIT_USERNAME
    assert sim.get_last_text() == bot._DATA_CLEAR_TEXT + "\n\n" + bot._CONNECT_CHOICE_TEXT
    assert latest_buttons(sim) == CONNECT_BUTTONS
    assert credentials.get_credentials(sim.user_id) is None
    assert profile_store.get_training_level(sim.user_id) is None
    assert profile_store.get_curriculum(sim.user_id) is None
    assert not await consent.has_current_consent(sim.user_id)
    assert "case_text" not in context.user_data and "draft_data" not in context.user_data
    mirror_delete.assert_called_once_with(sim.user_id)

    assert await bot.setup_password_start(sim._make_callback_update("ACTION|setup_password"), context) == bot.AWAIT_USERNAME
    assert sim.get_last_text() == bot._KAIZEN_PASSWORD_ROUTE_PROMPT
    assert latest_buttons(sim) == []
    update = sim._make_text_update(USERNAME)
    update.message.delete = AsyncMock(return_value=True)
    assert await bot.setup_username(update, context) == bot.AWAIT_PASSWORD
    assert sim.get_last_text() == PASSWORD_PROMPT
    assert latest_buttons(sim) == []
    assert_private(sim, caplog)


@pytest.mark.asyncio
async def test_unfinished_passwordless_link_expires_and_new_link_replaces_old_buttons(monkeypatch, caplog):
    clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    store = handoff.HandoffStore(clock=lambda: clock["now"])
    created_links = []

    async def create_link(user_id):
        created = store.create({"telegram_user_id": user_id, "subject_key": handoff.subject_key_for(user_id)})
        created_links.append(created)
        return handoff.ConnectLink(
            url=f"https://connect.example.test/handoff#{created.token}",
            expires_in_seconds=600,
            session_id=created.session_id,
        )

    monkeypatch.setattr(bot, "_create_passwordless_link", create_link)
    monkeypatch.setattr(handoff, "connect_link_status", lambda session_id: store.get_by_id(session_id).status)
    sim = BotSimulator()
    context = sim._make_context()
    jobs = []

    def schedule(callback, **kwargs):
        job = SimpleNamespace(data=kwargs["data"], schedule_removal=MagicMock(), name=kwargs["name"])
        jobs.append(job)
        return job

    context.job_queue = SimpleNamespace(
        run_repeating=schedule,
        get_jobs_by_name=lambda name: [job for job in jobs if job.name == name and not job.schedule_removal.called],
    )
    assert await bot.setup_start(sim._make_text_update("/setup"), context) == bot.AWAIT_USERNAME
    assert sim.get_last_text() == bot._CONNECT_CHOICE_TEXT
    assert latest_buttons(sim) == CONNECT_BUTTONS

    assert await bot.passwordless_setup_start(sim._make_callback_update("ACTION|connect_passwordless"), context) == bot.ConversationHandler.END
    assert sim.get_last_text() == bot._PASSWORDLESS_LINK_TEXT
    first_url = f"https://connect.example.test/handoff#{created_links[0].token}"
    assert latest_buttons(sim) == [
        ("🔒 Open Kaizen sign-in", first_url),
        ("🔑 Share my login instead", "ACTION|setup_password"),
        ("❌ Cancel", "ACTION|cancel"),
    ]
    record = store.get_by_viewer_token(store.exchange(created_links[0].token))
    store.set_status(record.session_id, "login")
    context.job = jobs[-1]
    before = list(sim.messages_sent)
    await bot._passwordless_watch_job(context)
    assert sim.messages_sent == before
    assert not bot._kaizen_connected(sim.user_id)

    clock["now"] += timedelta(minutes=11)
    await bot._passwordless_watch_job(context)
    assert record.status == "expired" and record.request is None
    assert sim.get_last_text() == "⌛ That sign-in link expired before Kaizen connected. Get a new link to try again."
    assert latest_buttons(sim) == [
        ("🔁 Get a new link", "ACTION|passwordless_link"),
        ("🔑 Share my login (recommended)", "ACTION|setup_password"),
        ("❌ Cancel", "ACTION|cancel"),
    ]
    assert context.job.schedule_removal.called
    assert bot._PWL_WATCH_KEY not in context.user_data
    assert not bot._kaizen_connected(sim.user_id)

    assert await bot.passwordless_setup_new_link(sim._make_callback_update("ACTION|passwordless_link"), context) == bot.ConversationHandler.END
    second_url = f"https://connect.example.test/handoff#{created_links[1].token}"
    assert second_url != first_url
    assert sim.get_last_text() == bot._PASSWORDLESS_LINK_TEXT
    assert latest_buttons(sim) == [
        ("🔒 Open Kaizen sign-in", second_url),
        ("🔑 Share my login instead", "ACTION|setup_password"),
        ("❌ Cancel", "ACTION|cancel"),
    ]
    assert_private(sim, caplog)


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="First case before linking is discarded: the connect guard clears user_data without retaining the case to resume Best fit.")
async def test_first_case_is_retained_while_the_doctor_connects():
    sim = BotSimulator()
    context = sim._make_context()
    assert await bot.handle_case_input(sim._make_text_update(CASE), context) == bot.AWAIT_USERNAME
    assert sim.get_last_text() == bot._CONNECT_CHOICE_TEXT
    assert latest_buttons(sim) == CONNECT_BUTTONS
    assert context.user_data.get("case_text") == CASE


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="Username validation only checks whether '@' and '.' occur, so a doubled @ typo advances to the password step.")
async def test_doubled_at_email_typo_stays_on_username_step():
    sim = BotSimulator()
    context = sim._make_context()
    await bot.setup_start(sim._make_text_update("/setup"), context)
    update = sim._make_text_update("synthetic@@example.test")
    update.message.delete = AsyncMock(return_value=True)
    state = await bot.setup_username(update, context)
    assert state == bot.AWAIT_USERNAME
    assert "doesn't look like an email" in sim.get_last_text()
    assert latest_buttons(sim) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("landing", ["https://kaizenep.com/verification", "https://kaizenep.com/welcome"])
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="_login accepts any kaizenep.com URL after submission, including verification or non-portfolio landing pages, and claims login success.")
async def test_non_portfolio_post_login_page_cannot_be_reported_as_connected(monkeypatch, landing, caplog):
    from engine.providers.kaizen import KaizenInfrastructureError

    monkeypatch.delenv("PG_ENV", raising=False)
    monkeypatch.delenv("PG_KAIZEN_OFFLINE", raising=False)
    async def no_sleep(*args):
        pass
    monkeypatch.setattr(filer, "asyncio", SimpleNamespace(sleep=no_sleep))
    locator = SimpleNamespace(count=AsyncMock(return_value=1), fill=AsyncMock(), click=AsyncMock())
    page = SimpleNamespace(url=landing, goto=AsyncMock(), locator=lambda selector: locator)
    page.wait_for_url = AsyncMock()
    # This is the actual Playwright URL pattern's accepted shape, rather than
    # a mock that invents success on a different host.
    import fnmatch
    assert fnmatch.fnmatch(landing, "**/kaizenep.com/**")
    try:
        connected = await filer._login(page, USERNAME, PASSWORD)
    except KaizenInfrastructureError:
        connected = False
    assert_private(BotSimulator(), caplog)
    assert connected is not True
