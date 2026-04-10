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
    SQLModel.metadata.create_all(engine)

    credentials.store_credentials(123, "doctor@example.com", "secret-pass")

    assert credentials.get_credentials(123) == ("doctor@example.com", "secret-pass")


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
