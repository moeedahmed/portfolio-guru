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
    store = SimpleNamespace(user_data={})

    await bot._run_health_analysis(
        user_id=user_id,
        chat=SimpleNamespace(send_action=AsyncMock()),
        send_progress=AsyncMock(),
        send_result=AsyncMock(side_effect=lambda text, reply_markup: sent.setdefault("text", text)),
        send_photo_fn=AsyncMock(),
        fail_fn=AsyncMock(),
        context_store=store,
    )

    text = sent["text"]
    assert text.startswith("📍 *Portfolio priorities*")
    # Provenance moved into Scan info; the counted evidence is still stated
    # there, so a doctor knows how much this was read from.
    assert "1 visible evidence item(s)" in store.user_data["last_health_report"]["views"]["scan"]
    assert "No Portfolio Guru cases filed yet" not in text
    assert "1/36 WPBAs counted in this scan" in text


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
    assert [[button.callback_data for button in row] for row in keyboard.inline_keyboard] == [
        ["ACTION|setup"],
        ["ACTION|voice", "ACTION|portfolio_defaults"],
        ["ACTION|delete"],
    ]

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
async def test_health_command_auto_scans_kaizen_when_index_is_missing(monkeypatch):
    """Missing index + creds → /health runs the read-only scan itself, shows a
    scanning message, then runs analysis without asking the user to rerun."""
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))
    sync = AsyncMock(
        return_value=SimpleNamespace(
            status="ok", rows_seen=10, rows_written=10, rows_drifted=0, notes=[]
        )
    )
    monkeypatch.setattr(bot, "sync_kaizen_portfolio_index_for_user", sync)
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.health_command(sim._make_text_update("/health"), context)

    sync.assert_awaited_once_with(4242)
    run_health.assert_awaited_once()
    texts = [text for _, text, _ in sim.messages_sent if text]
    assert any("Scanning your Kaizen portfolio" in text for text in texts)


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
    assert ('🔄 Sync Kaizen', "ACTION|confirm_refresh_portfolio") in sim.get_last_buttons()
    assert ('🔙 Back', "ACTION|settings") in sim.get_last_buttons()
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
    assert ('📊 Portfolio health', "ACTION|health") in sim.get_last_buttons()
    assert ('🔙 Back', "ACTION|settings") in sim.get_last_buttons()


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
    assert ('🔗 Reconnect Kaizen', "ACTION|setup") in sim.get_last_buttons()
    assert ('🔙 Back', "ACTION|settings") in sim.get_last_buttons()


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
    assert ('🔄 Retry', "ACTION|refresh_portfolio") in sim.get_last_buttons()


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
async def test_health_command_auto_scans_kaizen_when_index_is_stale(monkeypatch):
    """A stale index triggers the same autonomous scan-to-report flow."""
    import bot

    stale = (datetime.now(UTC) - __import__("datetime").timedelta(days=3)).isoformat()
    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(
        bot,
        "_safe_kaizen_sync_status",
        AsyncMock(return_value=_make_sync_status(stale, run_status="ok", items_indexed=8)),
    )
    sync = AsyncMock(
        return_value=SimpleNamespace(
            status="ok", rows_seen=8, rows_written=8, rows_drifted=0, notes=[]
        )
    )
    monkeypatch.setattr(bot, "sync_kaizen_portfolio_index_for_user", sync)
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.health_command(sim._make_text_update("/health"), context)

    sync.assert_awaited_once_with(4242)
    run_health.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_command_skips_scan_when_index_is_fresh(monkeypatch):
    """A recent successful sync lets /health run analysis directly — no scan."""
    import bot

    recent = datetime.now(UTC).isoformat()
    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(
        bot,
        "_safe_kaizen_sync_status",
        AsyncMock(return_value=_make_sync_status(recent, run_status="ok", items_indexed=12)),
    )
    sync = AsyncMock()
    monkeypatch.setattr(bot, "sync_kaizen_portfolio_index_for_user", sync)
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.health_command(sim._make_text_update("/health"), context)

    sync.assert_not_awaited()
    run_health.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_command_skips_scan_when_not_connected(monkeypatch):
    """Without Kaizen credentials, /health does not pretend a scan ran; it falls
    through to the existing (limited) analysis path."""
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: False)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))
    sync = AsyncMock()
    monkeypatch.setattr(bot, "sync_kaizen_portfolio_index_for_user", sync)
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.health_command(sim._make_text_update("/health"), context)

    sync.assert_not_awaited()
    run_health.assert_awaited_once()
    texts = [text for _, text, _ in sim.messages_sent if text]
    assert not any("Scanning your Kaizen portfolio" in text for text in texts)


