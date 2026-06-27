"""Health + settings integration with the Kaizen Portfolio Index.

These tests pin two contracts:

1. ``/health`` prefers the indexed Kaizen evidence over ``get_case_history``
   when the index has rows for the user, and falls back to the existing
   case-history path when it does not (the priority spelled out in
   ``docs/PORTFOLIO_HEALTH_SPEC.md`` Phase 2).
2. ``/settings`` surfaces a Kaizen evidence status row and a guarded sync
   workflow when the user is connected.

Offline only: no Kaizen, Playwright, CDP, credentials, or network.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from sqlmodel import SQLModel, create_engine

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
async def test_account_switch_clears_previous_account_health_sources(
    tmp_path, monkeypatch, kaizen_index, isolated_health_store
):
    import bot
    import usage

    usage_db = tmp_path / "account_switch_usage.db"
    monkeypatch.setenv("USAGE_DB_PATH", str(usage_db))
    usage = importlib.reload(usage)
    kaizen_index = importlib.reload(kaizen_index)

    monkeypatch.setattr(bot, "delete_portfolio_evidence", usage.delete_portfolio_evidence)
    monkeypatch.setattr(bot, "delete_user_index", kaizen_index.delete_user_index)
    monkeypatch.setattr(bot, "delete_health_profile", isolated_health_store.delete_health_profile)
    monkeypatch.setattr(bot, "list_evidence_items", kaizen_index.list_evidence_items)
    monkeypatch.setattr(bot, "get_case_history", usage.get_case_history)
    monkeypatch.setattr(
        "kaizen_form_filer.invalidate_session_cache",
        lambda *_args, **_kwargs: 0,
    )

    user_id = 9106
    await usage.record_case_filed(user_id, "CBD")
    await usage.save_kc_coverage(user_id, "CBD", ["Higher SLO1 KC1"])
    await kaizen_index.upsert_evidence_item(
        _evidence_row(
            kaizen_index,
            id="old-account-cbd",
            user_id=str(user_id),
            event_type="CBD",
        )
    )
    run_id = await kaizen_index.start_index_run(user_id)
    await kaizen_index.finish_index_run(run_id, "ok", rows_written=1)
    isolated_health_store.save_health_profile(_profile(user_id, Pathway.training_arcp))

    before_items, before_history, before_source = await bot._resolve_health_evidence(user_id)
    assert before_items
    assert before_history
    assert before_source == "kaizen_index"

    await bot._clear_local_portfolio_account_data(user_id, reason="kaizen_account_switch")

    after_items, after_history, after_source = await bot._resolve_health_evidence(user_id)
    assert after_items == []
    assert after_history == []
    assert after_source == "case_history"
    assert await usage.get_kc_stats(user_id) == {
        "total_kcs": 0,
        "slos_covered": 0,
        "slos_total": 12,
        "recent_kcs": [],
    }
    assert await kaizen_index.count_evidence_items(user_id) == 0
    assert await kaizen_index.latest_index_run(user_id) is None
    assert isolated_health_store.get_health_profile(user_id) is None


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

    async def _chart(*_a, **_k):
        return None

    async def _snapshot(*_a, **_k):
        return ""

    monkeypatch.setitem(
        sys.modules,
        "portfolio_chart",
        SimpleNamespace(
            generate_health_chart_async=_chart,
            format_health_activity_snapshot_async=_snapshot,
        ),
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
    assert "*Portfolio Health — CESR / Portfolio Pathway*" in text
    assert "No Portfolio Guru cases filed yet" not in text
    assert "WPBA progress toward 36" in text
    assert "1/36" in text


@pytest.mark.asyncio
async def test_health_evidence_is_strictly_scoped_to_requested_user(
    kaizen_index, isolated_health_store, monkeypatch
):
    import bot

    moeed_id = 9101
    sana_id = 9102
    await kaizen_index.upsert_evidence_item(
        _evidence_row(
            kaizen_index,
            id="moeed-cbd",
            event_type="CBD",
            user_id=str(moeed_id),
            description="Moeed HST CBD evidence",
        )
    )
    await kaizen_index.upsert_evidence_item(
        _evidence_row(
            kaizen_index,
            id="sana-qiat",
            event_type="QIAT",
            user_id=str(sana_id),
            description="Sana CESR QI evidence",
        )
    )
    isolated_health_store.save_health_profile(_profile(moeed_id, Pathway.training_arcp))
    isolated_health_store.save_health_profile(_profile(sana_id, Pathway.cesr_portfolio))

    async def fake_history(user_id, months=6):
        return [
            {
                "form_type": "MINI_CEX" if user_id == moeed_id else "REFLECT_LOG",
                "filed_at": "2026-05-10 09:00:00",
                "status": "filed",
            }
        ]

    monkeypatch.setattr(bot, "get_case_history", fake_history)

    sana_items, sana_history, sana_source = await bot._resolve_health_evidence(sana_id)
    moeed_items, moeed_history, moeed_source = await bot._resolve_health_evidence(moeed_id)

    assert sana_source == "kaizen_index"
    assert {item.form_type for item in sana_items} == {"QIAT"}
    assert "Moeed" not in " ".join(item.summary for item in sana_items)
    assert sana_history == [
        {"form_type": "REFLECT_LOG", "filed_at": "2026-05-10 09:00:00", "status": "filed"}
    ]
    assert isolated_health_store.get_health_profile(sana_id).pathway is Pathway.cesr_portfolio

    assert moeed_source == "kaizen_index"
    assert {item.form_type for item in moeed_items} == {"CBD"}
    assert "Sana" not in " ".join(item.summary for item in moeed_items)
    assert moeed_history == [
        {"form_type": "MINI_CEX", "filed_at": "2026-05-10 09:00:00", "status": "filed"}
    ]
    assert isolated_health_store.get_health_profile(moeed_id).pathway is Pathway.training_arcp


@pytest.mark.asyncio
async def test_kaizen_username_reconnect_clears_previous_account_health_context(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "portfolio_guru.db"
    usage_path = tmp_path / "usage.db"
    monkeypatch.setenv("USAGE_DB_PATH", str(usage_path))
    monkeypatch.setenv(
        "PORTFOLIO_GURU_HEALTH_PROFILE_PATH",
        str(tmp_path / "health_profiles.json"),
    )

    import credentials
    import health_profile_store
    import kaizen_index
    import profile_store
    import usage

    account_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(credentials, "engine", account_engine)
    monkeypatch.setattr(credentials, "FERNET_KEY", Fernet.generate_key())
    monkeypatch.setattr(profile_store, "engine", account_engine)
    monkeypatch.setattr(usage, "DB_PATH", str(usage_path))
    SQLModel.metadata.create_all(account_engine)

    user_id = 9201
    credentials.store_credentials(user_id, "moeed@example.com", "old-password")
    await usage.record_case_filed(user_id, "CBD")
    await kaizen_index.upsert_evidence_item(
        _evidence_row(
            kaizen_index,
            id="moeed-cbd",
            event_type="CBD",
            user_id=str(user_id),
            description="Moeed indexed evidence",
        )
    )
    health_profile_store.save_health_profile(_profile(user_id, Pathway.training_arcp))
    profile_store.store_training_level(user_id, "HIGHER")
    profile_store.store_kaizen_role(user_id, "hst")

    credentials.store_credentials(user_id, "moeed@example.com", "rotated-password")
    assert await usage.get_case_history(user_id, months=6)
    assert await kaizen_index.list_evidence_items(user_id)
    assert health_profile_store.get_health_profile(user_id) is not None
    assert profile_store.get_training_level(user_id) == "HIGHER"

    credentials.store_credentials(user_id, "sana@example.com", "new-password")

    assert credentials.get_credentials(user_id) == ("sana@example.com", "new-password")
    assert await usage.get_case_history(user_id, months=6) == []
    assert await kaizen_index.list_evidence_items(user_id) == []
    assert await kaizen_index.latest_index_run(user_id) is None
    assert health_profile_store.get_health_profile(user_id) is None
    assert profile_store.get_kaizen_role(user_id) is None


# ── /settings Kaizen evidence row ───────────────────────────────────────────


def test_settings_includes_kaizen_sync_status_when_status_provided(
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

    assert "Kaizen evidence" in text
    assert "2026-06-01 12:38 BST" in text
    assert "Items indexed: 412" in text
    assert "synced" in text
    assert "(ok)" not in text

    buttons = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "ACTION|refresh_portfolio" not in buttons


def test_settings_shows_running_sync_as_temporary_in_progress(
    isolated_health_store, monkeypatch
):
    import bot
    from datetime import UTC, datetime
    from kaizen_index import IndexRunRow, KaizenSyncStatus

    monkeypatch.setattr(bot, "get_curriculum", lambda _uid: "2025")
    monkeypatch.setattr(bot, "get_training_level", lambda _uid: "ST5")
    monkeypatch.setattr(bot, "get_voice_profile", lambda _uid: None)

    started_at = datetime.now(UTC).isoformat()
    status = KaizenSyncStatus(
        last_run=IndexRunRow(
            id=1,
            user_id="4242",
            started_at=started_at,
            finished_at=None,
            status="running",
        ),
        items_indexed=12,
    )

    text, _ = bot._settings_view_components(
        4242,
        tier="pro_plus",
        used=0,
        connected=True,
        kaizen_sync=status,
    )

    assert "Kaizen evidence: syncing now" in text
    assert "Items indexed: 12" in text


def test_settings_shows_stale_running_sync_as_timed_out(
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
            started_at="2000-01-01T00:00:00+00:00",
            finished_at=None,
            status="running",
        ),
        items_indexed=12,
    )

    text, _ = bot._settings_view_components(
        4242,
        tier="pro_plus",
        used=0,
        connected=True,
        kaizen_sync=status,
    )

    assert "Kaizen evidence: sync timed out" in text
    assert "running" not in text
    assert "Items indexed: 12" in text


def test_settings_makes_portfolio_health_primary_and_hides_manual_sync(
    isolated_health_store, monkeypatch
):
    """Product rule: settings top-level shows only Kaizen connection, Writing style,
    Portfolio defaults, and Reset data. Portfolio health is reached via /health
    or the inline flow from other surfaces. Manual Kaizen sync is a hidden
    troubleshooting action, not a normal settings button.
    """
    import bot

    monkeypatch.setattr(bot, "get_curriculum", lambda _uid: "2025")
    monkeypatch.setattr(bot, "get_training_level", lambda _uid: "ST5")
    monkeypatch.setattr(bot, "get_voice_profile", lambda _uid: None)

    _, keyboard = bot._settings_view_components(
        4242,
        tier="pro_plus",
        used=0,
        connected=True,
    )

    flat = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert sorted(flat) == sorted([
        "ACTION|setup",
        "ACTION|voice",
        "ACTION|portfolio_defaults",
        "ACTION|delete",
    ])

    assert "ACTION|health" not in flat
    assert "ACTION|refresh_portfolio" not in flat


def test_settings_omits_portfolio_health_button_when_not_connected(
    isolated_health_store, monkeypatch
):
    """Without Kaizen credentials, the primary health CTA is suppressed — the
    user must connect Kaizen first (the same gate /health already enforces).
    """
    import bot

    monkeypatch.setattr(bot, "get_curriculum", lambda _uid: "2025")
    monkeypatch.setattr(bot, "get_training_level", lambda _uid: "ST5")
    monkeypatch.setattr(bot, "get_voice_profile", lambda _uid: None)

    _, keyboard = bot._settings_view_components(
        4242,
        tier="pro_plus",
        used=0,
        connected=False,
    )

    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "ACTION|health" not in callbacks
    assert "ACTION|refresh_portfolio" not in callbacks


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

    assert "Kaizen evidence: not synced yet" in text


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

    assert "Kaizen evidence" not in text


@pytest.mark.asyncio
async def test_health_command_runs_immediately_when_kaizen_index_is_missing(monkeypatch):
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.health_command(sim._make_text_update("/health"), context)

    run_health.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_refresh_for_health_runs_sync_then_health(monkeypatch):
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(
        bot,
        "sync_kaizen_portfolio_index_for_user",
        AsyncMock(
            return_value=SimpleNamespace(
                status="ok",
                rows_seen=12,
                rows_written=12,
                rows_drifted=0,
                notes=[],
            )
        ),
    )
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|confirm_refresh_for_health"),
        context,
    )

    bot.sync_kaizen_portfolio_index_for_user.assert_awaited_once_with(4242)
    run_health.assert_awaited_once()


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
    assert "Sync Kaizen evidence" in text
    assert "no saving or submitting" in text
    assert ("✅ Sync now", "ACTION|confirm_refresh_portfolio") in sim.get_last_buttons()
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
    assert "Kaizen evidence synced" in text
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
    assert "Sync did not complete" in text
    assert "secret low-level failure" not in text
    assert ("🔄 Try again", "ACTION|refresh_portfolio") in sim.get_last_buttons()


def _make_sync_status(finished_at: str, *, run_status: str = "ok", items_indexed: int = 5):
    from kaizen_index import IndexRunRow, KaizenSyncStatus

    return KaizenSyncStatus(
        last_run=IndexRunRow(
            id=1,
            user_id="4242",
            started_at=finished_at,
            finished_at=finished_at,
            status=run_status,
            rows_seen=items_indexed,
            rows_written=items_indexed,
            rows_drifted=0,
        ),
        items_indexed=items_indexed,
    )


@pytest.mark.asyncio
async def test_health_command_runs_immediately_when_index_is_stale(monkeypatch):
    """A stale sync should not block the primary quick /health journey."""
    import bot

    stale = (datetime.now(UTC) - __import__("datetime").timedelta(days=3)).isoformat()
    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(
        bot,
        "_safe_kaizen_sync_status",
        AsyncMock(return_value=_make_sync_status(stale, run_status="ok", items_indexed=8)),
    )
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.health_command(sim._make_text_update("/health"), context)

    run_health.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_command_skips_refresh_prompt_when_index_is_fresh(monkeypatch):
    """A recent successful sync should let /health run analysis directly."""
    import bot

    recent = datetime.now(UTC).isoformat()
    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(
        bot,
        "_safe_kaizen_sync_status",
        AsyncMock(return_value=_make_sync_status(recent, run_status="ok", items_indexed=12)),
    )
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.health_command(sim._make_text_update("/health"), context)

    run_health.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_command_skips_refresh_prompt_when_not_connected(monkeypatch):
    """Users without Kaizen credentials should fall through to the existing analysis path."""
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: False)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.health_command(sim._make_text_update("/health"), context)

    run_health.assert_awaited_once()


@pytest.mark.asyncio
async def test_inline_health_button_runs_immediately_when_stale(monkeypatch):
    """The inline ACTION|health entry point mirrors /health's quick path."""
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "is_beta_tester", AsyncMock(return_value=False))
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|health"),
        context,
    )

    run_health.assert_awaited_once()

    send_result = run_health.await_args.kwargs["send_result"]
    await send_result("Health result", None)
    assert ("🔙 Back to settings", "ACTION|settings") in sim.get_last_buttons()
    assert ("🔙 Back", "ACTION|back_to_menu") not in sim.get_last_buttons()


