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
    assert "*Portfolio Health — CESR / Portfolio Pathway*" in text
    assert "No cases filed yet" not in text
    assert "WPBA progress toward 36" in text
    assert "1/36" in text


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
    """Product rule: connected users see Portfolio health as the primary settings
    CTA. Manual Kaizen sync remains a hidden troubleshooting action, not a
    normal settings button users have to understand.
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

    rows = [
        [(button.text, button.callback_data) for button in row]
        for row in keyboard.inline_keyboard
    ]
    flat = [pair for row in rows for pair in row]

    # Portfolio health appears, routes to the existing inline ACTION|health
    # handler (which itself prompts the read-only refresh when needed).
    assert ("📊 Portfolio health", "ACTION|health") in flat

    # Manual Kaizen refresh is hidden from the normal settings surface. The
    # callback flow remains covered separately for troubleshooting/support use.
    button_labels = [text for text, _ in flat]
    assert "🔄 Refresh portfolio" not in button_labels
    assert "🔄 Sync Kaizen evidence" not in button_labels
    assert "ACTION|refresh_portfolio" not in [
        callback for _, callback in flat
    ]


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