@pytest.mark.asyncio
async def test_health_command_auth_required_shows_reconnect_without_running_health(monkeypatch):
    """auth_required during the autonomous scan must show reconnect copy and not
    run the analysis."""
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))
    monkeypatch.setattr(
        bot,
        "sync_kaizen_portfolio_index_for_user",
        AsyncMock(
            return_value=SimpleNamespace(
                status="auth_required", rows_seen=0, rows_written=0, rows_drifted=0, notes=["login needed"]
            )
        ),
    )
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.health_command(sim._make_text_update("/health"), context)

    text = sim.get_last_text()
    assert "Kaizen needs reconnecting" in text
    assert ('🔗 Reconnect Kaizen', "ACTION|setup") in sim.get_last_buttons()
    run_health.assert_not_awaited()


@pytest.mark.asyncio
async def test_health_command_scan_failure_shows_safe_recovery_without_traceback(monkeypatch):
    """A raised sync exception must surface as plain copy (no traceback/secrets)
    with retry + limited-view recovery, and must not run the analysis."""
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))
    monkeypatch.setattr(
        bot,
        "sync_kaizen_portfolio_index_for_user",
        AsyncMock(side_effect=RuntimeError("hidden internal detail")),
    )
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.health_command(sim._make_text_update("/health"), context)

    text = sim.get_last_text()
    assert "Sync did not complete" in text
    assert "hidden internal detail" not in text
    buttons = sim.get_last_buttons()
    assert ('🔄 Retry', "ACTION|health") in buttons
    assert ('📊 Limited view', "ACTION|health_limited") in buttons
    run_health.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_health_button_auto_scans_when_stale(monkeypatch):
    """The inline ACTION|health entry mirrors /health's autonomous scan-to-report."""
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "is_beta_tester", AsyncMock(return_value=False))
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))
    sync = AsyncMock(
        return_value=SimpleNamespace(
            status="ok", rows_seen=9, rows_written=9, rows_drifted=0, notes=[]
        )
    )
    monkeypatch.setattr(bot, "sync_kaizen_portfolio_index_for_user", sync)
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|health"),
        context,
    )

    sync.assert_awaited_once_with(4242)
    run_health.assert_awaited_once()

    send_result = run_health.await_args.kwargs["send_result"]
    await send_result("Health result", None)
    assert ('📌 Actions', "ACTION|health_view|actions") in sim.get_last_buttons()
    assert ('🔎 Scan info', "ACTION|health_view|scan") in sim.get_last_buttons()
    assert ("🔙 Back", "ACTION|back_to_menu") not in sim.get_last_buttons()
    assert ('🔙 Back', "ACTION|settings") not in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_inline_health_limited_skips_scan_and_runs_analysis(monkeypatch):
    """ACTION|health_limited is the recovery fallback: it runs analysis directly
    without attempting a Kaizen scan."""
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "is_beta_tester", AsyncMock(return_value=False))
    sync = AsyncMock()
    monkeypatch.setattr(bot, "sync_kaizen_portfolio_index_for_user", sync)
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|health_limited"),
        context,
    )

    sync.assert_not_awaited()
    run_health.assert_awaited_once()


