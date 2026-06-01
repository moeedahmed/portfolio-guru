from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import ConversationHandler

from health_models import HealthProfile, Pathway
from tests.bot_simulator import BotSimulator


def _profile(user_id: int, pathway: Pathway) -> HealthProfile:
    now = datetime.now(UTC)
    return HealthProfile(
        user_id=str(user_id),
        pathway=pathway,
        pathway_config={},
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_pathway_command_saves_selected_pathway(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_GURU_HEALTH_PROFILE_PATH", str(tmp_path / "health_profiles.json"))

    import bot
    import health_profile_store

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    result = await bot.pathway_command(sim._make_text_update("/pathway"), context)

    assert result == bot.AWAIT_PATHWAY
    assert ("Training (ARCP)", "PATHWAY|training_arcp") in sim.get_last_buttons()
    assert ("CESR / Portfolio Pathway", "PATHWAY|cesr_portfolio") in sim.get_last_buttons()

    result = await bot.handle_pathway_choice(
        sim._make_callback_update("PATHWAY|cesr_portfolio"),
        context,
    )

    assert result == ConversationHandler.END
    stored = health_profile_store.get_health_profile(sim.user_id)
    assert stored is not None
    assert stored.pathway == Pathway.cesr_portfolio
    assert "CESR / Portfolio Pathway" in sim.get_last_text()


@pytest.mark.asyncio
async def test_cesr_health_output_uses_deterministic_engine_without_llm(monkeypatch):
    import sys
    import bot

    user_id = 5151
    history = [
        {"form_type": "CBD", "filed_at": "2026-05-01 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "DOPS", "filed_at": "2026-05-02 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "QIAT", "filed_at": "2026-05-03 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "TEACH_OBS", "filed_at": "2026-05-04 09:00:00", "status": "failed", "telegram_user_id": user_id},
    ]
    analysis = AsyncMock()

    async def generate_health_chart_async(_user_id):
        return None

    monkeypatch.setitem(
        sys.modules,
        "portfolio_chart",
        SimpleNamespace(generate_health_chart_async=generate_health_chart_async),
    )
    monkeypatch.setattr(bot, "get_health_profile", lambda _user_id: _profile(user_id, Pathway.cesr_portfolio))
    monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST6")
    monkeypatch.setattr(bot, "get_case_history", AsyncMock(return_value=history))
    monkeypatch.setattr(bot, "analyse_portfolio_health", analysis)

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
    assert "Health score:" in text
    assert "Domain coverage:" in text
    assert "Gap summary:" in text
    assert "Next actions:" in text
    assert "WPBA count: 2" in text
    assert "CESR requires 36 WPBAs minimum" in text
    analysis.assert_not_called()
