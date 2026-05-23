"""Tests for assessor ticket state persistence."""

from __future__ import annotations

import json

import pytest

import state_tracker


def test_empty_state_has_no_seen_tickets(tmp_path):
    state = state_tracker.TrackedState(path=tmp_path / "state.json")

    assert state.is_new_ticket("uuid-1") is True
    assert state.seen_tickets == {}


def test_mark_seen_records_ticket_status(tmp_path):
    state = state_tracker.TrackedState(path=tmp_path / "state.json")

    state.mark_seen("uuid-1", status="unfilled")

    assert state.is_new_ticket("uuid-1") is False
    assert state.seen_tickets["uuid-1"] == "unfilled"


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = state_tracker.TrackedState(path=path)
    state.mark_seen("uuid-1", status="unfilled")
    state.mark_seen("uuid-2", status="filled")
    state.save()

    reloaded = state_tracker.TrackedState.load(path)

    assert reloaded.is_new_ticket("uuid-1") is False
    assert reloaded.is_new_ticket("uuid-2") is False
    assert reloaded.seen_tickets["uuid-1"] == "unfilled"
    assert reloaded.seen_tickets["uuid-2"] == "filled"


def test_load_missing_file_returns_empty_state(tmp_path):
    state = state_tracker.TrackedState.load(tmp_path / "does-not-exist.json")

    assert state.seen_tickets == {}
    assert state.is_new_ticket("uuid-1") is True


def test_load_malformed_json_returns_empty_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")

    state = state_tracker.TrackedState.load(path)

    assert state.seen_tickets == {}


def test_mark_seen_updates_status_on_repeat_call(tmp_path):
    state = state_tracker.TrackedState(path=tmp_path / "state.json")
    state.mark_seen("uuid-1", status="unfilled")

    state.mark_seen("uuid-1", status="filled")

    assert state.seen_tickets["uuid-1"] == "filled"
    assert state.is_new_ticket("uuid-1") is False


def test_save_creates_parent_directory(tmp_path):
    nested = tmp_path / "deep" / "nested" / "state.json"
    state = state_tracker.TrackedState(path=nested)
    state.mark_seen("uuid-1", status="unfilled")

    state.save()

    assert nested.exists()
    payload = json.loads(nested.read_text(encoding="utf-8"))
    assert payload["seen_tickets"]["uuid-1"] == "unfilled"


def test_filter_new_tickets_returns_only_unseen(tmp_path):
    state = state_tracker.TrackedState(path=tmp_path / "state.json")
    state.mark_seen("uuid-1", status="unfilled")

    new_tickets = state.filter_new(["uuid-1", "uuid-2", "uuid-3"])

    assert new_tickets == ["uuid-2", "uuid-3"]


def test_filter_new_tickets_returns_empty_when_all_seen(tmp_path):
    state = state_tracker.TrackedState(path=tmp_path / "state.json")
    state.mark_seen("uuid-1", status="unfilled")
    state.mark_seen("uuid-2", status="unfilled")

    assert state.filter_new(["uuid-1", "uuid-2"]) == []