@pytest.mark.asyncio
async def test_inline_health_button_auth_required_shows_reconnect(monkeypatch):
    """Inline autonomous health must also surface reconnect on auth_required."""
    import bot

    monkeypatch.setattr(bot, "has_credentials", lambda _uid: True)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "is_beta_tester", AsyncMock(return_value=False))
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))
    monkeypatch.setattr(
        bot,
        "sync_kaizen_portfolio_index_for_user",
        AsyncMock(
            return_value=SimpleNamespace(
                status="auth_required", rows_seen=0, rows_written=0, rows_drifted=0, notes=["login needed"]
            )
        ),
    )
    run_health = AsyncMock()
    monkeypatch.setattr(bot, "_run_health_analysis", run_health)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|health"),
        context,
    )

    text = sim.get_last_text()
    assert "Kaizen needs reconnecting" in text
    assert ('🔗 Reconnect Kaizen', "ACTION|setup") in sim.get_last_buttons()
    run_health.assert_not_awaited()


def _buttons(markup):
    return [
        (button.text, button.callback_data)
        for row in markup.inline_keyboard
        for button in row
    ]


def test_every_health_view_can_switch_directly_to_the_other_three():
    """Back-then-forward is two taps and one lost place. The four views are
    always one tap apart, in the same position on every view."""
    import bot

    nav = [
        ('📍 Priorities', "ACTION|health_view|priorities"),
        ('📌 Actions', "ACTION|health_view|actions"),
        ('📊 Coverage', "ACTION|health_view|coverage"),
        ('🔎 Scan info', "ACTION|health_view|scan"),
    ]
    for view in ("priorities", "actions", "coverage", "curriculum", "scan"):
        buttons = _buttons(bot._health_view_keyboard(view))
        assert buttons[-4:] == nav
        # Exactly one meaningful functional emoji per active button — no
        # decorative stars, sparkles, robots or party icons riding along.
        for text, _data in buttons:
            symbols = [ch for ch in text.replace("\ufe0f", "") if ord(ch) > 0x2000]
            assert len(symbols) == 1 and text.startswith(symbols[0])

    assert (
        '🏷️ Curriculum tags', "ACTION|health_view|curriculum"
    ) in _buttons(bot._health_view_keyboard("coverage"))

    # Generic filing actions are gone: a New case button on a Coverage pane is
    # not justified by anything the scan found.
    assert ('➕ New case', "ACTION|file") not in _buttons(bot._health_view_keyboard("coverage"))
    assert ('🔙 Back', "ACTION|health_back_to_report") not in _buttons(
        bot._health_view_keyboard("scan")
    )


def test_action_queue_pager_appears_only_where_there_is_another_page():
    import bot

    first = _buttons(bot._health_view_keyboard(
        "action_queue", queue="draft", page=0, page_count=4
    ))
    middle = _buttons(bot._health_view_keyboard(
        "action_queue", queue="draft", page=1, page_count=4
    ))
    last = _buttons(bot._health_view_keyboard(
        "action_queue", queue="draft", page=3, page_count=4
    ))
    single = _buttons(bot._health_view_keyboard(
        "action_queue", queue="draft", page=0, page_count=1
    ))

    assert ('➡️ Next', "ACTION|health_queue|draft|1") in first
    assert not any(text == '⬅️ Previous' for text, _ in first)
    assert ('⬅️ Previous', "ACTION|health_queue|draft|0") in middle
    assert ('➡️ Next', "ACTION|health_queue|draft|2") in middle
    assert not any(text == '➡️ Next' for text, _ in last)
    assert ('⬅️ Previous', "ACTION|health_queue|draft|2") in last
    assert not any(data.startswith("ACTION|health_queue|draft") for _text, data in single)

    # Telegram rejects callback data over 64 bytes; every one of ours is tiny.
    assert all(len(data.encode()) <= 64 for _text, data in first + middle + last)


def test_review_month_button_is_offered_only_when_no_month_is_set():
    import bot

    with_route = _buttons(bot._health_view_keyboard("priorities", needs_review_month=True))
    without = _buttons(bot._health_view_keyboard("priorities", needs_review_month=False))

    assert ('📅 Review month', "ACTION|health_review_setup") in with_route
    assert not any(data == "ACTION|health_review_setup" for _text, data in without)


