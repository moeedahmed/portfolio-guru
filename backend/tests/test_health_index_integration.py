"""Health + settings integration with the Kaizen Portfolio Index.

These tests pin two contracts:

1. ``/health`` prefers the indexed Kaizen evidence over ``get_case_history``
   when the index has rows for the user, and falls back to the existing
   case-history path when it does not (the priority spelled out in
   ``docs/PORTFOLIO_HEALTH_SPEC.md`` Phase 2).
2. ``/settings`` surfaces a Kaizen sync status row and a guarded refresh
   workflow when the user is connected.

Offline only: no Kaizen, Playwright, CDP, credentials, or network.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from health_models import HealthProfile, Pathway
from tests.bot_simulator import BotSimulator


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

    buttons = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "ACTION|refresh_portfolio" in buttons


def test_settings_hides_refresh_button_when_kaizen_not_connected(
    isolated_health_store, monkeypatch
):
    import bot
    from kaizen_index import KaizenSyncStatus

    monkeypatch.setattr(bot, "get_curriculum", lambda _uid: "2025")
    monkeypatch.setattr(bot, "get_training_level", lambda _uid: "ST5")
    monkeypatch.setattr(bot, "get_voice_profile", lambda _uid: None)

    text, keyboard = bot._settings_view_components(
        4242,
        tier="pro_plus",
        used=0,
        connected=False,
        kaizen_sync=KaizenSyncStatus(last_run=None, items_indexed=0),
    )

    buttons = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "Kaizen: not connected" in text
    assert "ACTION|refresh_portfolio" not in buttons


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


@pytest.mark.asyncio
async def test_refresh_portfolio_shows_read_only_confirmation(monkeypatch):
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    sync = AsyncMock()
    monkeypatch.setattr(bot, "sync_kaizen_portfolio_index_for_user", sync)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|refresh_portfolio"),
        context,
    )

    text = sim.get_last_text()
    assert "Refresh portfolio from Kaizen" in text
    assert "no saving or submitting" in text
    assert ("✅ Refresh now", "ACTION|confirm_refresh_portfolio") in sim.get_last_buttons()
    assert ("🔙 Back to settings", "ACTION|settings") in sim.get_last_buttons()
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_refresh_portfolio_runs_sync_and_shows_success(monkeypatch):
    import bot
    from kaizen_index import IndexRunRow, KaizenSyncStatus

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(
        bot,
        "sync_kaizen_portfolio_index_for_user",
        AsyncMock(
            return_value=SimpleNamespace(
                status="ok",
                rows_seen=12,
                rows_written=10,
                rows_drifted=0,
                notes=[],
            )
        ),
    )
    monkeypatch.setattr(
        bot,
        "_safe_kaizen_sync_status",
        AsyncMock(
            return_value=KaizenSyncStatus(
                last_run=IndexRunRow(
                    id=1,
                    user_id="4242",
                    started_at="2026-06-01T12:00:00",
                    finished_at="2026-06-01T12:01:00",
                    status="ok",
                    rows_seen=12,
                    rows_written=10,
                    rows_drifted=0,
                ),
                items_indexed=99,
            )
        ),
    )

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|confirm_refresh_portfolio"),
        context,
    )

    bot.sync_kaizen_portfolio_index_for_user.assert_awaited_once_with(4242)
    text = sim.get_last_text()
    assert "Portfolio refreshed" in text
    assert "Read from Kaizen: 12 items" in text
    assert "Portfolio Guru now has: 99 indexed items" in text
    assert ("📊 View portfolio health", "ACTION|health") in sim.get_last_buttons()
    assert ("🔙 Back to settings", "ACTION|settings") in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_confirm_refresh_portfolio_handles_auth_required(monkeypatch):
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(
        bot,
        "sync_kaizen_portfolio_index_for_user",
        AsyncMock(
            return_value=SimpleNamespace(
                status="auth_required",
                rows_seen=0,
                rows_written=0,
                rows_drifted=0,
                notes=["login needed"],
            )
        ),
    )
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|confirm_refresh_portfolio"),
        context,
    )

    text = sim.get_last_text()
    assert "Kaizen needs reconnecting" in text
    assert ("🔗 Reconnect Kaizen", "ACTION|setup") in sim.get_last_buttons()
    assert ("🔙 Back to settings", "ACTION|settings") in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_confirm_refresh_portfolio_handles_failure_without_traceback(monkeypatch):
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(
        bot,
        "sync_kaizen_portfolio_index_for_user",
        AsyncMock(side_effect=RuntimeError("secret low-level failure")),
    )
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|confirm_refresh_portfolio"),
        context,
    )

    text = sim.get_last_text()
    assert "Refresh did not complete" in text
    assert "secret low-level failure" not in text
    assert ("🔄 Try again", "ACTION|refresh_portfolio") in sim.get_last_buttons()
