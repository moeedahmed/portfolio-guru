"""Real Telegram routing and encrypted restart recovery, with offline boundaries."""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.ext import Application, ApplicationBuilder

from tests.helpers import BOT_USER, OfflineRequest, isolate_bot_storage, make_message


pytestmark = pytest.mark.consent_gate
_IDS = itertools.count(10000)
_PROFILES = {
    "ACCS": ("2025", "accs"),
    "INTERMEDIATE": ("2025", "intermediate"),
    "SAS": ("2021", "sas"),
}
_STEPS = (r"GATHER\|done", r"FORM\|best", r"APPROVE\|draft\|\w+",
          r"ACTION\|same_case_another")
_ERROR_TEXT = ("expired", "no longer", "send the case again", "start again",
               "something went wrong", "didn't complete", "already saving", "couldn't", "not found")


def _case(marker):
    return (f"Synthetic test case {marker}: 58M central chest pain, troponin rising. "
            "I assessed and discussed with cardiology. Reflection: repeat the ECG sooner. "
            "Seen 12/9/2026 in ED majors.")


class _Responses:
    """Capture each chat's current buttons and all text, without Telegram I/O."""

    def __init__(self):
        self.events = []
        self.screens = {}
        self.errors = []

    def _message(self, chat_id, text, message_id):
        message = MagicMock(spec=Message)
        message.message_id = message_id
        message.chat_id = chat_id
        message.text = text

        async def edit(text=None, **kwargs):
            return await self.edit(text=text, chat_id=chat_id, message_id=message_id, **kwargs)

        message.edit_text = AsyncMock(side_effect=edit)
        message.delete = AsyncMock(return_value=True)
        return message

    async def send(self, chat_id=None, text="", **kwargs):
        return await self.edit(text=text, chat_id=chat_id, message_id=next(_IDS), **kwargs)

    async def edit(self, text="", chat_id=None, message_id=None, **kwargs):
        self.events.append((chat_id, text or ""))
        self.screens[message_id] = (chat_id, text, kwargs.get("reply_markup"))
        return self._message(chat_id, text, message_id)

    async def edit_markup(self, chat_id=None, message_id=None, reply_markup=None, **kwargs):
        if message_id in self.screens:
            _, text, _ = self.screens[message_id]
            self.screens[message_id] = (chat_id, text, reply_markup)
        return True

    async def answer(self, callback_query_id=None, text=None, **kwargs):
        return True

    async def delete(self, chat_id=None, message_id=None, **kwargs):
        self.screens.pop(message_id, None)
        return True

    def button(self, user_id, pattern):
        for message_id, (chat_id, text, markup) in reversed(sorted(self.screens.items())):
            if chat_id != user_id or markup is None:
                continue
            for row in markup.inline_keyboard:
                for button in row:
                    if button.callback_data and re.fullmatch(pattern, button.callback_data):
                        return button.callback_data, message_id, text
        pytest.fail(f"User {user_id}: expected button {pattern!r} was absent")