def test_health_compact_report_moves_audit_detail_behind_buttons():
    import bot

    full_text = (
        "📊 *Portfolio Health — Training (CCT) evidence scan*\n"
        "June 2026\n\n"
        "*Evidence basis*\n"
        "Scanned: Portfolio Guru filing history only: 3 case(s) in last 6 months\n"
        "Window: last 6 months of Portfolio Guru filings only; add your ARCP month to time this to your cycle\n"
        "Assumed pathway: Training (CCT) — change if wrong\n"
        "Refresh: no Kaizen refresh available; this is a partial local view\n"
        "Scope: partial — the Kaizen index was unavailable\n\n"
        "*Evidence gap level:* 🔴 Red\n"
        "*Why:* Red because CPD evidence is missing.\n\n"
        "*Next 3 useful filing actions*\n"
        "1. Add a recent CPD course\n\n"
        "*Visible domain coverage*\n"
        "• Clinical: 2\n\n"
        "*Missing domains*\n"
        "CPD · QI\n\n"
        "*Activity snapshot*\n"
        "- This month: 3 cases\n"
        "- Form mix: Mini-CEX 1, Reflection 1, CBD 1"
    )

    summary = bot._health_compact_report_text(full_text)

    assert "*Evidence gap level:* 🔴 Red" in summary
    assert "*Next 3 useful filing actions*" in summary
    assert "*Missing domains*" in summary
    assert "*Evidence basis*" not in summary
    assert "*Activity snapshot*" not in summary
    assert "*Visible domain coverage*" not in summary
    assert "*Scan facts*" in summary
    assert "Scanned: Portfolio Guru filing history only" in summary
    assert "Refresh: no Kaizen refresh available" in summary
    assert "Confidence:" not in summary


def test_health_compact_report_no_index_leads_with_sync_needed():
    """Compact no-index summary must lead with the sync-needed limited-view
    framing and never present a red full-portfolio verdict."""
    import bot
    from health_engine import case_history_to_evidence_items, compute_snapshot

    history = [
        {"form_type": "CBD", "filed_at": "2026-05-01 09:00:00", "status": "filed"},
        {"form_type": "DOPS", "filed_at": "2026-05-02 09:00:00", "status": "filed"},
    ]
    items = case_history_to_evidence_items(history)
    snapshot = compute_snapshot(_profile(9500, Pathway.training_arcp), items)

    full_text = bot._format_arcp_action_plan_message(
        snapshot=snapshot,
        history=history,
        month_label="June 2026",
        evidence_context=bot._format_health_evidence_context(
            source="case_history",
            evidence_count=len(items),
            history_count=len(history),
            profile_is_default=False,
            sync_status=None,
            pathway=Pathway.training_arcp,
        ),
        limited_view=True,
    )

    summary = bot._health_compact_report_text(full_text)

    assert "Full Kaizen scan not available" in summary
    assert "🔴" not in summary
    assert "Evidence gap level:" not in summary
    # Limited-scan framing must lead, ahead of any actions/gaps.
    assert summary.index("Full Kaizen scan not available") < summary.index("Next 3 useful filing actions")


def _limited_arcp_full_text():
    import bot
    from health_engine import case_history_to_evidence_items, compute_snapshot

    history = [
        {"form_type": "CBD", "filed_at": "2026-05-01 09:00:00", "status": "filed"},
        {"form_type": "REFLECT_LOG", "filed_at": "2026-05-02 09:00:00", "status": "filed"},
    ]
    items = case_history_to_evidence_items(history)
    snapshot = compute_snapshot(_profile(9600, Pathway.training_arcp), items)
    full_text = bot._format_arcp_action_plan_message(
        snapshot=snapshot,
        history=history,
        month_label="June 2026",
        evidence_context=bot._format_health_evidence_context(
            source="case_history",
            evidence_count=len(items),
            history_count=len(history),
            profile_is_default=False,
            sync_status=None,
            pathway=Pathway.training_arcp,
        ),
        limited_view=True,
    )
    return full_text


