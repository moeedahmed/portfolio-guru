"""Offline tests for the Portfolio Health sign-off watcher.

No Kaizen, browser, CDP, credentials, Telegram, or network. These guard the
behaviour a doctor would notice if it regressed: which evidence counts as
stuck, how long it is reported as waiting, and when the watcher stays silent.
"""

from __future__ import annotations

import importlib
from datetime import date

import pytest


@pytest.fixture
def watch_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "health_watch_test.db"))
    import kaizen_index
    import health_watch

    kaizen_index = importlib.reload(kaizen_index)
    health_watch = importlib.reload(health_watch)
    return kaizen_index, health_watch


async def _store(kaizen_index, **overrides):
    base = dict(
        id="item-1",
        user_id="7",
        surface="event",
        event_type="CBD - Case Based Discussion (2025 update)",
        category="Assessments",
        state="pending",
        date_occurred_on="5 Jun, 2026",
        end_date=None,
        description="Clinical narrative that must never reach a nudge",
        linked_kc_tags=[],
        section_states=[
            {"state": "complete", "label": "This section is completed"},
            {"state": "pending", "label": "This section is awaiting a response"},
        ],
        filled_in_by=None,
        filled_in_on=None,
        parent_event_id=None,
        detail_url="https://kaizenep.com/events/view-section/item-1",
    )
    base.update(overrides)
    await kaizen_index.upsert_evidence_item(kaizen_index.EvidenceItemRow(**base))


@pytest.mark.asyncio
async def test_pending_item_past_threshold_is_stuck(watch_modules):
    kaizen_index, health_watch = watch_modules
    await _store(kaizen_index)

    stuck = await health_watch.find_stuck_signoffs("7", today=date(2026, 8, 24))

    assert len(stuck) == 1
    assert stuck[0].state == "pending"
    assert stuck[0].days_waiting == 80
    assert stuck[0].waits_on_someone_else
    assert stuck[0].blocking_label == "This section is awaiting a response"


@pytest.mark.asyncio
async def test_completed_evidence_is_never_stuck(watch_modules):
    kaizen_index, health_watch = watch_modules
    await _store(kaizen_index, state="complete")

    assert await health_watch.find_stuck_signoffs("7", today=date(2026, 8, 24)) == []


@pytest.mark.asyncio
async def test_recent_pending_item_is_normal_turnaround_not_a_chase(watch_modules):
    """Chasing a three-day-old assessment would train the doctor to mute us."""
    kaizen_index, health_watch = watch_modules
    await _store(kaizen_index, date_occurred_on="21 Aug, 2026")

    assert await health_watch.find_stuck_signoffs("7", today=date(2026, 8, 24)) == []


@pytest.mark.asyncio
async def test_age_is_measured_from_the_event_not_from_first_scan(watch_modules):
    """state_since is ~now on a first scan; using it would hide months-old items."""
    kaizen_index, health_watch = watch_modules
    await _store(kaizen_index, date_occurred_on="12 Oct, 2023", id="old-1")

    stuck = await health_watch.find_stuck_signoffs("7", today=date(2026, 8, 24))

    assert stuck[0].days_waiting == 1047
    assert stuck[0].is_stale


@pytest.mark.asyncio
async def test_stuck_items_are_scoped_to_one_user(watch_modules):
    kaizen_index, health_watch = watch_modules
    await _store(kaizen_index, user_id="7", id="mine")
    await _store(kaizen_index, user_id="8", id="theirs")

    stuck = await health_watch.find_stuck_signoffs("7", today=date(2026, 8, 24))

    assert [item.id for item in stuck] == ["mine"]


@pytest.mark.asyncio
async def test_stuck_items_are_ordered_oldest_first(watch_modules):
    kaizen_index, health_watch = watch_modules
    await _store(kaizen_index, id="newer", date_occurred_on="5 Jul, 2026")
    await _store(kaizen_index, id="older", date_occurred_on="5 Jun, 2026")

    stuck = await health_watch.find_stuck_signoffs("7", today=date(2026, 8, 24))

    assert [item.id for item in stuck] == ["older", "newer"]


