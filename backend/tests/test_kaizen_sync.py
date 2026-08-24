"""Offline tests for the read-only Kaizen sync driver.

No live Kaizen, browser, CDP, credentials, Telegram, launchd, or network. The
fake page below mimics the tiny Playwright surface the sync driver needs.
"""

from __future__ import annotations

import importlib
import inspect

import pytest


class FakeKaizenPage:
    def __init__(self, *, lists=None, details=None, auth_urls=None, scroll_settles=True):
        self.scroll_settles = scroll_settles
        self.lists = lists or {}
        self.details = details or {}
        self.auth_urls = set(auth_urls or [])
        self.url = "https://kaizenep.com/dashboard"
        self.visited: list[str] = []

    async def goto(self, url, **_kwargs):
        self.visited.append(url)
        self.url = "https://auth.kaizenep.com/interaction/login" if url in self.auth_urls else url

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    async def evaluate(self, _script, *args):
        if "window.scrollTo" in _script:
            return self.scroll_settles
        if "/events/list/" in self.url:
            return self.lists.get(self.url, [])
        if self.url == "https://kaizenep.com/activities":
            return self.lists.get(self.url, [])
        if "/events/view" in self.url:
            payload = self.details.get(self.url)
            if isinstance(payload, BaseException):
                raise payload
            return payload
        return []


class FakeLazyKaizenPage(FakeKaizenPage):
    """Mimics Kaizen rows that only appear after read-only scrolling."""

    def __init__(self, *, initial_lists=None, expanded_lists=None, details=None, scroll_settles=True):
        super().__init__(lists=initial_lists or {}, details=details or {}, scroll_settles=scroll_settles)
        self.initial_lists = initial_lists or {}
        self.expanded_lists = expanded_lists or {}
        self.did_scroll_expand = False

    async def evaluate(self, script, *args):
        if "window.scrollTo" in script:
            self.did_scroll_expand = True
            return self.scroll_settles
        if "/events/list/" in self.url or self.url == "https://kaizenep.com/activities":
            lists = self.expanded_lists if self.did_scroll_expand else self.initial_lists
            return lists.get(self.url, [])
        return await super().evaluate(script, *args)


@pytest.fixture
def sync_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "kaizen_sync_test.db"))
    import kaizen_index
    import kaizen_sync

    kaizen_index = importlib.reload(kaizen_index)
    kaizen_sync = importlib.reload(kaizen_sync)
    return kaizen_index, kaizen_sync