def test_limited_banner_uses_clear_non_technical_wording():
    """The limited banner must explain why the full Kaizen portfolio is not
    visible without the old technical 'Kaizen sync needed' phrasing."""
    import bot

    banner = bot._health_limited_view_banner()
    assert "⚠️ *Full Kaizen scan not available*" in banner
    assert "filed through Portfolio Guru" in banner
    assert "has not been indexed for this health scan" in banner
    assert "refresh Kaizen, then rerun /health" in banner
    assert "Kaizen sync needed" not in banner


def test_limited_domain_detail_page_uses_limited_scan_headings():
    """In limited mode the Domain detail button page must use the consistent
    emoji/format and limited-scan language, not full-portfolio findings."""
    import bot

    sections = bot._health_report_sections(_limited_arcp_full_text())
    domains = sections["domains"]

    assert domains.startswith("📋 *Domain detail*")
    assert "✅ *Visible in this limited scan*" in domains
    assert "⚠️ *Not seen in this limited scan*" in domains
    # Capitalised visible domains, acronyms preserved.
    assert "• Clinical: 1" in domains
    assert "• Reflection: 1" in domains
    # Not-seen domains use the shared label format (CPD/QI uppercase).
    assert "CPD" in domains and "QI" in domains
    assert "teaching" in domains and "leadership" in domains
    # Full-scan wording must not leak into the limited page.
    assert "Already strong" not in domains
    assert "Missing domains" not in domains
    assert "official ARCP outcome" not in domains


def test_limited_detail_pages_have_consistent_emoji_headings():
    """Evidence basis, Activity snapshot, and Domain detail pages must each
    lead with their consistent emoji heading."""
    import bot

    full_text = _limited_arcp_full_text()
    full_text += "\n\n*Activity snapshot*\n- This month: 2 cases"
    sections = bot._health_report_sections(full_text)

    assert sections["basis"].startswith("🔎 *Evidence basis*")
    assert sections["activity"].startswith("📈 *Activity snapshot*")
    assert sections["domains"].startswith("📋 *Domain detail*")


@pytest.mark.asyncio
async def test_limited_activity_snapshot_states_partial_scope(monkeypatch):
    """The limited-mode activity snapshot states the exact missing source."""
    import bot

    async def _snapshot(*_a, **_k):
        return (
            "*Activity snapshot*\n"
            "- This month: 2 cases\n"
            "- Form mix: CBD 1, Reflection 1"
        )

    monkeypatch.setattr(
        "portfolio_chart.format_health_activity_snapshot_async", _snapshot
    )

    appended = await bot._append_health_activity_snapshot(
        "BODY", 9700, [], "ST6", limited_view=True
    )
    assert "*Activity snapshot*" in appended
    assert "Scope: partial" in appended
    assert "Kaizen index not available" in appended
    assert "Confidence:" not in appended

    not_limited = await bot._append_health_activity_snapshot(
        "BODY", 9700, [], "ST6", limited_view=False
    )
    assert "Scope: partial" not in not_limited


@pytest.mark.asyncio
async def test_health_arcp_index_present_is_not_reported_as_a_partial_scan(
    kaizen_index, isolated_health_store, monkeypatch
):
    """With a Kaizen index present the reading is of the real portfolio and
    must not carry the partial-scan notice."""
    import bot
    import sys

    user_id = 9404
    await kaizen_index.upsert_evidence_item(
        _evidence_row(kaizen_index, id="cbd-arcp", event_type="CBD", user_id=str(user_id))
    )
    monkeypatch.setattr(bot, "get_case_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        bot, "get_health_profile", lambda _uid: _profile(user_id, Pathway.training_arcp)
    )
    monkeypatch.setattr(bot, "get_training_level", lambda _uid: "ST6")
    monkeypatch.setattr(
        bot, "analyse_portfolio_health", AsyncMock(return_value={"suggestions": []})
    )

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
    store = SimpleNamespace(user_data={})

    await bot._run_health_analysis(
        user_id=user_id,
        chat=SimpleNamespace(send_action=AsyncMock()),
        send_progress=AsyncMock(),
        send_result=AsyncMock(side_effect=lambda text, reply_markup: sent.setdefault("text", text)),
        send_photo_fn=AsyncMock(),
        fail_fn=AsyncMock(),
        context_store=store,
    )

    text = sent["text"]
    scan = store.user_data["last_health_report"]["views"]["scan"]
    # A full index must not be reported as a limited view, and no view may
    # claim a readiness level the scan cannot support.
    assert "visible evidence item(s)" in scan
    assert "Partial scan" not in text
    assert "Limited view" not in scan
    assert not any(label in text for label in ("Well covered", "Needs attention", "Thin"))
    assert "A planning aid, not a formal training" in text


