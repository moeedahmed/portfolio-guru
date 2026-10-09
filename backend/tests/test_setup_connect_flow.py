"""/setup's Kaizen connect step, driven through the real application.

2026-09-27 (Moeed): /setup sent a pile of messages, every tap on the
password-free option added another "Sign in to Kaizen" message whose new link
silently cancelled the one in use, and after signing in nothing happened in
Telegram. Now: one message with both choices, edited in place, one link however
many taps, and the bot confirms the connection by itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message
from telegram.ext import CallbackContext

import bot
from tests.helpers import BOT_USER, TEST_USER, make_callback_update, make_command_update, make_text_update
from tests.test_e2e_offline import _prepare_update


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    from tests.helpers import isolate_bot_storage

    isolate_bot_storage(monkeypatch, tmp_path)
    monkeypatch.setenv("PORTFOLIO_GURU_DATA_DIR", str(tmp_path))  # fresh bot_persistence per test
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "0:FAKE")
    monkeypatch.setenv("PG_ENABLE_PASSWORDLESS_CONNECT", "1")
    monkeypatch.setenv("PG_PASSWORDLESS_ALLOWLIST", "*")


@pytest.fixture
def harness(monkeypatch):
    import profile_store

    connection = {"state": None}
    monkeypatch.setattr(bot, "has_credentials", lambda uid: False)
    monkeypatch.setattr(profile_store, "get_kaizen_connection", lambda uid: connection["state"])
    links = []

    async def create_link(uid):
        links.append(uid)
        return SimpleNamespace(
            url=f"https://connect.test/handoff#t{len(links)}",
            expires_in_seconds=600,
            session_id=f"s{len(links)}",
        )

    monkeypatch.setattr(bot, "_create_passwordless_link", create_link)
    app = bot.build_application()
    real_bot = app.bot
    real_bot._unfreeze()
    real_bot._bot_user = BOT_USER
    real_bot._bot_initialized = True
    real_bot._requests_initialized = True
    cls = type(real_bot)
    outbox = []

    async def send_message(_self, chat_id=None, text="", **kwargs):
        outbox.append(("send", text, kwargs.get("reply_markup")))
        chat = Chat(chat_id, "private")
        chat.set_bot(_self)
        message = Message(
            5000 + len(outbox), datetime.now(timezone.utc), chat,
            from_user=BOT_USER, text=text,
        )
        message.set_bot(_self)
        return message

    async def edit_message_text(_self, text="", **kwargs):
        outbox.append(("edit", text, kwargs.get("reply_markup")))
        return True

    monkeypatch.setattr(cls, "send_message", send_message)
    monkeypatch.setattr(cls, "edit_message_text", edit_message_text)
    for name in ("answer_callback_query", "edit_message_reply_markup", "delete_message", "send_chat_action"):
        monkeypatch.setattr(cls, name, AsyncMock(return_value=True))

    async def feed(update):
        _prepare_update(update, app.bot)
        await app.process_update(update)

    return SimpleNamespace(app=app, outbox=outbox, links=links, feed=feed, connection=connection)


def _buttons(markup):
    return [(b.text, b.callback_data or b.url) for row in markup.inline_keyboard for b in row]


@pytest.mark.asyncio
async def test_setup_is_one_message_with_both_ways_to_connect(harness):
    await harness.app.initialize()
    try:
        await harness.feed(make_command_update("setup"))
    finally:
        await harness.app.shutdown()

    assert len(harness.outbox) == 1
    kind, text, markup = harness.outbox[0]
    assert kind == "send"
    # Password first and recommended; password-free kept as the alternative.
    assert text.index("Share my login (recommended)") < text.index("Sign in on Kaizen's page")
    assert "Stays connected" in text and "encrypted" in text and "/reset" in text
    assert "about a day" in text
    assert "Fernet" not in text
    # Kept short enough to scan in a few seconds.
    assert len(text.splitlines()) <= 9
    assert _buttons(markup) == [
        ("🔑 Share my login (recommended)", "ACTION|setup_password"),
        ("🔒 Sign in on Kaizen's page", "ACTION|connect_passwordless"),
        ("❌ Cancel", "ACTION|cancel"),
    ]


@pytest.mark.asyncio
async def test_tapping_sign_in_twice_edits_one_message_and_keeps_one_link(harness):
    await harness.app.initialize()
    try:
        await harness.feed(make_command_update("setup"))
        harness.outbox.clear()
        await harness.feed(make_callback_update("ACTION|connect_passwordless"))
        await harness.feed(make_callback_update("ACTION|connect_passwordless"))
        jobs = harness.app.job_queue.get_jobs_by_name(f"pwl-watch-{TEST_USER.id}")
    finally:
        await harness.app.shutdown()

    assert harness.links == [TEST_USER.id]
    assert [kind for kind, _, _ in harness.outbox] == ["edit", "edit"]
    _, text, markup = harness.outbox[-1]
    assert "I'll confirm here as soon as it works" in text
    assert _buttons(markup) == [
        ("🔒 Open Kaizen sign-in", "https://connect.test/handoff#t1"),
        ("🔑 Share my login instead", "ACTION|setup_password"),
        ("❌ Cancel", "ACTION|cancel"),
    ]
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_bot_confirms_kaizen_connected_without_being_asked(harness, monkeypatch):
    import mobile_kaizen_handoff

    status = {"value": "login"}
    monkeypatch.setattr(mobile_kaizen_handoff, "connect_link_status", lambda session_id: status["value"])
    monkeypatch.setattr(bot, "_probe_kept_kaizen_session", AsyncMock(return_value="hst"))
    monkeypatch.setattr(bot.consent, "has_current_consent", AsyncMock(return_value=True))

    def mark(uid):
        harness.connection["state"] = "passwordless"

    monkeypatch.setattr(bot.kaizen_connection, "mark_passwordless", MagicMock(side_effect=mark))
    await harness.app.initialize()
    try:
        await harness.feed(make_command_update("setup"))
        await harness.feed(make_callback_update("ACTION|connect_passwordless"))
        job = harness.app.job_queue.get_jobs_by_name(f"pwl-watch-{TEST_USER.id}")[0]
        context = CallbackContext.from_job(job, harness.app)
        harness.outbox.clear()

        await bot._passwordless_watch_job(context)  # still signing in
        assert harness.outbox == []

        status["value"] = "complete"
        await bot._passwordless_watch_job(context)
    finally:
        await harness.app.shutdown()

    bot.kaizen_connection.mark_passwordless.assert_called_once_with(TEST_USER.id)
    kinds = [kind for kind, _, _ in harness.outbox]
    texts = [text for _, text, _ in harness.outbox]
    assert texts[0].startswith("✅ Signed in to Kaizen")
    # The sign-in message settles without buttons, and the result arrives as a
    # new message so the chat reads in order and the doctor is notified.
    assert harness.outbox[1] == ("edit", bot._PASSWORDLESS_SIGNED_IN_TEXT, None)
    assert kinds[-1] == "send"
    assert "Kaizen connected" in texts[-1] and "HST" in texts[-1]
    assert bot._PWL_WATCH_KEY not in harness.app.user_data[TEST_USER.id]


@pytest.mark.asyncio
async def test_an_expired_link_is_reported_with_a_way_to_get_a_new_one(harness, monkeypatch):
    import mobile_kaizen_handoff

    monkeypatch.setattr(mobile_kaizen_handoff, "connect_link_status", lambda session_id: "expired")
    await harness.app.initialize()
    try:
        await harness.feed(make_command_update("setup"))
        await harness.feed(make_callback_update("ACTION|connect_passwordless"))
        job = harness.app.job_queue.get_jobs_by_name(f"pwl-watch-{TEST_USER.id}")[0]
        harness.outbox.clear()
        await bot._passwordless_watch_job(CallbackContext.from_job(job, harness.app))

        _, text, markup = harness.outbox[-1]
        assert "expired" in text
        assert ("🔁 Get a new link", "ACTION|passwordless_link") in _buttons(markup)

        # The new-link button works although setup's conversation has ended.
        harness.outbox.clear()
        await harness.feed(make_callback_update("ACTION|passwordless_link"))
    finally:
        await harness.app.shutdown()

    assert len(harness.links) == 2
    assert "https://connect.test/handoff#t2" in [url for _, url in _buttons(harness.outbox[-1][2])]


@pytest.mark.asyncio
async def test_choosing_the_password_route_then_typing_an_email_asks_for_the_password(harness):
    await harness.app.initialize()
    try:
        await harness.feed(make_command_update("setup"))
        await harness.feed(make_callback_update("ACTION|connect_passwordless"))
        await harness.feed(make_callback_update("ACTION|setup_password"))
        assert "Share my Kaizen login" in harness.outbox[-1][1]
        assert harness.app.job_queue.get_jobs_by_name(f"pwl-watch-{TEST_USER.id}") == () or all(
            job.removed for job in harness.app.job_queue.get_jobs_by_name(f"pwl-watch-{TEST_USER.id}")
        )
        harness.outbox.clear()
        await harness.feed(make_text_update("doctor@example.test"))
    finally:
        await harness.app.shutdown()

    assert any("password" in text.lower() for _, text, _ in harness.outbox)


def _case_conv(app):
    from telegram.ext import ConversationHandler

    return next(h for h in app.handlers[0] if isinstance(h, ConversationHandler) and h.name == "case_conv")


@pytest.mark.asyncio
async def test_connect_buttons_on_the_connect_first_prompt_never_leave_a_case_stuck(harness):
    """A doctor who sends a case before connecting gets the same choice card.

    Its buttons must be answered by the case conversation that showed it; if
    /setup took them, the case conversation would sit waiting for a username
    and swallow the doctor's next case as one.
    """
    await harness.app.initialize()
    try:
        await harness.feed(make_text_update("Saw a 70-year-old with chest pain today"))
        assert "Sign in on Kaizen's page" in harness.outbox[-1][1]
        key = (TEST_USER.id, TEST_USER.id)
        assert _case_conv(harness.app)._conversations.get(key) == bot.AWAIT_USERNAME

        await harness.feed(make_callback_update("ACTION|connect_passwordless"))
        assert "I'll confirm here as soon as it works" in harness.outbox[-1][1]
        assert _case_conv(harness.app)._conversations.get(key) is None

        await harness.feed(make_text_update("Saw a 70-year-old with chest pain today"))
        assert "Sign in on Kaizen's page" in harness.outbox[-1][1]
        await harness.feed(make_callback_update("ACTION|setup_password"))
        assert "Share my Kaizen login" in harness.outbox[-1][1]
        assert _case_conv(harness.app)._conversations.get(key) == bot.AWAIT_USERNAME
    finally:
        await harness.app.shutdown()


@pytest.mark.asyncio
async def test_a_sign_in_finishing_after_reset_does_not_reconnect(harness, monkeypatch):
    """/reset wipes the watch while Kaizen is still being asked: the late
    "complete" must not mark the wiped account connected."""
    import mobile_kaizen_handoff

    monkeypatch.setattr(bot, "_probe_kept_kaizen_session", AsyncMock(return_value="hst"))
    monkeypatch.setattr(bot.kaizen_connection, "mark_passwordless", MagicMock())
    await harness.app.initialize()
    try:
        await harness.feed(make_command_update("setup"))
        await harness.feed(make_callback_update("ACTION|connect_passwordless"))
        job = harness.app.job_queue.get_jobs_by_name(f"pwl-watch-{TEST_USER.id}")[0]
        context = CallbackContext.from_job(job, harness.app)

        def status_during_reset(session_id):
            context.user_data.clear()  # /reset lands mid-check
            return "complete"

        monkeypatch.setattr(mobile_kaizen_handoff, "connect_link_status", status_during_reset)
        harness.outbox.clear()
        await bot._passwordless_watch_job(context)
    finally:
        await harness.app.shutdown()

    bot.kaizen_connection.mark_passwordless.assert_not_called()
    assert harness.outbox == []


@pytest.mark.asyncio
async def test_portfolio_pick_after_no_password_sign_in_ends_setup_normally(harness, monkeypatch):
    """The pick used to land on Settings' handler: "Portfolio set" with a Back
    to Settings, instead of setup's "send your case" ending."""
    import mobile_kaizen_handoff

    monkeypatch.setattr(mobile_kaizen_handoff, "connect_link_status", lambda session_id: "complete")
    monkeypatch.setattr(bot, "_probe_kept_kaizen_session", AsyncMock(return_value="unknown"))
    monkeypatch.setattr(bot.consent, "has_current_consent", AsyncMock(return_value=True))
    monkeypatch.setattr(bot.kaizen_connection, "mark_passwordless", MagicMock())
    monkeypatch.setattr(bot, "store_training_level", MagicMock())
    monkeypatch.setattr(bot, "store_curriculum", MagicMock())
    await harness.app.initialize()
    try:
        await harness.feed(make_command_update("setup"))
        await harness.feed(make_callback_update("ACTION|connect_passwordless"))
        job = harness.app.job_queue.get_jobs_by_name(f"pwl-watch-{TEST_USER.id}")[0]
        await bot._passwordless_watch_job(CallbackContext.from_job(job, harness.app))
        assert "Which Kaizen portfolio" in harness.outbox[-1][1]

        harness.outbox.clear()
        await harness.feed(make_callback_update("SETLEVEL|HIGHER"))
    finally:
        await harness.app.shutdown()

    _, text, markup = harness.outbox[-1]
    assert "Kaizen connected" in text
    assert bot.render_message("welcome_connected") in text
    assert markup is None or ("🔙 Back", "ACTION|settings") not in _buttons(markup)