@pytest.mark.asyncio
async def test_drafts_and_pending_are_counted_separately(watch_modules):
    kaizen_index, health_watch = watch_modules
    await _store(kaizen_index, id="waiting", state="pending")
    await _store(kaizen_index, id="unfinished", state="draft")

    stuck = await health_watch.find_stuck_signoffs("7", today=date(2026, 8, 24))
    counts = health_watch.summarise_stuck(stuck)

    assert counts == {
        "total": 2,
        "awaiting_others": 1,
        "own_drafts": 1,
        "stale": 0,
        "oldest_days": 80,
    }


def test_chase_copy_is_none_when_nothing_is_stuck(watch_modules):
    """Silence when there is no news is what earns the right to interrupt."""
    _, health_watch = watch_modules
    assert health_watch.format_signoff_chase([]) is None


@pytest.mark.asyncio
async def test_chase_copy_never_leaks_clinical_narrative(watch_modules):
    kaizen_index, health_watch = watch_modules
    await _store(kaizen_index)

    stuck = await health_watch.find_stuck_signoffs("7", today=date(2026, 8, 24))
    text = health_watch.format_signoff_chase(stuck)

    assert "Clinical narrative" not in text
    assert "CBD - Case Based Discussion (2025 update)" in text
    assert "80 days" in text


@pytest.mark.asyncio
async def test_chase_copy_truncates_and_says_how_many_it_hid(watch_modules):
    kaizen_index, health_watch = watch_modules
    for index in range(7):
        await _store(kaizen_index, id=f"item-{index}", date_occurred_on="5 Jun, 2026")

    stuck = await health_watch.find_stuck_signoffs("7", today=date(2026, 8, 24))
    text = health_watch.format_signoff_chase(stuck, limit=5)

    assert text.count("• ") == 5
    assert "and 2 more" in text


# ── The proactive job itself ────────────────────────────────────────────────


class _RecordingBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))


class _Context:
    def __init__(self, bot):
        self.bot = bot


@pytest.fixture
def chase_job(tmp_path, monkeypatch):
    """Import the job with the run sentinel redirected away from the real home."""
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "chase_job.db"))
    monkeypatch.setenv("HOME", str(tmp_path))
    import bot as bot_module

    return bot_module


@pytest.mark.asyncio
async def test_chase_stays_silent_when_nothing_moved(watch_modules):
    """The whole point. A weekly repeat of an unchanged list is what gets
    muted, and a muted watcher cannot report the week something moves."""
    kaizen_index, health_watch = watch_modules
    await _store(kaizen_index, id="old", state="pending")
    from datetime import datetime, timezone

    # Everything was already seen: state_since predates the last run.
    later = datetime.now(timezone.utc)
    changes = await health_watch.detect_changes("7", since=later, today=date(2026, 8, 24))

    assert not changes.has_news
    assert changes.still_waiting == 1
    assert health_watch.format_change_report(changes) is None


@pytest.mark.asyncio
async def test_first_run_treats_everything_stuck_as_news(watch_modules):
    """A doctor who has never been told still needs telling."""
    kaizen_index, health_watch = watch_modules
    await _store(kaizen_index, id="a", state="pending")

    changes = await health_watch.detect_changes("7", since=None, today=date(2026, 8, 24))

    assert len(changes.newly_stuck) == 1
    assert changes.has_news


@pytest.mark.asyncio
async def test_newly_stuck_item_is_reported(watch_modules):
    kaizen_index, health_watch = watch_modules
    from datetime import datetime, timedelta, timezone

    before = datetime.now(timezone.utc) - timedelta(days=7)
    await _store(kaizen_index, id="fresh", state="pending")

    changes = await health_watch.detect_changes("7", since=before, today=date(2026, 8, 24))
    text = health_watch.format_change_report(changes)

    assert "newly waiting" in text
    assert "CBD" in text


@pytest.mark.asyncio
async def test_signed_off_item_is_reported_as_good_news(watch_modules):
    """A watcher that only ever reports problems is one more source of dread,
    and never tells the doctor their chasing worked."""
    kaizen_index, health_watch = watch_modules
    from datetime import datetime, timedelta, timezone

    before = datetime.now(timezone.utc) - timedelta(days=7)
    await _store(kaizen_index, id="done", state="pending")
    await _store(kaizen_index, id="done", state="complete")

    changes = await health_watch.detect_changes("7", since=before, today=date(2026, 8, 24))
    text = health_watch.format_change_report(changes)

    assert len(changes.cleared) == 1
    assert "signed off since last time" in text


