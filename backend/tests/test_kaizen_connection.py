"""How a user is connected to Kaizen: stored password vs passwordless.

In-memory databases only; no Kaizen, Telegram or Supabase traffic.
"""

from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import profile_store as _profile_store

# Captured at import, before conftest's autouse stub replaces it per test.
_REAL_GET_KAIZEN_CONNECTION = _profile_store.get_kaizen_connection


def _memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def stores(monkeypatch):
    import credentials
    import profile_store
    import store

    cred_engine = _memory_engine()
    SQLModel.metadata.create_all(cred_engine)
    monkeypatch.setattr(credentials, "engine", cred_engine)
    monkeypatch.setattr(credentials, "FERNET_KEY", Fernet.generate_key())
    monkeypatch.setattr(credentials, "_invalidate_cached_kaizen_session", lambda uid: None)
    monkeypatch.setattr(credentials, "_clear_account_scoped_state", lambda uid: None)
    profile_engine = _memory_engine()
    SQLModel.metadata.create_all(profile_engine)
    monkeypatch.setattr(profile_store, "engine", profile_engine)
    monkeypatch.setattr(profile_store, "get_kaizen_connection", _REAL_GET_KAIZEN_CONNECTION)
    mirror_deletes = []
    import supabase_sync

    monkeypatch.setattr(supabase_sync, "mirror_credentials", lambda *a, **k: None)
    monkeypatch.setattr(supabase_sync, "delete_mirrored_credentials", mirror_deletes.append)
    # store.py re-exports credentials' functions; keep them pointing at the patched module.
    monkeypatch.setattr(store, "has_credentials", credentials.has_credentials)
    import kaizen_connection

    monkeypatch.setattr(kaizen_connection, "has_credentials", credentials.has_credentials)
    return {"mirror_deletes": mirror_deletes}


def test_connection_state_distinguishes_password_passwordless_and_none(stores):
    import credentials
    import kaizen_connection as kc

    assert kc.connection_state(1) == kc.NONE
    assert kc.is_connected(1) is False

    credentials.store_credentials(1, "doc@example.com", "secret")
    assert kc.connection_state(1) == kc.PASSWORD

    kc.mark_passwordless(2)
    assert kc.connection_state(2) == kc.PASSWORDLESS
    assert kc.is_connected(2) is True
    assert kc.is_passwordless(2) is True


def test_choosing_passwordless_deletes_the_stored_password_everywhere(stores):
    import credentials
    import kaizen_connection as kc

    credentials.store_credentials(7, "doc@example.com", "secret")

    kc.mark_passwordless(7)

    assert credentials.get_credentials(7) is None
    assert stores["mirror_deletes"] == [7]
    assert kc.connection_state(7) == kc.PASSWORDLESS


def test_passwordless_is_offered_only_when_switched_on_for_that_user(monkeypatch):
    import kaizen_connection as kc

    monkeypatch.delenv("PG_ENABLE_PASSWORDLESS_CONNECT", raising=False)
    monkeypatch.setenv("PG_PASSWORDLESS_ALLOWLIST", "*")
    assert kc.passwordless_offered_to(5) is False

    monkeypatch.setenv("PG_ENABLE_PASSWORDLESS_CONNECT", "1")
    monkeypatch.setenv("PG_PASSWORDLESS_ALLOWLIST", "6912896590, 42")
    assert kc.passwordless_offered_to(42) is True
    assert kc.passwordless_offered_to(5) is False

    monkeypatch.setenv("PG_PASSWORDLESS_ALLOWLIST", "*")
    assert kc.passwordless_offered_to(5) is True

    monkeypatch.setenv("PG_PASSWORDLESS_ALLOWLIST", "")
    assert kc.passwordless_offered_to(5) is False


def test_kept_session_needs_cookies(monkeypatch):
    import kaizen_connection as kc
    import kaizen_form_filer

    monkeypatch.setattr(kaizen_form_filer, "load_session_state", lambda uid, username=None: None)
    assert kc.has_kept_session(3) is False
    monkeypatch.setattr(
        kaizen_form_filer, "load_session_state", lambda uid, username=None: {"cookies": []}
    )
    assert kc.has_kept_session(3) is False
    monkeypatch.setattr(
        kaizen_form_filer,
        "load_session_state",
        lambda uid, username=None: {"cookies": [{"name": "s"}]},
    )
    assert kc.has_kept_session(3) is True


def test_login_never_submits_empty_details_to_rcem():
    """A lapsed passwordless session must not become a blank RCEM login."""
    import kaizen_form_filer

    class Page:
        async def goto(self, *args, **kwargs):
            raise AssertionError("login tried to reach the RCEM portal with no details")

    assert asyncio.run(kaizen_form_filer._login(Page(), "", "")) is False
