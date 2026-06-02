"""Tests for the new ``kaizen_role`` column + helpers on profile_store.

Isolated from the on-disk SQLite by swapping ``profile_store.engine`` for
an in-memory one, same pattern as ``test_smoke.test_profile_store_roundtrip_succeeds``.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, select
from sqlmodel import SQLModel, create_engine


def _memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def profile_store_module(monkeypatch):
    import profile_store

    engine = _memory_engine()
    monkeypatch.setattr(profile_store, "engine", engine)
    SQLModel.metadata.create_all(engine)
    return profile_store


def test_get_kaizen_role_returns_none_when_unset(profile_store_module):
    assert profile_store_module.get_kaizen_role(101) is None


def test_store_and_get_kaizen_role_round_trip(profile_store_module):
    profile_store_module.store_kaizen_role(101, "assessor")

    assert profile_store_module.get_kaizen_role(101) == "assessor"


def test_store_kaizen_role_overwrites_previous_value(profile_store_module):
    profile_store_module.store_kaizen_role(202, "trainee")
    profile_store_module.store_kaizen_role(202, "assessor")

    assert profile_store_module.get_kaizen_role(202) == "assessor"


def test_store_kaizen_role_accepts_none(profile_store_module):
    profile_store_module.store_kaizen_role(303, "trainee")
    profile_store_module.store_kaizen_role(303, None)

    assert profile_store_module.get_kaizen_role(303) is None


def test_store_kaizen_role_does_not_clobber_training_level(profile_store_module):
    profile_store_module.store_training_level(404, "HIGHER")
    profile_store_module.store_kaizen_role(404, "assessor")

    assert profile_store_module.get_training_level(404) == "HIGHER"
    assert profile_store_module.get_kaizen_role(404) == "assessor"


def test_interleaved_profile_writes_keep_users_isolated(profile_store_module):
    profile_store_module.store_kaizen_role(901, "accs_intermediate")
    profile_store_module.store_training_level(902, "HIGHER")
    profile_store_module.store_training_level(901, "INTERMEDIATE")
    profile_store_module.store_kaizen_role(902, "hst")

    assert profile_store_module.get_kaizen_role(901) == "accs_intermediate"
    assert profile_store_module.get_training_level(901) == "INTERMEDIATE"
    assert profile_store_module.get_kaizen_role(902) == "hst"
    assert profile_store_module.get_training_level(902) == "HIGHER"


def test_profile_and_credential_rows_do_not_cross_user_boundaries(monkeypatch, profile_store_module):
    import credentials

    credentials_engine = _memory_engine()
    monkeypatch.setattr(credentials, "engine", credentials_engine)
    monkeypatch.setattr(credentials, "FERNET_KEY", Fernet.generate_key())
    SQLModel.metadata.create_all(credentials_engine)

    profile_store_module.store_kaizen_role(1001, "accs_intermediate")
    profile_store_module.store_training_level(1001, "INTERMEDIATE")
    profile_store_module.store_kaizen_role(1002, "hst")
    profile_store_module.store_training_level(1002, "HIGHER")
    credentials.store_credentials(1001, "first@example.com", "first-secret")
    credentials.store_credentials(1002, "second@example.com", "second-secret")

    assert credentials.get_credentials(1001) == ("first@example.com", "first-secret")
    assert credentials.get_credentials(1002) == ("second@example.com", "second-secret")
    assert profile_store_module.get_training_level(1001) == "INTERMEDIATE"
    assert profile_store_module.get_training_level(1002) == "HIGHER"

    with Session(credentials.engine) as session:
        first = session.exec(
            select(credentials.UserCredential).where(
                credentials.UserCredential.telegram_user_id == 1001
            )
        ).first()
        second = session.exec(
            select(credentials.UserCredential).where(
                credentials.UserCredential.telegram_user_id == 1002
            )
        ).first()

    assert first is not None
    assert second is not None
    assert first.kaizen_username_enc != second.kaizen_username_enc


# ── list_users_by_kaizen_role ───────────────────────────────────────────────


def test_list_users_by_kaizen_role_returns_empty_when_none_match(profile_store_module):
    profile_store_module.store_kaizen_role(501, "trainee")
    profile_store_module.store_kaizen_role(502, "trainee")

    assert profile_store_module.list_users_by_kaizen_role("assessor") == []


def test_list_users_by_kaizen_role_returns_only_matching_role(profile_store_module):
    profile_store_module.store_kaizen_role(601, "assessor")
    profile_store_module.store_kaizen_role(602, "trainee")
    profile_store_module.store_kaizen_role(603, "assessor")
    profile_store_module.store_kaizen_role(604, "unknown")

    assert sorted(profile_store_module.list_users_by_kaizen_role("assessor")) == [601, 603]


def test_list_users_by_kaizen_role_excludes_users_with_no_kaizen_role(profile_store_module):
    # User has a training level but no kaizen_role probe ever ran.
    profile_store_module.store_training_level(701, "HIGHER")

    assert profile_store_module.list_users_by_kaizen_role("assessor") == []
    assert profile_store_module.list_users_by_kaizen_role("trainee") == []
