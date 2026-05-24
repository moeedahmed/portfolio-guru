"""Tests for the new ``kaizen_role`` column + helpers on profile_store.

Isolated from the on-disk SQLite by swapping ``profile_store.engine`` for
an in-memory one, same pattern as ``test_smoke.test_profile_store_roundtrip_succeeds``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
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