@pytest.mark.asyncio
async def test_health_view_buttons_render_the_stored_views(monkeypatch):
    import bot

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()
    context.user_data["last_health_report"] = {
        "views": {
            "priorities": "📍 Priorities view",
            "coverage": "📊 Coverage view",
            "curriculum": "🏷️ Curriculum view",
            "scan": "🔎 Scan info view",
        },
        "action_pages": ["📌 Actions page 1", "📌 Actions page 2"],
        "page": 0,
        "needs_review_month": True,
    }

    for action, expected in (
        ("ACTION|health_view|coverage", "📊 Coverage view"),
        ("ACTION|health_view|curriculum", "🏷️ Curriculum view"),
        ("ACTION|health_view|scan", "🔎 Scan info view"),
        ("ACTION|health_view|actions", "📌 Actions page 1"),
        ("ACTION|health_page|1", "📌 Actions page 2"),
        ("ACTION|health_view|priorities", "📍 Priorities view"),
    ):
        await bot.handle_action_button(sim._make_callback_update(action), context)
        assert sim.get_last_text() == expected
        assert ('📌 Actions', "ACTION|health_view|actions") in sim.get_last_buttons()

    assert ('📅 Review month', "ACTION|health_review_setup") in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_stale_health_buttons_recover_instead_of_dead_ending(monkeypatch):
    """Buttons outlive the report they were sent with. An old page number, a
    button from the previous layout, and a report this chat no longer holds
    each have to land somewhere real."""
    import bot
    monkeypatch.setattr(bot, "_track_funnel_event", lambda *_args, **_kwargs: None)

    sim = BotSimulator(user_id=4243)
    context = sim._make_context()
    context.user_data["last_health_report"] = {
        "views": {"priorities": "📍 Priorities view", "coverage": "📊 Coverage view",
                  "scan": "🔎 Scan info view"},
        "action_pages": ["📌 Actions page 1", "📌 Actions page 2"],
        "page": 0,
        "needs_review_month": False,
    }

    # A page beyond the stored report clamps onto the last real page.
    await bot.handle_action_button(sim._make_callback_update("ACTION|health_page|9"), context)
    assert sim.get_last_text() == "📌 Actions page 2"

    # Buttons from the pre-V2 layout route to the view that replaced them,
    # and Actions resumes the page the doctor had reached.
    for legacy, expected in (
        ("ACTION|health_detail|stuck", "📌 Actions page 2"),
        ("ACTION|health_detail|domains", "📊 Coverage view"),
        ("ACTION|health_detail|basis", "🔎 Scan info view"),
        ("ACTION|health_back_to_report", "📍 Priorities view"),
    ):
        await bot.handle_action_button(sim._make_callback_update(legacy), context)
        assert sim.get_last_text() == expected

    # With no stored report at all, the way back is one button, not a retype.
    fresh = BotSimulator(user_id=4243)
    await bot.handle_action_button(
        fresh._make_callback_update("ACTION|health_view|coverage"), fresh._make_context()
    )
    assert "no longer in memory" in fresh.get_last_text()
    assert ('🔄 Refresh health', "ACTION|health") in fresh.get_last_buttons()


