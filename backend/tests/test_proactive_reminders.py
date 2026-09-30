"""Proactive reminders: tiers, caps and controls, on fixed dates."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

import proactive_reminders as pr

TODAY = date(2026, 10, 1)
GAP = "Book 2 more ESLEs, with at least one in PEM"


def signals(review=None, **overrides):
    base = dict(deadline_name="ARCP", review_month=review, actions=(GAP,), scan_date=TODAY)
    base.update(overrides)
    return pr.Signals(**base)


def sent(state, kind, key, on):
    return {**state, "sent": state["sent"] + [{"on": on.isoformat(), "kind": kind, "key": key}]}


# ── Tiers ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "review,tier",
    [
        (None, pr.QUIET),
        (date(2027, 6, 1), pr.QUIET),  # eight months out
        (date(2026, 12, 1), pr.STEADY),  # two months out
        (date(2026, 10, 1), pr.FINAL),  # the review month itself
        (date(2026, 10, 20), pr.FINAL),
        (date(2026, 9, 1), pr.AFTER),  # September has just passed
        (date(2026, 6, 1), pr.QUIET),  # long past: stop asking
    ],
)
def test_tier_follows_the_review_month(review, tier):
    assert pr.tier_for(review, TODAY) == tier


# ── Caps ────────────────────────────────────────────────────────────────────


def test_quiet_tier_sends_at_most_two_a_month():
    state = pr.empty_state()
    state = sent(state, pr.DIGEST, "a", TODAY - timedelta(days=20))
    state = sent(state, pr.DIGEST, "b", TODAY - timedelta(days=5))
    assert pr.decide(signals(change_text="✅ *1 signed off*"), state, TODAY) is None
    state["sent"] = state["sent"][1:]
    assert pr.decide(signals(change_text="✅ *1 signed off*"), state, TODAY) is not None


def test_quiet_tier_does_not_repeat_an_unchanged_gap():
    first = pr.decide(signals(), pr.empty_state(), TODAY)
    assert first and first.kind == pr.DIGEST and GAP in first.text
    later = TODAY + timedelta(days=40)
    state = pr.record_sent(pr.empty_state(), first, TODAY)
    assert pr.decide(signals(), state, later) is None


def test_steady_tier_is_weekly_and_opens_with_a_milestone():
    review = date(2026, 12, 1)
    first = pr.decide(signals(review), pr.empty_state(), TODAY)
    assert first.kind == pr.MILESTONE and "ARCP (December 2026)" in first.text
    state = pr.record_sent(pr.empty_state(), first, TODAY)
    assert pr.decide(signals(review, change_text="news"), state, TODAY + timedelta(days=6)) is None
    assert pr.decide(signals(review, change_text="news"), state, TODAY + timedelta(days=7)) is not None


def test_final_tier_allows_two_a_week_but_never_two_days_running():
    review = date(2026, 10, 1)
    state = sent(pr.empty_state(), pr.MILESTONE, f"milestone:{review.isoformat()}:4w", TODAY - timedelta(days=3))
    news = signals(review, change_text="📌 *1 newly waiting*")
    assert pr.decide(news, state, TODAY) is not None
    state = sent(state, pr.DIGEST, "x", TODAY - timedelta(days=1))
    assert pr.decide(news, state, TODAY) is None  # yesterday's message blocks today
    assert pr.decide(news, state, TODAY + timedelta(days=1)) is None  # 2 in the last 7 days


def test_one_message_a_day_even_for_urgent_news():
    alert = pr.StuckAlert(key="k1", label="CBD from 1 Jul 2026", days_waiting=92)
    state = sent(pr.empty_state(), pr.DIGEST, "x", TODAY)
    assert pr.decide(signals(stuck_alerts=(alert,)), state, TODAY) is None


# ── Urgent alerts ───────────────────────────────────────────────────────────


def test_urgent_alert_ignores_caps_and_is_sent_once_per_item():
    alert = pr.StuckAlert(key="k1", label="CBD from 1 Jul 2026", days_waiting=92)
    state = pr.empty_state()
    for days in (25, 10):  # quiet cap already spent
        state = sent(state, pr.DIGEST, f"d{days}", TODAY - timedelta(days=days))
    reminder = pr.decide(signals(stuck_alerts=(alert,)), state, TODAY)
    assert reminder.kind == pr.URGENT and "92 days" in reminder.text
    state = pr.record_sent(state, reminder, TODAY)
    assert pr.decide(signals(stuck_alerts=(alert,)), state, TODAY + timedelta(days=1)) is None
    other = pr.StuckAlert(key="k2", label="DOPS from 2 Jul 2026", days_waiting=91)
    again = pr.decide(signals(stuck_alerts=(alert, other)), state, TODAY + timedelta(days=1))
    assert "DOPS" in again.text and "CBD" not in again.text


def test_urgent_does_not_count_as_ignored():
    alert = pr.StuckAlert(key="k1", label="CBD", days_waiting=70)
    reminder = pr.decide(signals(stuck_alerts=(alert,)), pr.empty_state(), TODAY)
    assert pr.record_sent(pr.empty_state(), reminder, TODAY)["ignored"] == 0


def test_cesr_expiry_alert():
    reminder = pr.decide(
        signals(deadline_name="appraisal", cesr_expiring=2), pr.empty_state(), TODAY
    )
    assert reminder.kind == pr.URGENT and "2 signed-off items" in reminder.text


# ── Controls and backoff ────────────────────────────────────────────────────


def test_off_and_pause_silence_everything():
    alert = pr.StuckAlert(key="k1", label="CBD", days_waiting=70)
    off = {**pr.empty_state(), "level": pr.LEVEL_OFF}
    assert pr.decide(signals(stuck_alerts=(alert,)), off, TODAY) is None
    paused = {**pr.empty_state(), "quiet_until": (TODAY + timedelta(days=3)).isoformat()}
    assert pr.decide(signals(stuck_alerts=(alert,)), paused, TODAY) is None


def test_only_urgent_level():
    state = {**pr.empty_state(), "level": pr.LEVEL_URGENT}
    assert pr.decide(signals(change_text="news"), state, TODAY) is None
    alert = pr.StuckAlert(key="k1", label="CBD", days_waiting=70)
    assert pr.decide(signals(stuck_alerts=(alert,)), state, TODAY).kind == pr.URGENT


def test_less_like_this_mutes_a_kind_for_thirty_days():
    state = {**pr.empty_state(), "muted": {pr.DIGEST: (TODAY + timedelta(days=29)).isoformat()}}
    assert pr.decide(signals(change_text="news"), state, TODAY) is None
    assert pr.decide(signals(change_text="news"), state, TODAY + timedelta(days=30)) is not None


def test_recent_engagement_holds_non_urgent_news():
    state = pr.record_engaged(pr.empty_state(), TODAY - timedelta(days=1))
    assert pr.decide(signals(change_text="news"), state, TODAY) is None
    assert pr.decide(signals(change_text="news"), state, TODAY + timedelta(days=1)) is not None


def test_ignored_reminders_drop_to_the_quieter_tier():
    review = date(2026, 10, 1)  # final tier: two a week
    state = {**pr.empty_state(), "ignored": pr.IGNORED_BEFORE_BACKOFF}
    state = sent(state, pr.MILESTONE, f"milestone:{review.isoformat()}:4w", TODAY - timedelta(days=4))
    assert pr.decide(signals(review, change_text="news"), state, TODAY) is None  # steady: one a week
    assert pr.decide(signals(review, change_text="news"), pr.record_engaged(state, TODAY - timedelta(days=3)), TODAY) is not None


# ── Content ─────────────────────────────────────────────────────────────────


def test_stale_scan_is_stated_not_hidden():
    old = signals(scan_date=TODAY - timedelta(days=30))
    reminder = pr.decide(old, pr.empty_state(), TODAY)
    assert "last Kaizen scan on 1 Sep" in reminder.text


def test_after_the_review_month_asks_for_the_next_one_once():
    review = date(2026, 9, 1)
    reminder = pr.decide(signals(review), pr.empty_state(), TODAY)
    assert reminder.kind == pr.AFTER and "September 2026" in reminder.text
    state = pr.record_sent(pr.empty_state(), reminder, TODAY)
    assert all(
        c.kind != pr.AFTER for c in pr.candidates(signals(review), state, TODAY + timedelta(days=8))
    )


def test_store_round_trip_and_erasure(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_GURU_PROACTIVE_PATH", str(tmp_path / "r.json"))
    pr.save_state(42, {**pr.empty_state(), "level": pr.LEVEL_URGENT})
    assert pr.load_state(42)["level"] == pr.LEVEL_URGENT
    pr.delete_state(42)
    assert pr.load_state(42) == pr.empty_state()


# ── The daily job ───────────────────────────────────────────────────────────


@pytest.fixture
def tick_env(tmp_path, monkeypatch):
    import bot

    monkeypatch.setenv("PORTFOLIO_GURU_PROACTIVE_PATH", str(tmp_path / "r.json"))
    monkeypatch.setenv("PG_ENABLE_PROACTIVE", "1")
    monkeypatch.setattr(bot, "data_path", lambda name: tmp_path / name)
    monkeypatch.setattr(bot, "get_all_active_users", AsyncMock(return_value=[111, 222]))
    monkeypatch.setattr(bot, "_kaizen_connected", lambda uid: False)
    monkeypatch.setattr(
        bot,
        "_proactive_signals",
        AsyncMock(side_effect=lambda uid, state, today: signals(change_text="✅ *1 signed off*")),
    )
    context = MagicMock()
    context.bot.send_message = AsyncMock()
    return bot, context


@pytest.mark.asyncio
async def test_tick_sends_once_per_user_and_respects_the_allowlist(tick_env, monkeypatch):
    bot, context = tick_env
    monkeypatch.setenv("PG_PROACTIVE_USER_IDS", "111")
    await bot.proactive_tick(context)
    assert context.bot.send_message.await_count == 1
    kwargs = context.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 111 and "Change this any time" in kwargs["text"]
    assert pr.load_state(111)["sent"] and pr.load_state(111)["changes_since"]
    # A restart the same evening does not run it again.
    await bot.proactive_tick(context)
    assert context.bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_tick_dry_run_sends_and_records_nothing(tick_env, monkeypatch):
    bot, context = tick_env
    monkeypatch.setenv("PG_PROACTIVE_DRY_RUN", "1")
    await bot.proactive_tick(context)
    context.bot.send_message.assert_not_awaited()
    assert pr.load_state(111)["sent"] == []
    assert not bot._proactive_owns(111)  # old weekly jobs still speak in a dry run


@pytest.mark.asyncio
async def test_tick_checks_a_named_pilot_user_with_no_filings(tick_env, monkeypatch):
    bot, context = tick_env
    monkeypatch.setenv("PG_PROACTIVE_USER_IDS", "333")
    await bot.proactive_tick(context)
    assert context.bot.send_message.await_count == 1
    assert context.bot.send_message.await_args.kwargs["chat_id"] == 333


def test_old_weekly_jobs_step_aside_only_for_covered_users(monkeypatch):
    import bot

    monkeypatch.delenv("PG_ENABLE_PROACTIVE", raising=False)
    assert not bot._proactive_owns(111)
    monkeypatch.setenv("PG_ENABLE_PROACTIVE", "1")
    monkeypatch.setenv("PG_PROACTIVE_USER_IDS", "111")
    assert bot._proactive_owns(111) and not bot._proactive_owns(222)


@pytest.mark.asyncio
async def test_signals_claim_no_gaps_without_a_kaizen_scan(monkeypatch, tmp_path):
    import bot

    monkeypatch.setenv("PORTFOLIO_GURU_HEALTH_PROFILE_PATH", str(tmp_path / "h.json"))
    monkeypatch.setattr(bot, "_resolve_health_evidence", AsyncMock(return_value=([], [], "case_history")))
    monkeypatch.setattr(bot, "_safe_kaizen_sync_status", AsyncMock(return_value=None))
    result = await bot._proactive_signals(111, pr.empty_state(), TODAY)
    assert result.actions == () and result.change_text is None and result.stuck_alerts == ()
    assert result.deadline_name == "ARCP"