def _detail(**overrides):
    base = {
        "event_type": "CBD - Case Based Discussion (2025 update)",
        "state": "Complete",
        "description": "Senior-led resus case",
        "fields": [
            {"label": "Date occurred on", "value": "20 May, 2026"},
            {"label": "End date", "value": "20 May, 2026"},
            {"label": "Case to be discussed", "value": "Shock case"},
        ],
        "tags": ["Higher SLO1 KC1", "Higher SLO3 KC2"],
        "filled_in_by": "Dr Example",
        "filled_in_on": "21 May, 2026",
        "buttons": ["View profile", "Send to assessor"],
        "url": "https://kaizenep.com/events/view-section/11111111-1111-1111-1111-111111111111",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_sync_writes_timeline_detail_and_activity_draft(sync_modules):
    kaizen_index, kaizen_sync = sync_modules
    assessment_url = "https://kaizenep.com/events/list/Assessments"
    activity_url = "https://kaizenep.com/activities"
    filed_url = "https://kaizenep.com/events/view-section/11111111-1111-1111-1111-111111111111"
    draft_url = "https://kaizenep.com/events/view-section/22222222-2222-2222-2222-222222222222"
    page = FakeKaizenPage(
        lists={
            assessment_url: [
                {
                    "title": "CBD - Case Based Discussion (2025 update)",
                    "href": "/events/view-section/11111111-1111-1111-1111-111111111111",
                    "state": "Complete",
                    "date_text": "20 May, 2026",
                }
            ],
            activity_url: [
                {
                    "title": "Saved draft: Reflection",
                    "href": "/events/view-section/22222222-2222-2222-2222-222222222222",
                    "state": "draft",
                    "date_text": "22 May, 2026",
                    "draftish": True,
                }
            ],
        },
        details={
            filed_url: _detail(),
            draft_url: _detail(
                event_type="Reflection on ESLE (2025 Update)",
                state=None,
                tags=["Higher SLO7 KC1"],
                url=draft_url,
            ),
        },
    )

    result = await kaizen_sync.sync_kaizen_portfolio_index(
        42,
        page,
        categories=("Assessments",),
        include_activities=True,
    )

    assert result.status == "ok"
    assert result.rows_seen == 2
    assert result.rows_written == 2
    rows = await kaizen_index.list_evidence_items("42")
    assert [row.id for row in rows] == [
        "22222222-2222-2222-2222-222222222222",
        "11111111-1111-1111-1111-111111111111",
    ]
    filed = next(row for row in rows if row.id.startswith("1111"))
    assert filed.surface == "event_section"
    assert filed.category == "Assessments"
    assert filed.event_type == "CBD - Case Based Discussion (2025 update)"
    assert filed.date_occurred_on == "20 May, 2026"
    assert filed.end_date == "20 May, 2026"
    assert filed.description == "Senior-led resus case"
    assert filed.linked_kc_tags == ["Higher SLO1 KC1", "Higher SLO3 KC2"]
    draft = next(row for row in rows if row.id.startswith("2222"))
    assert draft.surface == "draft"
    assert draft.state == "draft"
    latest = await kaizen_index.latest_index_run("42")
    assert latest is not None
    assert latest.status == "ok"
    assert latest.rows_written == 2


@pytest.mark.asyncio
async def test_sync_expands_lazy_loaded_timeline_before_reading_rows(sync_modules):
    kaizen_index, kaizen_sync = sync_modules
    assessment_url = "https://kaizenep.com/events/list/Assessments"
    initially_visible_count = 100
    total_available_count = initially_visible_count * 4

    initial_rows = [
        {
            "title": f"CBD {idx}",
            "href": f"/events/view-section/00000000-0000-0000-0000-{idx:012d}",
            "state": "Complete",
            "date_text": "20 May, 2026",
        }
        for idx in range(1, initially_visible_count + 1)
    ]
    expanded_rows = [
        {
            "title": f"CBD {idx}",
            "href": f"/events/view-section/00000000-0000-0000-0000-{idx:012d}",
            "state": "Complete",
            "date_text": "20 May, 2026",
        }
        for idx in range(1, total_available_count + 1)
    ]
    details = {
        f"https://kaizenep.com/events/view-section/00000000-0000-0000-0000-{idx:012d}": _detail(
            event_type=f"CBD {idx}",
            url=f"https://kaizenep.com/events/view-section/00000000-0000-0000-0000-{idx:012d}",
        )
        for idx in range(1, total_available_count + 1)
    }
    page = FakeLazyKaizenPage(
        initial_lists={assessment_url: initial_rows},
        expanded_lists={assessment_url: expanded_rows},
        details=details,
    )

    result = await kaizen_sync.sync_kaizen_portfolio_index(
        42,
        page,
        categories=("Assessments",),
        include_activities=False,
    )

    assert page.did_scroll_expand
    assert result.status == "ok"
    assert result.rows_seen == total_available_count
    assert result.rows_written == total_available_count
    rows = await kaizen_index.list_evidence_items("42")
    assert len(rows) == total_available_count
    latest = await kaizen_index.latest_index_run("42")
    assert latest is not None
    assert latest.rows_written == total_available_count


@pytest.mark.asyncio
async def test_sync_deduplicates_same_event_seen_in_two_categories(sync_modules):
    kaizen_index, kaizen_sync = sync_modules
    href = "/events/view-section/33333333-3333-3333-3333-333333333333"
    detail_url = f"https://kaizenep.com{href}"
    page = FakeKaizenPage(
        lists={
            "https://kaizenep.com/events/list/Assessments": [{"title": "LAT", "href": href}],
            "https://kaizenep.com/events/list/Manage%2C%20Administer%20%26%20Lead": [
                {"title": "LAT", "href": href}
            ],
        },
        details={detail_url: _detail(event_type="LAT", url=detail_url)},
    )

    result = await kaizen_sync.sync_kaizen_portfolio_index(
        42,
        page,
        categories=("Assessments", "Manage, Administer & Lead"),
        include_activities=False,
    )

    assert result.status == "ok"
    assert result.rows_seen == 2
    assert result.rows_written == 1
    rows = await kaizen_index.list_evidence_items("42")
    assert [row.id for row in rows] == ["33333333-3333-3333-3333-333333333333"]


@pytest.mark.asyncio
async def test_sync_records_partial_when_detail_shape_drifts(sync_modules):
    kaizen_index, kaizen_sync = sync_modules
    href = "/events/view-section/44444444-4444-4444-4444-444444444444"
    detail_url = f"https://kaizenep.com{href}"
    page = FakeKaizenPage(
        lists={"https://kaizenep.com/events/list/Assessments": [{"title": "CBD", "href": href}]},
        details={detail_url: RuntimeError("missing Formly read-only mount")},
    )

    result = await kaizen_sync.sync_kaizen_portfolio_index(
        42,
        page,
        categories=("Assessments",),
        include_activities=False,
    )

    assert result.status == "drift"
    assert result.rows_seen == 1
    assert result.rows_written == 0
    assert result.rows_drifted == 1
    assert await kaizen_index.count_evidence_items("42") == 0
    latest = await kaizen_index.latest_index_run("42")
    assert latest is not None
    assert latest.status == "drift"
    assert "detail drift" in (latest.notes or "")


@pytest.mark.asyncio
async def test_sync_marks_auth_required_on_auth_redirect(sync_modules):
    kaizen_index, kaizen_sync = sync_modules
    url = "https://kaizenep.com/events/list/Assessments"
    page = FakeKaizenPage(auth_urls={url})

    result = await kaizen_sync.sync_kaizen_portfolio_index(
        42,
        page,
        categories=("Assessments",),
        include_activities=False,
    )

    assert result.status == "auth_required"
    assert result.rows_written == 0
    latest = await kaizen_index.latest_index_run("42")
    assert latest is not None
    assert latest.status == "auth_required"


def test_sync_driver_has_no_write_side_browser_actions(sync_modules):
    _, kaizen_sync = sync_modules
    source = inspect.getsource(kaizen_sync)

    assert ".click(" not in source
    assert ".fill(" not in source
    assert ".type(" not in source
    assert "file_to_kaizen" not in source
    assert "save_draft" not in source
    assert "delete_all_drafts" not in source


# ── Trusted-bootstrap helper coverage ───────────────────────────────────────


class FakeContext:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


def _make_session(*, lists=None, details=None, auth_urls=None):
    page = FakeKaizenPage(lists=lists, details=details, auth_urls=auth_urls)
    page.context = FakeContext()
    pw = FakePlaywright()
    return page, pw


def _install_session_stubs(
    monkeypatch,
    kaizen_sync,
    *,
    page,
    pw,
    cached_result=False,
    cached_exc=None,
    credentials=("trainee", "pw"),
    login_result=True,
    login_exc=None,
    save_state_exc=None,
    creds_calls=None,
    cached_calls=None,
    login_calls=None,
    save_state_calls=None,
):
    creds_calls = creds_calls if creds_calls is not None else []
    cached_calls = cached_calls if cached_calls is not None else []
    login_calls = login_calls if login_calls is not None else []
    save_state_calls = save_state_calls if save_state_calls is not None else []

    async def fake_open():
        return page, pw

    async def fake_cached(arg_page, arg_uid, username=None):
        assert arg_page is page
        cached_calls.append((arg_uid, username))
        if cached_exc is not None:
            raise cached_exc
        return cached_result

    def fake_creds(uid):
        creds_calls.append(uid)
        return credentials

    async def fake_login(arg_page, username, password):
        login_calls.append((username, password))
        if login_exc is not None:
            raise login_exc
        return login_result

    async def fake_save_state(ctx, uid, username=None):
        save_state_calls.append((ctx, uid, username))
        if save_state_exc is not None:
            raise save_state_exc

    monkeypatch.setattr(kaizen_sync, "_open_kaizen_session_page", fake_open)
    monkeypatch.setattr(kaizen_sync, "_restore_cached_session", fake_cached)
    monkeypatch.setattr(kaizen_sync, "_load_user_credentials", fake_creds)
    monkeypatch.setattr(kaizen_sync, "_login_kaizen_page", fake_login)
    monkeypatch.setattr(kaizen_sync, "_persist_session_state", fake_save_state)
    return {
        "creds_calls": creds_calls,
        "cached_calls": cached_calls,
        "login_calls": login_calls,
        "save_state_calls": save_state_calls,
    }


def _assessments_url():
    return "https://kaizenep.com/events/list/Assessments"


def _seed_one_event(page):
    url = _assessments_url()
    href = "/events/view-section/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    detail_url = f"https://kaizenep.com{href}"
    page.lists[url] = [{"title": "CBD", "href": href}]
    page.details[detail_url] = _detail(url=detail_url)


@pytest.mark.asyncio
async def test_sync_for_user_uses_cached_session_without_credentials(sync_modules, monkeypatch):
    kaizen_index, kaizen_sync = sync_modules
    page, pw = _make_session()
    _seed_one_event(page)
    calls = _install_session_stubs(
        monkeypatch,
        kaizen_sync,
        page=page,
        pw=pw,
        cached_result=True,
        login_exc=AssertionError("login must not run when cache is valid"),
        credentials=None,
    )

    result = await kaizen_sync.sync_kaizen_portfolio_index_for_user(
        42,
        categories=("Assessments",),
        include_activities=False,
    )

    assert result.status == "ok"
    assert result.rows_written == 1
    assert calls["creds_calls"] == [42]
    assert calls["cached_calls"] == [(42, None)]
    assert calls["login_calls"] == []
    assert calls["save_state_calls"] == []
    assert page.context.closed
    assert pw.stopped


@pytest.mark.asyncio
async def test_sync_for_user_logs_in_when_cache_is_stale(sync_modules, monkeypatch):
    kaizen_index, kaizen_sync = sync_modules
    page, pw = _make_session()
    _seed_one_event(page)
    calls = _install_session_stubs(
        monkeypatch,
        kaizen_sync,
        page=page,
        pw=pw,
        cached_result=False,
        credentials=("trainee@example.com", "secret"),
        login_result=True,
    )

    result = await kaizen_sync.sync_kaizen_portfolio_index_for_user(
        99,
        categories=("Assessments",),
        include_activities=False,
    )

    assert result.status == "ok"
    assert result.rows_written == 1
    assert calls["creds_calls"] == [99]
    assert calls["cached_calls"] == [(99, "trainee@example.com")]
    assert calls["login_calls"] == [("trainee@example.com", "secret")]
    assert calls["save_state_calls"] and calls["save_state_calls"][0][1:] == (99, "trainee@example.com")
    assert page.context.closed
    assert pw.stopped


@pytest.mark.asyncio
async def test_sync_for_user_records_auth_required_when_no_credentials(sync_modules, monkeypatch):
    kaizen_index, kaizen_sync = sync_modules
    page, pw = _make_session()
    calls = _install_session_stubs(
        monkeypatch,
        kaizen_sync,
        page=page,
        pw=pw,
        cached_result=False,
        credentials=None,
        login_exc=AssertionError("login must not run when there are no credentials"),
    )

    result = await kaizen_sync.sync_kaizen_portfolio_index_for_user(
        7,
        categories=("Assessments",),
        include_activities=False,
    )

    assert result.status == "auth_required"
    assert result.rows_written == 0
    assert calls["login_calls"] == []
    assert calls["save_state_calls"] == []
    latest = await kaizen_index.latest_index_run("7")
    assert latest is not None
    assert latest.status == "auth_required"
    assert "credentials" in (latest.notes or "")
    assert page.context.closed
    assert pw.stopped


@pytest.mark.asyncio
async def test_sync_for_user_records_auth_required_when_login_fails(sync_modules, monkeypatch):
    kaizen_index, kaizen_sync = sync_modules
    page, pw = _make_session()
    calls = _install_session_stubs(
        monkeypatch,
        kaizen_sync,
        page=page,
        pw=pw,
        cached_result=False,
        credentials=("trainee", "pw"),
        login_result=False,
    )

    result = await kaizen_sync.sync_kaizen_portfolio_index_for_user(
        11,
        categories=("Assessments",),
        include_activities=False,
    )

    assert result.status == "auth_required"
    assert calls["login_calls"] == [("trainee", "pw")]
    assert calls["save_state_calls"] == []
    assert page.context.closed
    assert pw.stopped


@pytest.mark.asyncio
async def test_sync_for_user_records_failed_when_cdp_unavailable(sync_modules, monkeypatch):
    kaizen_index, kaizen_sync = sync_modules

    async def fake_open():
        return None, None

    monkeypatch.setattr(kaizen_sync, "_open_kaizen_session_page", fake_open)

    def _fail_creds(uid):
        raise AssertionError("credentials must not be requested when CDP is unavailable")

    async def _fail_cached(*_args, **_kwargs):
        raise AssertionError("cached session must not run when CDP is unavailable")

    monkeypatch.setattr(kaizen_sync, "_load_user_credentials", _fail_creds)
    monkeypatch.setattr(kaizen_sync, "_restore_cached_session", _fail_cached)

    result = await kaizen_sync.sync_kaizen_portfolio_index_for_user(
        13,
        categories=("Assessments",),
        include_activities=False,
    )

    assert result.status == "failed"
    latest = await kaizen_index.latest_index_run("13")
    assert latest is not None
    assert latest.status == "failed"
    assert "CDP" in (latest.notes or "")


@pytest.mark.asyncio
async def test_sync_for_user_closes_session_even_when_sync_raises(sync_modules, monkeypatch):
    kaizen_index, kaizen_sync = sync_modules
    page, pw = _make_session()
    _install_session_stubs(
        monkeypatch,
        kaizen_sync,
        page=page,
        pw=pw,
        cached_result=True,
    )

    async def boom(*_args, **_kwargs):
        raise RuntimeError("simulated sync failure")

    monkeypatch.setattr(kaizen_sync, "sync_kaizen_portfolio_index", boom)

    with pytest.raises(RuntimeError):
        await kaizen_sync.sync_kaizen_portfolio_index_for_user(
            55,
            categories=("Assessments",),
            include_activities=False,
        )

    assert page.context.closed
    assert pw.stopped


@pytest.mark.asyncio
async def test_sync_reads_signoff_state_from_progress_icon_class(sync_modules):
    """Kaizen's sign-off state lives in the progress icon's class modifier.

    The icons carry no text, so reading textContent stored NULL for every row
    and the whole sign-off workflow was invisible. Guard the class read, and
    guard that an item awaiting someone else outranks its completed sections.
    """
    kaizen_index, kaizen_sync = sync_modules
    href = "/events/view-section/44444444-4444-4444-4444-444444444444"
    detail_url = f"https://kaizenep.com{href}"
    page = FakeKaizenPage(
        lists={
            "https://kaizenep.com/events/list/Assessments": [
                {
                    "title": "CBD - Case Based Discussion (2025 update)",
                    "href": href,
                    "section_states": [
                        {"state": "complete", "label": "This section is completed"},
                        {"state": "pending", "label": "This section is awaiting a response"},
                        {"state": "missing", "label": "Future section in the workflow"},
                    ],
                    "date_text": "5 Jun, 2026",
                }
            ]
        },
        # Detail pages carry no progress icons — the listing is the only source.
        details={detail_url: _detail(state=None, url=detail_url)},
    )

    await kaizen_sync.sync_kaizen_portfolio_index(
        77, page, categories=("Assessments",), include_activities=False
    )

    rows = await kaizen_index.list_evidence_items("77")
    assert len(rows) == 1
    assert rows[0].state == "pending"


@pytest.mark.asyncio
async def test_sync_rolls_up_to_complete_when_no_section_is_waiting(sync_modules):
    kaizen_index, kaizen_sync = sync_modules
    href = "/events/view-section/55555555-5555-5555-5555-555555555555"
    detail_url = f"https://kaizenep.com{href}"
    page = FakeKaizenPage(
        lists={
            "https://kaizenep.com/events/list/Assessments": [
                {
                    "title": "Mini-CEX (2025 Update)",
                    "href": href,
                    "section_states": [
                        {"state": "complete", "label": "This section is completed"},
                        {"state": "skipped", "label": "This section was intentionally skipped"},
                    ],
                    "date_text": "5 Jun, 2026",
                }
            ]
        },
        details={detail_url: _detail(state=None, url=detail_url)},
    )

    await kaizen_sync.sync_kaizen_portfolio_index(
        78, page, categories=("Assessments",), include_activities=False
    )

    rows = await kaizen_index.list_evidence_items("78")
    assert rows[0].state == "complete"


@pytest.mark.asyncio
async def test_sync_flags_truncation_when_listing_never_finishes_loading(sync_modules):
    """A half-loaded listing invents evidence gaps, so it must not pass as ok."""
    kaizen_index, kaizen_sync = sync_modules
    href = "/events/view-section/66666666-6666-6666-6666-666666666666"
    detail_url = f"https://kaizenep.com{href}"
    page = FakeKaizenPage(
        scroll_settles=False,
        lists={
            "https://kaizenep.com/events/list/Assessments": [
                {"title": "CBD", "href": href, "section_states": [], "date_text": "5 Jun, 2026"}
            ]
        },
        details={detail_url: _detail(url=detail_url)},
    )

    result = await kaizen_sync.sync_kaizen_portfolio_index(
        79, page, categories=("Assessments",), include_activities=False
    )

    assert result.status == "partial"
    assert any("truncated" in note for note in result.notes)

    latest = await kaizen_index.latest_index_run("79")
    assert latest is not None
    assert latest.status == "partial"


@pytest.mark.asyncio
async def test_second_sync_skips_detail_pages_for_unchanged_rows(sync_modules):
    """Reading every listing row in full made scans several times longer. A row
    whose sign-off state has not moved has nothing new on its detail page."""
    kaizen_index, kaizen_sync = sync_modules
    href = "/events/view-section/77777777-7777-7777-7777-777777777777"
    detail_url = f"https://kaizenep.com{href}"
    listing = {
        "https://kaizenep.com/events/list/Assessments": [
            {
                "title": "CBD",
                "href": href,
                "section_states": [{"state": "pending", "label": "This section is awaiting a response"}],
                "date_text": "5 Jun, 2026",
            }
        ]
    }
    page = FakeKaizenPage(lists=listing, details={detail_url: _detail(state=None, url=detail_url)})

    first = await kaizen_sync.sync_kaizen_portfolio_index(
        81, page, categories=("Assessments",), include_activities=False
    )
    second = await kaizen_sync.sync_kaizen_portfolio_index(
        81, page, categories=("Assessments",), include_activities=False
    )

    assert first.rows_written == 1 and first.rows_refreshed == 0
    assert second.rows_written == 0 and second.rows_refreshed == 1
    assert page.visited.count(detail_url) == 1

    rows = await kaizen_index.list_evidence_items("81")
    assert len(rows) == 1
    # The cheap refresh must not blank the fields the detail read supplied.
    assert rows[0].description == "Senior-led resus case"
    assert rows[0].state == "pending"


@pytest.mark.asyncio
async def test_full_refresh_reopens_detail_pages(sync_modules):
    kaizen_index, kaizen_sync = sync_modules
    href = "/events/view-section/88888888-8888-8888-8888-888888888888"
    detail_url = f"https://kaizenep.com{href}"
    listing = {
        "https://kaizenep.com/events/list/Assessments": [
            {
                "title": "CBD",
                "href": href,
                "section_states": [{"state": "pending", "label": "awaiting"}],
                "date_text": "5 Jun, 2026",
            }
        ]
    }
    page = FakeKaizenPage(lists=listing, details={detail_url: _detail(state=None, url=detail_url)})

    await kaizen_sync.sync_kaizen_portfolio_index(
        82, page, categories=("Assessments",), include_activities=False
    )
    await kaizen_sync.sync_kaizen_portfolio_index(
        82, page, categories=("Assessments",), include_activities=False, full_refresh=True
    )

    assert page.visited.count(detail_url) == 2


@pytest.mark.asyncio
async def test_sync_records_the_moment_a_pending_item_gets_signed_off(sync_modules):
    """The transition is the event a proactive watcher reacts to."""
    kaizen_index, kaizen_sync = sync_modules
    href = "/events/view-section/99999999-9999-9999-9999-999999999999"
    detail_url = f"https://kaizenep.com{href}"

    def listing(state):
        return {
            "https://kaizenep.com/events/list/Assessments": [
                {
                    "title": "CBD",
                    "href": href,
                    "section_states": [{"state": state, "label": state}],
                    "date_text": "5 Jun, 2026",
                }
            ]
        }

    details = {detail_url: _detail(state=None, url=detail_url)}
    await kaizen_sync.sync_kaizen_portfolio_index(
        83, FakeKaizenPage(lists=listing("pending"), details=details),
        categories=("Assessments",), include_activities=False,
    )
    before = (await kaizen_index.list_evidence_items("83"))[0]

    await kaizen_sync.sync_kaizen_portfolio_index(
        83, FakeKaizenPage(lists=listing("complete"), details=details),
        categories=("Assessments",), include_activities=False,
    )
    after = (await kaizen_index.list_evidence_items("83"))[0]

    assert before.state == "pending" and before.previous_state is None
    assert after.state == "complete"
    assert after.previous_state == "pending"
    assert after.state_since != before.state_since


@pytest.mark.asyncio
async def test_state_since_holds_still_while_an_item_stays_stuck(sync_modules):
    """"Pending since" must survive every re-sync that sees the same state."""
    kaizen_index, kaizen_sync = sync_modules
    href = "/events/view-section/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    detail_url = f"https://kaizenep.com{href}"
    listing = {
        "https://kaizenep.com/events/list/Assessments": [
            {
                "title": "CBD",
                "href": href,
                "section_states": [{"state": "pending", "label": "awaiting"}],
                "date_text": "5 Jun, 2026",
            }
        ]
    }
    details = {detail_url: _detail(state=None, url=detail_url)}

    await kaizen_sync.sync_kaizen_portfolio_index(
        84, FakeKaizenPage(lists=listing, details=details),
        categories=("Assessments",), include_activities=False,
    )
    first_seen_pending = (await kaizen_index.list_evidence_items("84"))[0].state_since

    for _ in range(3):
        await kaizen_sync.sync_kaizen_portfolio_index(
            84, FakeKaizenPage(lists=listing, details=details),
            categories=("Assessments",), include_activities=False,
        )

    assert (await kaizen_index.list_evidence_items("84"))[0].state_since == first_seen_pending