@pytest.mark.asyncio
async def test_a_rejected_login_after_start_goes_back_to_the_email_step(harness, monkeypatch):
    """2026-09-28 (Moeed): /start, then a wrong password, showed the old
    "Login failed — Select Retry, or type your Kaizen email" step. Now the same
    message asks for the email again, with the password-free way in under it."""
    monkeypatch.setattr(bot, "_test_kaizen_login", AsyncMock(return_value=False))
    await harness.app.initialize()
    try:
        await harness.feed(make_command_update("start"))
        await harness.feed(make_callback_update("ACTION|setup_password"))
        await harness.feed(make_text_update("doctor@example.test"))
        await harness.feed(make_text_update("wrong-password"))
        kind, text, markup = harness.outbox[-1]
        harness.outbox.clear()
        await harness.feed(make_text_update("doctor@example.test"))
    finally:
        await harness.app.shutdown()

    assert kind == "edit"
    assert "didn't accept that email and password" in text
    assert "Type your Kaizen email" in text
    assert "Login failed" not in text and "Select Retry" not in text
    assert _buttons(markup) == [
        ("🔒 Sign in on Kaizen's page", "ACTION|connect_passwordless"),
        ("❌ Cancel", "ACTION|cancel"),
    ]
    # Typing the email again carries straight on to the password.
    assert any("password" in t.lower() for _, t, _ in harness.outbox)