@pytest.mark.asyncio
async def test_change_report_links_items_and_hides_clinical_detail(watch_modules):
    kaizen_index, health_watch = watch_modules
    await _store(kaizen_index, id="a", state="pending")

    changes = await health_watch.detect_changes("7", since=None, today=date(2026, 8, 24))
    text = health_watch.format_change_report(changes)

    assert "https://kaizenep.com" in text
    assert "Clinical narrative" not in text


@pytest.mark.asyncio
async def test_recent_item_is_not_news_yet(watch_modules):
    kaizen_index, health_watch = watch_modules
    await _store(kaizen_index, id="new", state="pending", date_occurred_on="21 Aug, 2026")

    changes = await health_watch.detect_changes("7", since=None, today=date(2026, 8, 24))

    assert not changes.has_news


@pytest.mark.asyncio
async def test_chase_job_stays_silent_when_nothing_is_stuck(chase_job, monkeypatch):
    """A user with a clean portfolio must never be messaged."""
    recording = _RecordingBot()

    async def nothing_moved(_user_id, **_kwargs):
        import health_watch

        return health_watch.ChangeSet(newly_stuck=[], cleared=[], still_waiting=3)

    monkeypatch.setattr("health_watch.detect_changes", nothing_moved)

    async def one_user():
        return [4242]

    monkeypatch.setattr(chase_job, "get_all_active_users", one_user)

    await chase_job.signoff_chase_push(_Context(recording))

    assert recording.messages == []


@pytest.mark.asyncio
async def test_chase_job_messages_only_the_user_with_stuck_evidence(chase_job, monkeypatch):
    recording = _RecordingBot()
    import health_watch

    change = health_watch.PortfolioChange(
        title="Mini-CEX (2025 Update)",
        form_type="MINI_CEX",
        event_date=date(2026, 6, 5),
        kind="stuck",
        url=None,
    )
    news = health_watch.ChangeSet(newly_stuck=[change], cleared=[], still_waiting=1)
    quiet = health_watch.ChangeSet(newly_stuck=[], cleared=[], still_waiting=0)

    async def moved_for_one(user_id, **_kwargs):
        return news if user_id == 111 else quiet

    monkeypatch.setattr("health_watch.detect_changes", moved_for_one)

    async def two_users():
        return [111, 222]

    monkeypatch.setattr(chase_job, "get_all_active_users", two_users)

    await chase_job.signoff_chase_push(_Context(recording))

    assert [chat_id for chat_id, _ in recording.messages] == [111]
    assert "Mini-CEX" in recording.messages[0][1]


@pytest.mark.asyncio
async def test_chase_job_does_not_resend_within_the_week(chase_job, monkeypatch):
    """A bot restart must not turn a weekly cadence into a daily one."""
    recording = _RecordingBot()
    import health_watch

    async def always_news(_user_id, **_kwargs):
        return health_watch.ChangeSet(
            newly_stuck=[
                health_watch.PortfolioChange(
                    title="CBD",
                    form_type="CBD",
                    event_date=date(2026, 6, 5),
                    kind="stuck",
                    url=None,
                )
            ],
            cleared=[],
            still_waiting=1,
        )

    monkeypatch.setattr("health_watch.detect_changes", always_news)

    async def one_user():
        return [999]

    monkeypatch.setattr(chase_job, "get_all_active_users", one_user)

    await chase_job.signoff_chase_push(_Context(recording))
    await chase_job.signoff_chase_push(_Context(recording))

    assert len(recording.messages) == 1


def test_chase_job_is_registered_on_a_different_day_from_the_weekly_digest(chase_job):
    """Two proactive messages in one evening reads as spam, not as help."""
    import inspect

    source = inspect.getsource(chase_job.main)
    assert 'name="signoff_chase"' in source
    assert 'name="weekly_push"' in source
    chase_day = source.index('name="signoff_chase"')
    digest_day = source.index('name="weekly_push"')
    # Named constants, not bare integers: PTB indexes days from Sunday and the
    # raw numbers were misread as ISO weekdays for months.
    assert source[digest_day - 400 : digest_day].count("days=(SUNDAY,)") == 1
    assert source[chase_day - 400 : chase_day].count("days=(WEDNESDAY,)") == 1


# ── Off-switch and failure alarm ────────────────────────────────────────────