@pytest.mark.asyncio
async def test_review_month_button_opens_picker_and_changes_nothing(monkeypatch):
    """Opening the picker is non-mutating; only Confirm can persist."""
    import bot

    sim = BotSimulator(user_id=4244)
    context = sim._make_context()
    saved = []
    monkeypatch.setattr(bot, "save_health_profile", lambda profile: saved.append(profile))
    monkeypatch.setattr(bot, "_track_funnel_event", lambda *_args, **_kwargs: None)

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|health_review_setup"), context
    )

    assert "nothing changes until you tap Confirm" in sim.get_last_text()
    assert saved == []
    assert any(
        callback.startswith("ACTION|health_review_select|")
        for _label, callback in sim.get_last_buttons()
    )
    assert ('🔙 Cancel', "ACTION|health_view|priorities") in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_activity_snapshot_derives_slos_from_indexed_kaizen_kc_tags(
    kaizen_index, monkeypatch
):
    """End-to-end: indexed Kaizen rows carrying ``linked_kc_tags`` drive the
    Activity snapshot's SLO coverage line via the read-only index — no sync,
    no Portfolio Guru-linked-only caveat."""
    import portfolio_chart

    user_id = 9800
    await kaizen_index.upsert_evidence_item(
        _evidence_row(
            kaizen_index,
            id="cbd-1",
            user_id=str(user_id),
            event_type="CBD",
            linked_kc_tags=["Higher SLO1 KC1", "Higher SLO8 KC2"],
        )
    )
    await kaizen_index.upsert_evidence_item(
        _evidence_row(
            kaizen_index,
            id="acat-1",
            user_id=str(user_id),
            event_type="ACAT",
            linked_kc_tags=["SLO 12"],
        )
    )

    monkeypatch.setattr(portfolio_chart, "list_evidence_items", kaizen_index.list_evidence_items)
    monkeypatch.setattr(portfolio_chart, "get_case_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(portfolio_chart, "get_cases_this_month", AsyncMock(return_value=0))
    monkeypatch.setattr(portfolio_chart, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(portfolio_chart, "is_beta_tester", AsyncMock(return_value=False))
    monkeypatch.setattr(portfolio_chart, "get_kc_coverage", AsyncMock(return_value={}))
    monkeypatch.setattr(portfolio_chart, "get_kc_stats", AsyncMock(return_value=None))
    monkeypatch.setattr(portfolio_chart, "get_training_level", lambda _uid: "ST6")

    text = await portfolio_chart.format_health_activity_snapshot_async(user_id)

    assert "3/12 SLOs visible in indexed Kaizen KC links" in text
    assert "SLO 1" in text
    assert "SLO 8" in text
    assert "SLO 12" in text
    assert "not your full Kaizen strength" not in text


def test_health_refresh_confirm_back_returns_to_settings():
    import bot

    buttons = [
        (button.text, button.callback_data)
        for row in bot._health_refresh_confirm_keyboard().inline_keyboard
        for button in row
    ]
    assert ('🔙 Back', "ACTION|settings") in buttons
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
    assert ('🔗 Reconnect Kaizen', "ACTION|setup") in sim.get_last_buttons()
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


def test_health_sync_recovery_keyboard_offers_retry_and_limited_view():
    """Failure/drift recovery offers an autonomous retry and a limited-view
    fallback — never a 'rerun /health' instruction button."""
    import bot

    for status in ("failed", "drift"):
        buttons = [
            (button.text, button.callback_data)
            for row in bot._health_sync_recovery_keyboard(status).inline_keyboard
            for button in row
        ]
        assert ('🔄 Retry', "ACTION|health") in buttons
        assert ('📊 Limited view', "ACTION|health_limited") in buttons


def test_health_sync_recovery_keyboard_offers_reconnect_on_auth_required():
    import bot

    buttons = [
        (button.text, button.callback_data)
        for row in bot._health_sync_recovery_keyboard("auth_required").inline_keyboard
        for button in row
    ]
    assert buttons == [('🔗 Reconnect Kaizen', "ACTION|setup")]