def test_health_result_keyboard_offers_file_and_change_pathway():
    import bot

    buttons = [
        (button.text, button.callback_data)
        for row in bot._health_result_keyboard().inline_keyboard
        for button in row
    ]
    assert ("✍️ File missing evidence", "ACTION|file") in buttons
    assert ("📊 Change pathway", "ACTION|change_pathway") in buttons
    assert ("🔙 Back to settings", "ACTION|settings") not in buttons


def test_health_refresh_confirm_back_returns_to_settings():
    import bot

    buttons = [
        (button.text, button.callback_data)
        for row in bot._health_refresh_confirm_keyboard().inline_keyboard
        for button in row
    ]
    assert ("🔙 Back to settings", "ACTION|settings") in buttons
    assert ("🔙 Back", "ACTION|back_to_menu") not in buttons


@pytest.mark.asyncio
async def test_confirm_refresh_for_health_handles_auth_required(monkeypatch):
    """auth_required during the health-triggered refresh must offer reconnect, not run health."""
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "is_beta_tester", AsyncMock(return_value=False))
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
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|confirm_refresh_for_health"),
        context,
    )

    text = sim.get_last_text()
    assert "Kaizen needs reconnecting" in text
    assert ("🔗 Reconnect Kaizen", "ACTION|setup") in sim.get_last_buttons()
    run_health.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_refresh_for_health_handles_unexpected_failure(monkeypatch):
    """Low-level sync exceptions must surface as plain copy without skipping straight to health."""
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "is_beta_tester", AsyncMock(return_value=False))
    monkeypatch.setattr(
        bot,
        "sync_kaizen_portfolio_index_for_user",
        AsyncMock(side_effect=RuntimeError("hidden internal detail")),
    )
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|confirm_refresh_for_health"),
        context,
    )

    text = sim.get_last_text()
    assert "Sync did not complete" in text
    assert "hidden internal detail" not in text
    run_health.assert_not_awaited()


def test_sync_status_freshness_helper_recognises_stale_and_fresh_runs():
    """Unit-level coverage for the freshness gate that drives the prompt."""
    import bot

    assert bot._sync_status_is_fresh(None) is False

    fresh = _make_sync_status(datetime.now(UTC).isoformat())
    assert bot._sync_status_is_fresh(fresh) is True

    stale = _make_sync_status(
        (datetime.now(UTC) - __import__("datetime").timedelta(days=2)).isoformat()
    )
    assert bot._sync_status_is_fresh(stale) is False

    failed = _make_sync_status(datetime.now(UTC).isoformat(), run_status="failed")
    assert bot._sync_status_is_fresh(failed) is False

    empty = _make_sync_status(datetime.now(UTC).isoformat(), items_indexed=0)
    assert bot._sync_status_is_fresh(empty) is False
