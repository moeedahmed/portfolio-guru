import importlib
import sys

import pytest
from cryptography.fernet import Fernet
from sqlmodel import SQLModel, create_engine
from sqlalchemy.pool import StaticPool


def _memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_fernet_roundtrip_succeeds(monkeypatch):
    import credentials

    engine = _memory_engine()
    monkeypatch.setattr(credentials, "engine", engine)
    monkeypatch.setattr(credentials, "FERNET_KEY", Fernet.generate_key())
    invalidated = []
    monkeypatch.setattr(credentials, "_invalidate_cached_kaizen_session", invalidated.append)
    cleared = []
    monkeypatch.setattr(credentials, "_clear_account_scoped_state", cleared.append)
    SQLModel.metadata.create_all(engine)

    credentials.store_credentials(123, "doctor@example.com", "secret-pass")

    assert credentials.get_credentials(123) == ("doctor@example.com", "secret-pass")
    assert invalidated == [123]
    assert cleared == []


def test_store_credentials_invalidates_cache_on_account_rotation(monkeypatch):
    import credentials

    engine = _memory_engine()
    monkeypatch.setattr(credentials, "engine", engine)
    monkeypatch.setattr(credentials, "FERNET_KEY", Fernet.generate_key())
    invalidated = []
    monkeypatch.setattr(credentials, "_invalidate_cached_kaizen_session", invalidated.append)
    cleared = []
    monkeypatch.setattr(credentials, "_clear_account_scoped_state", cleared.append)
    SQLModel.metadata.create_all(engine)

    credentials.store_credentials(123, "moeed@example.com", "first-pass")
    credentials.store_credentials(123, "haris@example.com", "second-pass")

    assert credentials.get_credentials(123) == ("haris@example.com", "second-pass")
    assert invalidated == [123, 123]
    assert cleared == [123]


def test_bot_import_with_env_succeeds(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    monkeypatch.setenv("FERNET_SECRET_KEY", Fernet.generate_key().decode())

    sys.modules.pop("bot", None)
    bot = importlib.import_module("bot")

    assert bot is not None


def test_missing_fernet_key_raises(monkeypatch):
    import credentials

    monkeypatch.setattr(credentials, "FERNET_KEY", b"")

    with pytest.raises(ValueError, match="FERNET_SECRET_KEY"):
        credentials.get_credentials(123)


def test_profile_store_roundtrip_succeeds(monkeypatch):
    import profile_store

    engine = _memory_engine()
    monkeypatch.setattr(profile_store, "engine", engine)
    SQLModel.metadata.create_all(engine)

    profile_store.store_training_level(456, "ST6")

    assert profile_store.get_training_level(456) == "ST6"


def test_token_redaction_in_logging(monkeypatch):
    import logging
    # Ensure bot module is loaded so monkeypatch is applied
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ12345678")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    monkeypatch.setenv("FERNET_SECRET_KEY", Fernet.generate_key().decode())

    sys.modules.pop("bot", None)
    import bot

    # Create a custom log handler to capture logs
    log_records = []
    class CaptureHandler(logging.Handler):
        def emit(self, record):
            log_records.append(record.getMessage())

    handler = CaptureHandler()
    logger = logging.getLogger("test_redact")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Log a message containing a raw token
    token = "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ12345678"
    logger.info(f"Connecting to https://api.telegram.org/bot{token}/getUpdates")

    # Assert it was redacted
    assert len(log_records) == 1
    assert token not in log_records[0]
    assert "<REDACTED_TELEGRAM_TOKEN>" in log_records[0]


def test_token_redaction_without_env_token():
    import bot

    token = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    redacted = bot._redact_token_string(f"https://api.telegram.org/bot{token}/getUpdates")

    assert token not in redacted
    assert "bot<REDACTED_TELEGRAM_TOKEN>/getUpdates" in redacted


def test_token_redaction_preserves_mapping_log_args(monkeypatch):
    import logging

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ12345678")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    monkeypatch.setenv("FERNET_SECRET_KEY", Fernet.generate_key().decode())

    sys.modules.pop("bot", None)
    import bot  # noqa: F401

    records = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("test_mapping_redact")
    logger.setLevel(logging.INFO)
    logger.addHandler(CaptureHandler())

    logger.info("Retention purge: %(status)s", {"status": "ok"})

    assert records == ["Retention purge: ok"]