@pytest.mark.asyncio
async def test_a_rejected_login_without_the_password_free_option_offers_cancel_only(harness, monkeypatch):
    monkeypatch.setenv("PG_PASSWORDLESS_ALLOWLIST", "")
    monkeypatch.setattr(bot, "_test_kaizen_login", AsyncMock(return_value=False))
    await harness.app.initialize()
    try:
        await harness.feed(make_command_update("start"))
        await harness.feed(make_text_update("doctor@example.test"))
        await harness.feed(make_text_update("wrong-password"))
    finally:
        await harness.app.shutdown()

    _, text, markup = harness.outbox[-1]
    assert "didn't accept that email and password" in text
    assert _buttons(markup) == [("❌ Cancel", "ACTION|cancel")]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["hst", "unknown"])
@pytest.mark.parametrize("source", ["case", "setup"])
async def test_pending_case_after_passwordless_watch_has_working_best_fit(harness, monkeypatch, role, source):
    import mobile_kaizen_handoff
    from models import FormTypeRecommendation

    case = "Synthetic scenario: I assessed a fictional adult with chest pain and discussed the ECG and escalation with my supervisor."
    monkeypatch.setattr(mobile_kaizen_handoff, "connect_link_status", lambda _: "complete")
    monkeypatch.setattr(bot, "_probe_kept_kaizen_session", AsyncMock(return_value=role))
    monkeypatch.setattr(bot.kaizen_connection, "mark_passwordless", lambda _: harness.connection.update(state="passwordless"))
    monkeypatch.setattr(bot, "recommend_form_types", AsyncMock(return_value=[
        FormTypeRecommendation(form_type="CBD", rationale="Synthetic discussion", uuid=bot.FORM_UUIDS["CBD"]),
    ]))
    # Stop at the existing missing-detail boundary after the form is chosen.
    gate = AsyncMock(return_value=bot.AWAIT_CASE_INPUT)
    monkeypatch.setattr(bot, "_essentials_gate_before_draft", gate)
    await harness.app.initialize()
    try:
        await harness.feed(make_text_update(case))
        if source == "setup":
            await harness.feed(make_command_update("setup"))
        await harness.feed(make_callback_update("ACTION|connect_passwordless"))
        job = harness.app.job_queue.get_jobs_by_name(f"pwl-watch-{TEST_USER.id}")[0]
        await bot._passwordless_watch_job(CallbackContext.from_job(job, harness.app))
        if role == "unknown":
            await harness.feed(make_callback_update("SETLEVEL|HIGHER"))
        assert "Best fit:" in harness.outbox[-1][1]
        data = harness.app.user_data[TEST_USER.id]
        assert data["case_text"] == case
        choice = make_callback_update("FORM|best")
        choice.callback_query.message._unfreeze()
        choice.callback_query.message.message_id = data["last_bot_msg_id"]
        await harness.feed(choice)
        assert data["chosen_form"] == "CBD"
        gate.assert_awaited_once()
        case_conv = next(h for h in harness.app.handlers[0] if getattr(h, "name", None) == "case_conv")
        assert case_conv._conversations[(TEST_USER.id, TEST_USER.id)] == bot.AWAIT_CASE_INPUT
    finally:
        await harness.app.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["not_connected", "no_consent", "pending_setup", "old_message", "no_resume", "reset"])
