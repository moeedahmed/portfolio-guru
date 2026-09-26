"""A CBD draft's Key Capabilities must reach the Kaizen filer.

Regression (2026-09-26): the Telegram draft listed SLO3 KC3, SLO3 KC5 and
SLO7 KC1, but the CBDData filing payload carried no ``key_capabilities``. The
filer fell back to ticking by bare SLO code ("SLO3", "SLO7"), which ticks one
KC per SLO, so Kaizen showed 2 items selected instead of 3.
"""

from unittest.mock import AsyncMock, patch

import pytest

import bot
from models import CBDData
from tests.bot_simulator import BotSimulator

KCS = [
    "SLO3 KC3: Higher SLO3 KC3 (2025 Update)",
    "SLO3 KC5: Higher SLO3 KC5 (2025 Update)",
    "SLO7 KC1: Higher SLO7 KC1 (2025 Update)",
]


def _cbd_draft() -> CBDData:
    return CBDData(
        date_of_encounter="26/9/2026",
        stage_of_training="Higher",
        patient_presentation="Chest pain",
        clinical_reasoning="Assessed and managed as ACS.",
        reflection="I learned to escalate to cardiology sooner and will do so next time.",
        curriculum_links=["SLO3", "SLO7"],
        key_capabilities=KCS,
    )


def test_cbd_filing_fields_carry_every_key_capability():
    fields = bot._cbd_filing_fields(_cbd_draft())

    assert fields["key_capabilities"] == KCS
    assert fields["curriculum_links"] == ["SLO3", "SLO7"]


@pytest.mark.asyncio
async def test_approving_a_cbd_draft_sends_all_three_kcs_to_the_filer():
    sim = BotSimulator()
    update = sim._make_callback_update("APPROVE|draft")
    context = sim._make_context()
    context.user_data.update({
        "case_text": (
            "Chest pain, managed as ACS. I learned to escalate to cardiology "
            "sooner and will do so next time."
        ),
        "case_input_source": "text",
        "chosen_form": "CBD",
    })
    bot._store_draft(context, _cbd_draft())
    route = AsyncMock(return_value={
        "status": "failed",
        "filled": [],
        "skipped": [],
        "error": "deliberate offline stop after payload capture",
        "method": "deterministic",
    })

    with patch("bot.get_credentials", return_value=("user", "pass")), \
         patch("bot._needs_filing_curriculum_choice", return_value=False), \
         patch("bot.route_filing", new=route), \
         patch("bot.compose_filing_recovery_copy", new=AsyncMock(return_value="")), \
         patch("bot._alert_filing_failure", new=AsyncMock()):
        await bot.handle_approval_approve(update, context)

    route.assert_awaited_once()
    filed_fields = route.await_args.kwargs["fields"]
    assert filed_fields["key_capabilities"] == KCS
    assert route.await_args.kwargs["curriculum_links"] == ["SLO3", "SLO7"]