@pytest.fixture
def scenario(monkeypatch, tmp_path):
    """Stub model/Kaizen calls while retaining real isolated stores and routing."""
    import bot
    import consent
    from cryptography.fernet import Fernet
    from kaizen_form_filer import FORM_UUIDS
    from models import FormDraft, FormTypeRecommendation

    isolate_bot_storage(monkeypatch, tmp_path)
    monkeypatch.setenv("PORTFOLIO_GURU_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FERNET_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("PG_GATHERING_MODE", raising=False)
    monkeypatch.delenv("PG_PAYMENTS_ENABLED", raising=False)
    monkeypatch.setattr(consent, "has_current_consent", AsyncMock(return_value=True))
    monkeypatch.setattr(bot, "recommend_form_types", AsyncMock(return_value=[
        FormTypeRecommendation(form_type=form_type, rationale="Synthetic recommendation",
                               uuid=FORM_UUIDS.get(form_type))
        for form_type in ("CBD", "MINI_CEX", "REFLECT_LOG")
    ]))
    monkeypatch.setattr(bot, "classify_intent", AsyncMock(return_value="case"))
    monkeypatch.setattr(bot, "extract_explicit_form_type", lambda text, *, require_intent=True: None)

    async def extract(case_text=None, form_type=None, *args, **kwargs):
        marker = re.search(r"case (\w+):", case_text or kwargs.get("case_text") or "").group(1)
        form_type = form_type or kwargs.get("form_type") or "CBD"
        await asyncio.sleep(0)  # Allow independent users' real handlers to interleave.
        return FormDraft(form_type=form_type, uuid=FORM_UUIDS.get(form_type), fields={
            "date_of_encounter": "12/9/2026", "clinical_setting": "ED majors",
            "patient_presentation": f"Marker {marker} chest pain",
            "clinical_reasoning": f"Marker {marker} reasoning",
            "reflection": f"Marker {marker} reflection",
            "curriculum_links": ["SLO1"],
            "key_capabilities": ["SLO1 KC1: Assess and stabilise"],
        })

    async def extract_cbd(case_text=None, *args, **kwargs):
        return await extract(case_text=case_text or kwargs.get("case_text"), form_type="CBD")

    filed = []

    async def route(*args, **kwargs):
        await asyncio.sleep(0)
        filed.append({"user": kwargs["telegram_user_id"],
                      "login": kwargs["credentials"]["username"],
                      "fields": json.dumps(kwargs["fields"], default=str)})
        return {"status": "success", "draft_url": "https://kaizenep.com/events/fillin/synthetic-draft",
                "message": "Draft saved", "method": "stub", "filled": ["all"], "skipped": []}

    monkeypatch.setattr(bot, "extract_form_data", extract)
    monkeypatch.setattr(bot, "extract_cbd_data", extract_cbd)
    monkeypatch.setattr(bot, "route_filing", route)
    return filed


def _profile(user_id, training_level):
    import credentials
    import profile_store

    curriculum, role = _PROFILES[training_level]
    profile_store.store_training_level(user_id, training_level)
    profile_store.store_curriculum(user_id, curriculum)
    profile_store.store_kaizen_role(user_id, role)
    credentials.store_credentials(user_id, f"synthetic{user_id}@example.invalid", "synthetic-password")


@contextlib.asynccontextmanager
async def _persistent_app(responses, tmp_path):
    import bot
    from clinical_persistence import ClinicalScrubbingPersistence
    from update_processor import PerUserUpdateProcessor

    persistence = ClinicalScrubbingPersistence(filepath=tmp_path / "bot_persistence", update_interval=5)
    app = (Application.builder().token("0:FAKE").updater(None).job_queue(None)
           .persistence(persistence).request(OfflineRequest())
           .get_updates_request(OfflineRequest()).concurrent_updates(PerUserUpdateProcessor()).build())
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "0:FAKE"}), \
         patch("clinical_persistence.purge_existing_file", return_value={"status": "offline"}), \
         patch("clinical_persistence.ClinicalScrubbingPersistence", return_value=persistence), \
         patch.object(ApplicationBuilder, "build", return_value=app):
        app = bot.build_application()

    async def on_error(update, context):
        responses.errors.append(context.error)

    app.add_error_handler(on_error)
    app.bot._unfreeze()
    app.bot._bot_user = BOT_USER
    app.bot._bot_initialized = True
    app.bot._requests_initialized = True

    async def send(_bot, **kwargs):
        return await responses.send(**kwargs)

    async def edit(_bot, **kwargs):
        return await responses.edit(**kwargs)

    async def markup(_bot, **kwargs):
        return await responses.edit_markup(**kwargs)

    async def answer(_bot, **kwargs):
        return await responses.answer(**kwargs)

    async def delete(_bot, **kwargs):
        return await responses.delete(**kwargs)

    async def yes(*args, **kwargs):
        return True

    async def me(*args, **kwargs):
        return BOT_USER

    with contextlib.ExitStack() as stack:
        for name, function in {
            "send_message": send, "edit_message_text": edit, "edit_message_reply_markup": markup,
            "answer_callback_query": answer, "delete_message": delete, "send_chat_action": yes,
            "get_me": me, "delete_webhook": yes, "set_my_commands": yes,
            "pin_chat_message": yes, "set_message_reaction": yes,
        }.items():
            stack.enter_context(patch.object(type(app.bot), name, function))
        await app.initialize()
        try:
            yield app
        finally:
            if app.running:
                await app.stop()
            # Don't suppress errors: a failed flush must not count as a restart proof.
            await app.update_persistence()
            await app.shutdown()


def _update(app, user_id, *, text=None, button=None):
    user = User(id=user_id, is_bot=False, first_name="SyntheticDoctor")
    chat = Chat(id=user_id, type=Chat.PRIVATE)
    if button is None:
        update = Update(update_id=next(_IDS), message=make_message(text, user=user, chat=chat))
    else:
        data, message_id, previous_text = button
        message = Message(message_id=message_id, date=datetime.now(timezone.utc), chat=chat,
                          from_user=BOT_USER, text=previous_text)
        callback = CallbackQuery(id=str(next(_IDS)), from_user=user, chat_instance=str(user_id),
                                 data=data, message=message)
        update = Update(update_id=next(_IDS), callback_query=callback)
    for obj in (update, update.message, update.callback_query,
                getattr(update.callback_query, "message", None)):
        if obj is not None:
            obj.set_bot(app.bot)
            for child in (getattr(obj, "chat", None), getattr(obj, "from_user", None)):
                if child is not None:
                    child.set_bot(app.bot)
    return update


async def _step(app, responses, user_id, *, text=None, button_pattern=None):
    before = len(responses.events)
    button = responses.button(user_id, button_pattern) if button_pattern else None
    update = _update(app, user_id, text=text, button=button)
    await asyncio.wait_for(app.update_processor.process_update(update, app.process_update(update)), 5)
    assert not responses.errors
    texts = [text for chat_id, text in responses.events[before:] if chat_id == user_id]
    assert texts, "The user's update produced no response"
    assert not any(error in text.lower() for error in _ERROR_TEXT for text in texts), texts
    return texts