async def test_setup_form_reentry_refuses_unready_or_old_buttons(harness, monkeypatch, blocked):
    monkeypatch.setattr(bot.consent, "has_current_consent", AsyncMock(return_value=blocked != "no_consent"))
    harness.connection["state"] = None if blocked == "not_connected" else "passwordless"
    extraction = AsyncMock(side_effect=AssertionError("Extraction must not run"))
    monkeypatch.setattr(bot, "_analyse_selected_form", extraction)
    monkeypatch.setattr(bot, "_essentials_gate_before_draft", AsyncMock(return_value=None))
    update = make_callback_update("FORM|CBD")
    message_id = update.callback_query.message.message_id
    data = harness.app.user_data[TEST_USER.id]
    data.update({
        "case_text": "Synthetic fictional chest pain case, discussed with my supervisor.",
        "_setup_pending_case": blocked == "pending_setup",
        "_setup_form_reentry": (TEST_USER.id, message_id + (blocked == "old_message")),
        "last_bot_msg_id": message_id,
        "last_bot_chat_id": TEST_USER.id,
    })
    if blocked == "no_resume":
        data.pop("_setup_form_reentry")
    if blocked == "reset":
        data.clear()
    await harness.app.initialize()
    try:
        await harness.feed(update)
        extraction.assert_not_awaited()
        assert "chosen_form" not in data
        case_conv = next(h for h in harness.app.handlers[0] if getattr(h, "name", None) == "case_conv")
        assert (TEST_USER.id, TEST_USER.id) not in case_conv._conversations
    finally:
        await harness.app.shutdown()
