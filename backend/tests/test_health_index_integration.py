"""Health + settings integration with the Kaizen Portfolio Index.

These tests pin two contracts:

1. ``/health`` prefers the indexed Kaizen evidence over ``get_case_history``
   when the index has rows for the user, and falls back to the existing
   case-history path when it does not (the priority spelled out in
   ``docs/PORTFOLIO_HEALTH_SPEC.md`` Phase 2).
2. ``/settings`` surfaces a read-only Kaizen sync status row when the
   caller supplies a status, with no refresh button or live action.

Offline only: no Kaizen, Playwright, CDP, credentials, or network.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from health_models import HealthProfile, Pathway


@pytest.fixture
def kaizen_index(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "kaizen_index_health.db"))
    import kaizen_index
    return importlib.reload(kaizen_index)


@pytest.fixture
def isolated_health_store(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PORTFOLIO_GURU_HEALTH_PROFILE_PATH",
        str(tmp_path / "health_profiles.json"),
    )
    import health_profile_store
    return importlib.reload(health_profile_store)


def _profile(user_id: int, pathway: Pathway) -> HealthProfile:
    now = datetime.now(UTC)
    return HealthProfile(
        user_id=str(user_id),
        pathway=pathway,
        pathway_config={},
        created_at=now,
        updated_at=now,
    )


def _evidence_row(kaizen_index, **overrides):
    base = dict(
        id="event-cbd-1",
        user_id="9001",
        surface="event",
        event_type="CBD",
        category="Assessments",
        state="complete",
        date_occurred_on="2026-05-20",
        end_date=None,
        description="Resus case, supervised",
        linked_kc_tags=["Higher SLO1 KC1"],
        filled_in_by="Trainee",
        filled_in_on="2026-05-21",
        parent_event_id=None,
        detail_url=None,
    )
    base.update(overrides)
    return kaizen_index.EvidenceItemRow(**base)


# ── /health source priority ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_prefers_indexed_evidence_over_case_history(
    kaizen_index, isolated_health_store, monkeypatch
):
    import bot

    user_id = 9001
    # Indexed rows exist for this user: CBD + QIAT
    await kaizen_index.upsert_evidence_item(
        _evidence_row(kaizen_index, id="cbd-1", event_type="CBD", user_id=str(user_id))
    )
    await kaizen_index.upsert_evidence_item(
        _evidence_row(kaizen_index, id="qiat-1", event_type="QIAT", user_id=str(user_id))
    )

    # case_history would otherwise contribute a TEACH_OBS, but indexed
    # evidence wins — we should see no teaching in the snapshot.
    case_history = [
        {
            "form_type": "TEACH_OBS",
            "filed_at": "2026-05-10 09:00:00",
            "status": "filed",
            "telegram_user_id": user_id,
        }
    ]
    monkeypatch.setattr(bot, "get_case_history", AsyncMock(return_value=case_history))

    items, history, source = await bot._resolve_health_evidence(user_id)
    assert source == "kaizen_index"
    assert {item.form_type for item in items} == {"CBD", "QIAT"}
    # history is still returned so the LLM ARCP narrative path keeps working.
    assert history == case_history


@pytest.mark.asyncio
async def test_health_falls_back_to_case_history_when_index_empty(
    kaizen_index, isolated_health_store, monkeypatch
):
    import bot

    user_id = 9002
    case_history = [
        {
            "form_type": "CBD",
            "filed_at": "2026-05-15 09:00:00",
            "status": "filed",
            "telegram_user_id": user_id,
        }
    ]
    monkeypatch.setattr(bot, "get_case_history", AsyncMock(return_value=case_history))

    items, history, source = await bot._resolve_health_evidence(user_id)
    assert source == "case_history"
    assert len(items) == 1
    assert items[0].form_type == "CBD"
    assert history == case_history


@pytest.mark.asyncio
async def test_run_health_analysis_uses_indexed_source_when_history_empty(
    kaizen_index, isolated_health_store, monkeypatch
):
    """Indexed evidence alone is enough to produce a verdict; history may be empty."""
    import bot
    import sys

    user_id = 9003
    await kaizen_index.upsert_evidence_item(
        _evidence_row(kaizen_index, id="cbd-1", event_type="CBD", user_id=str(user_id))
    )
    monkeypatch.setattr(bot, "get_case_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        bot, "get_health_profile", lambda _uid: _profile(user_id, Pathway.cesr_portfolio)
    )
    monkeypatch.setattr(bot, "get_training_level", lambda _uid: "ST6")
    monkeypatch.setattr(bot, "analyse_portfolio_health", AsyncMock())

    async def generate_health_chart_async(_uid):
        return None

    monkeypatch.setitem(
        sys.modules,
        "portfolio_chart",
        SimpleNamespace(generate_health_chart_async=generate_health_chart_async),
    )

    sent: dict[str, str] = {}

    await bot._run_health_analysis(
        user_id=user_id,
        chat=SimpleNamespace(send_action=AsyncMock()),
        send_progress=AsyncMock(),
        send_result=AsyncMock(side_effect=lambda text, reply_markup: sent.setdefault("text", text)),
        send_photo_fn=AsyncMock(),
        fail_fn=AsyncMock(),
    )

    text = sent["text"]
    assert "*Portfolio Health — CESR*" in text
    assert "No cases filed yet" not in text
    assert "WPBA count: 1" in text


# ── /settings Kaizen sync row ───────────────────────────────────────────────


def test_settings_includes_kaizen_sync_row_when_status_provided(
    isolated_health_store, monkeypatch
):
    import bot
    from kaizen_index import IndexRunRow, KaizenSyncStatus

    monkeypatch.setattr(bot, "get_curriculum", lambda _uid: "2025")
    monkeypatch.setattr(bot, "get_training_level", lambda _uid: "ST5")
    monkeypatch.setattr(bot, "get_voice_profile", lambda _uid: None)

    status = KaizenSyncStatus(
        last_run=IndexRunRow(
            id=1,
            user_id="4242",
            started_at="2026-06-01T11:30:00",
            finished_at="2026-06-01T11:38:00",
            status="ok",
            rows_seen=412,
            rows_written=412,
            rows_drifted=0,
        ),
        items_indexed=412,
    )

    text, keyboard = bot._settings_view_components(
        4242,
        tier="pro_plus",
        used=0,
        connected=True,
        kaizen_sync=status,
    )

    assert "Kaizen sync" in text
    assert "Items indexed: 412" in text
    assert "(ok)" in text

    # Read-only: no refresh button anywhere in the keyboard.
    buttons = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert all("kaizen_sync" not in (cb or "") for cb in buttons)
    assert all("refresh" not in (cb or "").lower() for cb in buttons)


def test_settings_shows_not_synced_yet_when_no_run_exists(
    isolated_health_store, monkeypatch
):
    import bot
    from kaizen_index import KaizenSyncStatus

    monkeypatch.setattr(bot, "get_curriculum", lambda _uid: "2025")
    monkeypatch.setattr(bot, "get_training_level", lambda _uid: "ST5")
    monkeypatch.setattr(bot, "get_voice_profile", lambda _uid: None)

    status = KaizenSyncStatus(last_run=None, items_indexed=0)

    text, _ = bot._settings_view_components(
        4242,
        tier="pro_plus",
        used=0,
        connected=True,
        kaizen_sync=status,
    )

    assert "Kaizen sync: not synced yet" in text


def test_settings_omits_kaizen_sync_row_when_unavailable(
    isolated_health_store, monkeypatch
):
    """Existing call sites that don't pass ``kaizen_sync`` still render cleanly."""
    import bot

    monkeypatch.setattr(bot, "get_curriculum", lambda _uid: "2025")
    monkeypatch.setattr(bot, "get_training_level", lambda _uid: "ST5")
    monkeypatch.setattr(bot, "get_voice_profile", lambda _uid: None)

    text, _ = bot._settings_view_components(
        4242,
        tier="pro_plus",
        used=0,
        connected=True,
    )

    assert "Kaizen sync" not in text