def test_chase_is_off_unless_deliberately_enabled(chase_job, monkeypatch):
    """It messages real doctors unprompted, so it must fail closed."""
    monkeypatch.delenv("PG_ENABLE_SIGNOFF_CHASE", raising=False)
    assert chase_job._signoff_chase_enabled() is False

    for falsey in ("", "0", "false", "False"):
        monkeypatch.setenv("PG_ENABLE_SIGNOFF_CHASE", falsey)
        assert chase_job._signoff_chase_enabled() is False

    monkeypatch.setenv("PG_ENABLE_SIGNOFF_CHASE", "1")
    assert chase_job._signoff_chase_enabled() is True


def test_job_is_not_registered_at_all_when_disabled(chase_job):
    """Registration is gated, so a disabled chase cannot fire by accident."""
    import inspect

    source = inspect.getsource(chase_job.main)
    assert "if _signoff_chase_enabled():" in source
    gate = source.index("if _signoff_chase_enabled():")
    registration = source.index('name="signoff_chase"')
    assert gate < registration


def test_allowlist_narrows_the_chase_to_named_users(chase_job, monkeypatch):
    """Dogfood on one portfolio before pointing it at the beta cohort."""
    monkeypatch.setenv("PG_SIGNOFF_CHASE_USER_IDS", "111, 333")
    assert chase_job._signoff_chase_audience([111, 222, 333]) == [111, 333]


def test_empty_allowlist_means_every_active_user(chase_job, monkeypatch):
    monkeypatch.delenv("PG_SIGNOFF_CHASE_USER_IDS", raising=False)
    assert chase_job._signoff_chase_audience([111, 222]) == [111, 222]


@pytest.mark.asyncio
async def test_run_that_messages_nobody_still_pings_the_alarm(chase_job, monkeypatch):
    """The whole point: a dead job and a clean portfolio must not look alike."""
    pings = []
    monkeypatch.setenv("PG_SIGNOFF_CHASE_HEALTHCHECK_URL", "https://hc-ping.test/abc")
    monkeypatch.setattr(
        "ops_alert.ping_check", lambda url, suffix="": pings.append(suffix or "success")
    )

    async def nothing_moved(_user_id, **_kwargs):
        import health_watch

        return health_watch.ChangeSet(newly_stuck=[], cleared=[], still_waiting=3)

    monkeypatch.setattr("health_watch.detect_changes", nothing_moved)

    async def one_user():
        return [4242]

    monkeypatch.setattr(chase_job, "get_all_active_users", one_user)

    await chase_job.signoff_chase_push(_Context(_RecordingBot()))

    assert pings == ["/start", "success"]


@pytest.mark.asyncio
async def test_aborted_run_pings_fail_not_success(chase_job, monkeypatch):
    pings = []
    monkeypatch.setenv("PG_SIGNOFF_CHASE_HEALTHCHECK_URL", "https://hc-ping.test/abc")
    monkeypatch.setattr(
        "ops_alert.ping_check", lambda url, suffix="": pings.append(suffix or "success")
    )

    async def boom():
        raise RuntimeError("user list unavailable")

    monkeypatch.setattr(chase_job, "get_all_active_users", boom)

    with pytest.raises(RuntimeError):
        await chase_job.signoff_chase_push(_Context(_RecordingBot()))

    assert "/fail" in pings
    assert "success" not in pings


# ── Login-timeout copy ──────────────────────────────────────────────────────


def test_login_timeout_copy_does_not_blame_kaizen(chase_job):
    """The 60s login timeout fires for local causes too.

    On 2026-08-25 the automation browser wedged: it still answered HTTP but
    could no longer hand out a session, so CDP attach hung past the timeout
    while kaizenep.com was fully up. The old copy told doctors it was "usually
    a brief outage on their side", sending them away to wait on a service that
    was working. Copy must state what happened, not guess why.
    """
    import inspect

    source = inspect.getsource(chase_job)
    assert "brief outage on their side" not in source
    assert "The login check timed out before it finished" in source


def test_login_timeout_copy_says_credentials_were_not_rejected(chase_job):
    """A timeout is not a rejection — users must not retype good passwords."""
    import inspect

    source = inspect.getsource(chase_job)
    assert "haven't been rejected" in source