@pytest.mark.parametrize("training_level", _PROFILES)
@pytest.mark.parametrize("restart_before", [1, 2, 3, 4], ids=["captured", "form-picker", "draft-ready", "saved"])
async def test_restart_keeps_case_through_another_form(training_level, restart_before, scenario, tmp_path):
    """Restart between every case transition, including saved -> Another form."""
    user_id = 93000001
    responses = _Responses()
    _profile(user_id, training_level)
    for start, end in ((0, restart_before), (restart_before, 5)):
        async with _persistent_app(responses, tmp_path) as app:
            if start:
                assert (tmp_path / "bot_persistence").is_file()
                assert (tmp_path / "working-cases" / f"{user_id}.enc").is_file()
                assert "RESTART" in json.dumps(dict(app.user_data[user_id]), default=str)
            for step in range(start, end):
                if step == 0:
                    await _step(app, responses, user_id, text=_case("RESTART"))
                else:
                    await _step(app, responses, user_id, button_pattern=_STEPS[step - 1])
            if end == 5:
                responses.button(user_id, r"FORM\|\w+")
    assert len(scenario) == 1
    assert "RESTART" in scenario[0]["fields"]


async def test_three_users_do_not_cross_talk_and_double_save_files_once(scenario, monkeypatch, tmp_path):
    import bot

    users = {93100001: ("ACCS", "ALPHA"), 93100002: ("SAS", "BRAVO"),
             93100003: ("INTERMEDIATE", "CHARLIE")}
    saving_users = set()
    all_saving = asyncio.Event()
    route = bot.route_filing

    async def concurrent_route(*args, **kwargs):
        saving_users.add(kwargs["telegram_user_id"])
        if saving_users == set(users):
            all_saving.set()
        # A global update lock would time out: all three users must reach save together.
        await all_saving.wait()
        return await route(*args, **kwargs)

    monkeypatch.setattr(bot, "route_filing", concurrent_route)
    responses = _Responses()
    for user_id, (training_level, _) in users.items():
        _profile(user_id, training_level)
    async with _persistent_app(responses, tmp_path) as app:
        for step in range(4):
            async def run(user_id):
                if step == 0:
                    texts = await _step(app, responses, user_id, text=_case(users[user_id][1]))
                else:
                    texts = await _step(app, responses, user_id, button_pattern=_STEPS[step - 1])
                other_markers = [marker for other_id, (_, marker) in users.items() if other_id != user_id]
                assert not any(marker in text for marker in other_markers for text in texts)
            await asyncio.gather(*(run(user_id) for user_id in users))
        assert len(scenario) == 3
        assert {saved["user"] for saved in scenario} == set(users)
        user_id = 93100001
        await _step(app, responses, user_id, button_pattern=_STEPS[-1])
        await _step(app, responses, user_id, button_pattern=r"FORM\|\w+")
        button = responses.button(user_id, _STEPS[2])
        before = len(scenario)

        async def save():
            update = _update(app, user_id, button=button)
            await app.update_processor.process_update(update, app.process_update(update))

        await asyncio.wait_for(asyncio.gather(save(), save()), 5)
        assert len(scenario) - before == 1
        assert not responses.errors
    for saved in scenario:
        assert users[saved["user"]][1] in saved["fields"]
        assert not any(marker in saved["fields"] for other_id, (_, marker) in users.items()
                       if other_id != saved["user"])
        assert saved["login"] == f"synthetic{saved['user']}@example.invalid"


async def test_stop_during_save_finishes_and_reports_success(scenario, monkeypatch, tmp_path):
    """PTB's SIGTERM stop drains the update currently saving to Kaizen."""
    import bot

    started, finish = asyncio.Event(), asyncio.Event()
    route = bot.route_filing

    async def waiting_route(*args, **kwargs):
        started.set()
        await finish.wait()
        return await route(*args, **kwargs)

    monkeypatch.setattr(bot, "route_filing", waiting_route)
    user_id = 93200001
    _profile(user_id, "ACCS")
    responses = _Responses()
    async with _persistent_app(responses, tmp_path) as app:
        await _step(app, responses, user_id, text=_case("SIGTERM"))
        for pattern in _STEPS[:2]:
            await _step(app, responses, user_id, button_pattern=pattern)
        button = responses.button(user_id, _STEPS[2])
        await app.start()
        await app.update_queue.put(_update(app, user_id, button=button))
        await asyncio.wait_for(started.wait(), 5)
        stopping = asyncio.create_task(app.stop())
        try:
            await asyncio.sleep(0)
            assert not stopping.done(), "Stopping discarded an in-flight save"
            assert not scenario
        finally:
            finish.set()
            await asyncio.wait_for(stopping, 5)
        assert len(scenario) == 1
        assert "SIGTERM" in scenario[0]["fields"]
        assert any("Saved!" in text for chat_id, text in responses.events if chat_id == user_id)
        assert not responses.errors
