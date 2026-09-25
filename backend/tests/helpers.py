"""PTB-native test helpers — OfflineRequest, real Update/Message factories."""

from __future__ import annotations

import datetime
import re
from unittest.mock import AsyncMock

from telegram import Bot, Chat, Message, Update, User, CallbackQuery
from telegram.request import BaseRequest


_UPDATE_COUNTER = 0
_MSG_COUNTER = 1000


def _next_update_id() -> int:
    global _UPDATE_COUNTER
    _UPDATE_COUNTER += 1
    return _UPDATE_COUNTER


def _next_msg_id() -> int:
    global _MSG_COUNTER
    _MSG_COUNTER += 1
    return _MSG_COUNTER


class OfflineRequest(BaseRequest):
    """Blocks any network call — test fails immediately if bot tries to reach Telegram."""

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    @property
    def read_timeout(self) -> float | None:
        return None

    async def do_request(
        self,
        url,
        method,
        request_data=None,
        read_timeout=None,
        write_timeout=None,
        connect_timeout=None,
        pool_timeout=None,
    ):
        import pytest

        pytest.fail(f"OfflineRequest: bot tried to make a network call to {url}")


TEST_USER = User(id=99999, is_bot=False, first_name="TestDoctor")
TEST_CHAT = Chat(id=99999, type=Chat.PRIVATE)
BOT_USER = User(id=12345, is_bot=True, first_name="PortfolioGuru", username="portfolio_guru_bot")


def make_message(text: str, user: User | None = None, chat: Chat | None = None) -> Message:
    """Build a real PTB Message object."""
    user = user or TEST_USER
    chat = chat or TEST_CHAT
    msg = Message(
        message_id=_next_msg_id(),
        date=datetime.datetime.now(tz=datetime.timezone.utc),
        chat=chat,
        from_user=user,
        text=text,
    )
    return msg


def make_text_update(text: str, user: User | None = None) -> Update:
    """Build a real Update containing a text message."""
    msg = make_message(text, user=user)
    return Update(update_id=_next_update_id(), message=msg)


def make_callback_update(
    data: str,
    user: User | None = None,
    message_text: str = "prev",
) -> Update:
    """Build a real Update containing a CallbackQuery."""
    user = user or TEST_USER
    chat = TEST_CHAT
    # The message that the button was attached to
    msg = Message(
        message_id=_next_msg_id(),
        date=datetime.datetime.now(tz=datetime.timezone.utc),
        chat=chat,
        from_user=BOT_USER,
        text=message_text,
    )
    cq = CallbackQuery(
        id=str(_next_update_id()),
        from_user=user,
        chat_instance=str(chat.id),
        data=data,
        message=msg,
    )
    return Update(update_id=_next_update_id(), callback_query=cq)


def make_command_update(command: str, user: User | None = None, args: list[str] | None = None) -> Update:
    """Build a real Update for a /command. Includes proper entity so PTB recognises it."""
    from telegram import MessageEntity

    full_text = f"/{command}" + (" " + " ".join(args) if args else "")
    user = user or TEST_USER
    msg = Message(
        message_id=_next_msg_id(),
        date=datetime.datetime.now(tz=datetime.timezone.utc),
        chat=TEST_CHAT,
        from_user=user,
        text=full_text,
        entities=[MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(f"/{command}"))],
    )
    return Update(update_id=_next_update_id(), message=msg)


def build_offline_application():
    """Use production registration, replacing only transport and persistence.

    Never initialize the polling updater or inspect/purge a real persistence file.
    The caller owns response capture and per-scenario external-effect stubs.
    """
    from unittest.mock import patch
    from telegram.ext import Application, ApplicationBuilder, DictPersistence
    import bot

    persistence = DictPersistence()
    app = (Application.builder().token("0:FAKE").updater(None).job_queue(None)
           .persistence(persistence).request(OfflineRequest())
           .get_updates_request(OfflineRequest()).build())
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "0:FAKE",
                                   "PG_ALLOW_NON_EU_EXTRACTION": "1"}), \
         patch("bot.os.makedirs"), \
         patch("clinical_persistence.purge_existing_file", return_value={"status": "offline"}), \
         patch("clinical_persistence.ClinicalScrubbingPersistence", return_value=persistence), \
         patch.object(ApplicationBuilder, "build", return_value=app):
        return bot.build_application()


def isolate_bot_storage(monkeypatch, tmp_path, *, audit_path=None):
    """Keep production storage boundaries real but scoped to synthetic test data."""
    from sqlmodel import SQLModel, create_engine
    from sqlalchemy.pool import StaticPool
    import credentials
    import profile_store
    import usage
    import kaizen_index
    import supervisor_bot

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(credentials, "engine", engine)
    monkeypatch.setattr(profile_store, "engine", engine)
    monkeypatch.setattr(usage, "DB_PATH", str(tmp_path / "usage.db"))
    monkeypatch.setattr(kaizen_index, "DB_PATH", str(tmp_path / "usage.db"))
    monkeypatch.setattr(supervisor_bot, "NOTIFICATION_CACHE_DIR", tmp_path / "supervisor")
    for key, filename in {
        "PORTFOLIO_GURU_FUNNEL_LOG_PATH": "funnel.ndjson",
        "PORTFOLIO_GURU_FILING_LOG_PATH": "filing.ndjson",
        "PORTFOLIO_GURU_DOGFOOD_AUDIT_PATH": "audit.ndjson",
        "PORTFOLIO_GURU_DRAFT_BACKUP_DIR": "drafts",
        "PORTFOLIO_GURU_HEALTH_PROFILE_PATH": "health.json",
    }.items():
        monkeypatch.setenv(key, str(audit_path if key == "PORTFOLIO_GURU_DOGFOOD_AUDIT_PATH"
                                   and audit_path else tmp_path / filename))


def unstamp(callback_data):
    """Drop the per-case token from Save/Cancel buttons (bot._case_token)."""
    return re.sub(r"^(APPROVE|CANCEL)\|draft\|[0-9a-f]+$", r"\1|draft", callback_data or "")