def test_weekly_refresh_is_bounded(chase_job):
    """A Kaizen scan is minutes of browser work and this job shares the bot's
    event loop. Refreshing every user every week would tie the bot up for hours
    and make it unresponsive to the doctors actually using it."""
    assert chase_job.SIGNOFF_CHASE_MAX_REFRESH_PER_RUN <= 10
    assert chase_job.SIGNOFF_CHASE_REFRESH_TIMEOUT_S <= 1800

    import inspect

    source = inspect.getsource(chase_job.signoff_chase_push)
    assert "SIGNOFF_CHASE_MAX_REFRESH_PER_RUN" in source
    assert "asyncio.wait_for" in source
    # Only refresh a stale index — a fresh one has nothing new to tell us.
    assert "_health_needs_kaizen_refresh" in source


# ── Deadline-scaled cadence ─────────────────────────────────────────────────


def test_chase_speaks_monthly_by_default(chase_job, monkeypatch):
    """The spec makes monthly the default proactive cadence; weekly is reserved
    for deadline mode. Unfinished evidence six months out is worth a monthly
    mention, not a weekly one."""
    monkeypatch.setattr(chase_job, "_stored_review_date", lambda _p: None)
    monkeypatch.setattr(chase_job, "_get_or_default_health_profile", lambda _u: object())

    assert chase_job._chase_interval_days(7) == chase_job.CHASE_INTERVAL_DEFAULT_DAYS


def test_chase_speaks_weekly_near_a_review(chase_job, monkeypatch):
    """The evidence has not changed, but the cost of not knowing has."""
    from datetime import date, timedelta

    soon = date.today() + timedelta(days=30)
    monkeypatch.setattr(chase_job, "_stored_review_date", lambda _p: soon)
    monkeypatch.setattr(chase_job, "_get_or_default_health_profile", lambda _u: object())

    assert chase_job._chase_interval_days(7) == chase_job.CHASE_INTERVAL_DEADLINE_DAYS


def test_a_review_far_away_does_not_trigger_deadline_mode(chase_job, monkeypatch):
    from datetime import date, timedelta

    far = date.today() + timedelta(days=200)
    monkeypatch.setattr(chase_job, "_stored_review_date", lambda _p: far)
    monkeypatch.setattr(chase_job, "_get_or_default_health_profile", lambda _u: object())

    assert chase_job._chase_interval_days(7) == chase_job.CHASE_INTERVAL_DEFAULT_DAYS


def test_a_passed_review_date_does_not_trigger_deadline_mode(chase_job, monkeypatch):
    """A date left behind is not a deadline approaching."""
    from datetime import date, timedelta

    past = date.today() - timedelta(days=10)
    monkeypatch.setattr(chase_job, "_stored_review_date", lambda _p: past)
    monkeypatch.setattr(chase_job, "_get_or_default_health_profile", lambda _u: object())

    assert chase_job._chase_interval_days(7) == chase_job.CHASE_INTERVAL_DEFAULT_DAYS


def test_held_news_is_not_swallowed(chase_job):
    """News found while the cadence says "too soon" must survive to the next
    send. The diff is taken against the last message sent, not the last look,
    so holding back cannot lose anything."""
    import inspect

    source = inspect.getsource(chase_job.signoff_chase_push)
    hold = source.index("holding news for")
    stamp = source.index('seen[str(user_id)] = datetime.now(UTC).isoformat()')
    # The last-sent stamp must be written after the cadence gate, never before.
    assert hold < stamp


# ── Scheduled weekdays ──────────────────────────────────────────────────────


def test_scheduled_jobs_land_on_the_days_their_comments_claim(chase_job):
    """PTB indexes run_daily days from Sunday, not Monday.

    The comment in bot.py claimed ISO weekdays for months, so the "Sunday"
    digest went out every Saturday and the sign-off chase was scheduled for
    Tuesday while its comment said Wednesday — which is why it never fired on
    its first week. Assert against PTB's own mapping so a library change or a
    careless edit cannot quietly move either job again.
    """
    import inspect

    from telegram.ext import JobQueue

    mapping = JobQueue._CRON_MAPPING
    source = inspect.getsource(chase_job.main)

    assert mapping[0] == "sun", "PTB day indexing changed; re-check both jobs"

    # The named constants must match the mapping the library actually uses.
    assert f"SUNDAY, WEDNESDAY = {mapping.index('sun')}, {mapping.index('wed')}" in source
    assert "days=(SUNDAY,)" in source
    assert "days=(WEDNESDAY,)" in source
    # No bare integers left in the code to be misread as ISO weekdays. Comments
    # are stripped first: the explanation of the old bug names those literals.
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "days=(6,)" not in code
    assert "days=(2,)" not in code
