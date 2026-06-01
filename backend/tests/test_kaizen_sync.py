"""Offline tests for the read-only Kaizen sync driver.

No live Kaizen, browser, CDP, credentials, Telegram, launchd, or network. The
fake page below mimics the tiny Playwright surface the sync driver needs.
"""

from __future__ import annotations

import importlib
import inspect

import pytest


class FakeKaizenPage:
    def __init__(self, *, lists=None, details=None, auth_urls=None):
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

