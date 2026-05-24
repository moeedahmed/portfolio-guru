"""Tests for the per-user supervisor notification cache.

The cache is the bridge between two stateless layers:

* The scheduler that produces :class:`supervisor_workflow.SupervisorNotificationPayload`
  objects and dispatches Telegram messages with Open / Skip / Later buttons.
* The callback handlers that fire when those buttons are tapped, which must
  recover the original ``ticket_url`` (and other PHI-free fields) without
  re-polling Kaizen.

A user may tap Open after a bot restart or hours after the notification,
so the cache must survive process death. A JSON file per Telegram user is
plenty — small, atomic, easy to inspect by hand for debugging.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import supervisor_notification_cache as cache_mod
from supervisor_workflow import SupervisorNotificationPayload


def _payload(uuid: str, *, form_type: str | None = "CBD") -> SupervisorNotificationPayload:
    return SupervisorNotificationPayload(
        ticket_uuid=uuid,
        ticket_url=f"https://kaizenep.com/events/view-section/{uuid}",
        form_type=form_type,
        redacted_title="CBD - Case Based Discussion (2025 update)",
        status="unfilled",
    )


def test_lookup_returns_none_when_cache_empty(tmp_path: Path):
    assert cache_mod.lookup(tmp_path, telegram_user_id=1, ticket_uuid="missing") is None


def test_remember_then_lookup_round_trip(tmp_path: Path):
    payload = _payload("uuid-a")

    cache_mod.remember(tmp_path, telegram_user_id=42, payload=payload)
    recovered = cache_mod.lookup(tmp_path, telegram_user_id=42, ticket_uuid="uuid-a")

    assert recovered == payload


def test_remember_persists_across_module_reload(tmp_path: Path):
    """The cache must survive a bot restart — only the on-disk file matters."""
    payload = _payload("uuid-b")
    cache_mod.remember(tmp_path, telegram_user_id=7, payload=payload)

    # Read from a fresh process (simulated by direct file inspection).
    expected_path = tmp_path / "supervisor_notifications_7.json"
    assert expected_path.exists()

    recovered = cache_mod.lookup(tmp_path, telegram_user_id=7, ticket_uuid="uuid-b")
    assert recovered is not None
    assert recovered.ticket_url.endswith("uuid-b")


def test_remember_isolates_users(tmp_path: Path):
    cache_mod.remember(tmp_path, telegram_user_id=100, payload=_payload("uuid-shared"))
    cache_mod.remember(tmp_path, telegram_user_id=200, payload=_payload("uuid-shared", form_type="DOPS"))

    p100 = cache_mod.lookup(tmp_path, telegram_user_id=100, ticket_uuid="uuid-shared")
    p200 = cache_mod.lookup(tmp_path, telegram_user_id=200, ticket_uuid="uuid-shared")

    assert p100 is not None and p100.form_type == "CBD"
    assert p200 is not None and p200.form_type == "DOPS"


def test_forget_removes_the_entry(tmp_path: Path):
    cache_mod.remember(tmp_path, telegram_user_id=1, payload=_payload("uuid-x"))
    cache_mod.remember(tmp_path, telegram_user_id=1, payload=_payload("uuid-y"))

    cache_mod.forget(tmp_path, telegram_user_id=1, ticket_uuid="uuid-x")

    assert cache_mod.lookup(tmp_path, telegram_user_id=1, ticket_uuid="uuid-x") is None
    # Sibling entry untouched.
    assert cache_mod.lookup(tmp_path, telegram_user_id=1, ticket_uuid="uuid-y") is not None


def test_forget_unknown_uuid_is_safe(tmp_path: Path):
    """Tapping Skip twice or after the cache cleared must never raise."""
    cache_mod.forget(tmp_path, telegram_user_id=1, ticket_uuid="never-existed")
    # Still safe when the file exists but the uuid does not.
    cache_mod.remember(tmp_path, telegram_user_id=1, payload=_payload("uuid-other"))
    cache_mod.forget(tmp_path, telegram_user_id=1, ticket_uuid="never-existed")


def test_remember_overwrites_existing_uuid(tmp_path: Path):
    cache_mod.remember(tmp_path, telegram_user_id=1, payload=_payload("uuid-z", form_type="CBD"))
    cache_mod.remember(tmp_path, telegram_user_id=1, payload=_payload("uuid-z", form_type="DOPS"))

    recovered = cache_mod.lookup(tmp_path, telegram_user_id=1, ticket_uuid="uuid-z")
    assert recovered is not None
    assert recovered.form_type == "DOPS"


def test_list_pending_returns_all_cached_payloads(tmp_path: Path):
    cache_mod.remember(tmp_path, telegram_user_id=1, payload=_payload("uuid-1"))
    cache_mod.remember(tmp_path, telegram_user_id=1, payload=_payload("uuid-2", form_type="DOPS"))

    pending = cache_mod.list_pending(tmp_path, telegram_user_id=1)

    assert {p.ticket_uuid for p in pending} == {"uuid-1", "uuid-2"}


def test_list_pending_empty_when_no_cache_file(tmp_path: Path):
    assert cache_mod.list_pending(tmp_path, telegram_user_id=999) == []


def test_cache_corruption_returns_empty_results(tmp_path: Path):
    """A garbled cache file must not crash the bot — just behave as empty."""
    cache_path = tmp_path / "supervisor_notifications_5.json"
    cache_path.write_text("{not valid json", encoding="utf-8")

    assert cache_mod.lookup(tmp_path, telegram_user_id=5, ticket_uuid="any") is None
    assert cache_mod.list_pending(tmp_path, telegram_user_id=5) == []
