"""Free-tier limit enforcement tests.

Every other offline test mocks check_can_file with allowed=True, so until now
nothing failed if the over-limit gate in handle_case_input regressed. These
tests pin both layers of the paywall:

1. usage.check_can_file counts real rows: a free user at the monthly limit is
   blocked; pro_plus and the beta override are not.
2. handle_case_input honours a blocked verdict: the user gets the upgrade
   prompt, the conversation ends, and no extraction work starts.
"""

from unittest.mock import AsyncMock, patch

import pytest
from telegram.ext import ConversationHandler

from tests.bot_simulator import BotSimulator


# ─── usage.check_can_file counts real usage ──────────────────────────────


@pytest.fixture
def tmp_usage_db(tmp_path, monkeypatch):
    import usage

    monkeypatch.setattr(usage, "DB_PATH", str(tmp_path / "usage.db"))
    return usage


@pytest.mark.asyncio
async def test_free_user_is_blocked_at_the_monthly_limit(tmp_usage_db):
    usage = tmp_usage_db
    user_id = 101
    limit = usage.TIER_LIMITS["free"]

    for _ in range(limit):
        await usage.record_case_filed(user_id, "CBD")

    allowed, used, returned_limit, tier = await usage.check_can_file(user_id)
    assert allowed is False
    assert used == limit
    assert returned_limit == limit
    assert tier == "free"


@pytest.mark.asyncio
async def test_free_user_below_the_limit_can_file(tmp_usage_db):
    usage = tmp_usage_db
    user_id = 102

    for _ in range(usage.TIER_LIMITS["free"] - 1):
        await usage.record_case_filed(user_id, "CBD")

    allowed, *_ = await usage.check_can_file(user_id)
    assert allowed is True


@pytest.mark.asyncio
async def test_pro_plus_is_unlimited(tmp_usage_db):
    usage = tmp_usage_db
    user_id = 103
    await usage.set_user_tier(user_id, "pro_plus")

    for _ in range(usage.TIER_LIMITS["free"] + 3):
        await usage.record_case_filed(user_id, "CBD")

    allowed, used, limit, tier = await usage.check_can_file(user_id)
    assert allowed is True
    assert limit == -1
    assert tier == "pro_plus"


@pytest.mark.asyncio
async def test_beta_override_bypasses_the_free_limit(tmp_usage_db):
    usage = tmp_usage_db
    user_id = 104
    await usage.set_beta_tester(user_id, True)

    for _ in range(usage.TIER_LIMITS["free"] + 3):
        await usage.record_case_filed(user_id, "CBD")

    allowed, used, limit, tier = await usage.check_can_file(user_id)
    assert allowed is True
    assert limit == -1
    assert tier == "beta"


# ─── handle_case_input honours a blocked verdict ─────────────────────────


@pytest.mark.asyncio
async def test_over_limit_case_shows_upgrade_prompt_and_ends_flow():
    from bot import handle_case_input

    sim = BotSimulator()
    update = sim._make_text_update(
        "45M with chest pain, troponin positive, managed as ACS and reflected on escalation."
    )
    context = sim._make_context()

    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(False, 5, 5, "free"))), \
         patch("bot.recommend_form_types", new_callable=AsyncMock) as recommend, \
         patch("bot.classify_intent", new_callable=AsyncMock) as classify:
        result = await handle_case_input(update, context)

    assert result == ConversationHandler.END
    last_text = sim.get_last_text() or ""
    assert "used all" in last_text
    assert "9.99" in last_text
    # The gate must fire before any extraction work starts.
    recommend.assert_not_awaited()
    classify.assert_not_awaited()
    # An upgrade button is offered.
    assert sim.get_last_buttons(), "expected an upgrade keyboard on the limit message"
