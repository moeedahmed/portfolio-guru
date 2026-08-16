"""
Guard tests for kaizen_delete_draft.delete_draft.

The safety invariant: a draft is only ever deleted when its own page contains
the caller's expected content marker, the SweetAlert confirmation says what we
expect, and the marker is gone afterwards. These tests fail if any of those
checks stops gating the Delete click.
"""

import pytest

from kaizen_delete_draft import delete_draft


class FakeLocator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return self

    async def count(self):
        return 1 if self._selector in self._page.present_selectors else 0

    async def is_visible(self, timeout=None):
        return await self.count() > 0

    async def click(self):
        self._page.clicks.append(self._selector)

    async def inner_text(self):
        return self._page.selector_text.get(self._selector, "")


class FakePage:
    """Minimal stand-in for a Playwright page, scripted per test."""

    def __init__(self, body_texts, present_selectors, selector_text=None):
        # body_texts: innerText returned on each successive page load
        self._body_texts = list(body_texts)
        self.present_selectors = set(present_selectors)
        self.selector_text = selector_text or {}
        self.clicks = []
        self.visited = []

    async def goto(self, url, **kwargs):
        self.visited.append(url)

    async def evaluate(self, _script):
        return self._body_texts.pop(0) if self._body_texts else ""

    def locator(self, selector):
        return FakeLocator(self, selector)

    async def wait_for_selector(self, selector, timeout=None):
        if selector not in self.present_selectors:
            raise TimeoutError(f"no {selector}")
        return FakeLocator(self, selector)


DELETE_LINK = "a.text-danger:has-text('Delete')"
CONFIRM_DIALOG = ".sweet-alert.visible"
CONFIRM_BUTTON = ".sweet-alert.visible button.confirm"
CONFIRMED = {DELETE_LINK, CONFIRM_DIALOG, CONFIRM_BUTTON}
CONFIRM_PROMPT = {CONFIRM_DIALOG: "You will not be able to remove this event again!"}


async def test_refuses_to_delete_when_marker_absent():
    page = FakePage(["Some real clinical case about chest pain"], CONFIRMED, CONFIRM_PROMPT)

    result = await delete_draft(page, "123456", "AI declaration test", dry_run=False)

    assert result["deleted"] is False
    assert result["status"] == "skipped-marker-absent"
    assert page.clicks == []


async def test_dry_run_never_clicks_delete():
    page = FakePage(["AI declaration test draft"], CONFIRMED, CONFIRM_PROMPT)

    result = await delete_draft(page, "123456", "AI declaration test", dry_run=True)

    assert result["status"] == "would-delete"
    assert result["deleted"] is False
    assert result["delete_control_present"] is True
    assert page.clicks == []


async def test_dry_run_reports_missing_delete_control():
    """A drifted Delete selector must show up in the dry run, not at delete time."""
    page = FakePage(["AI declaration test draft"], CONFIRMED - {DELETE_LINK}, CONFIRM_PROMPT)

    result = await delete_draft(page, "123456", "AI declaration test", dry_run=True)

    assert result["delete_control_present"] is False
    assert page.clicks == []


async def test_deletes_and_verifies_by_doc_id():
    page = FakePage(
        ["AI declaration test draft", "Event not found"],  # before, then after reload
        CONFIRMED,
        CONFIRM_PROMPT,
    )

    result = await delete_draft(page, "123456", "AI declaration test", dry_run=False)

    assert result["deleted"] is True
    assert result["status"] == "deleted"
    assert DELETE_LINK in page.clicks and CONFIRM_BUTTON in page.clicks
    # Verification re-opens the same doc id rather than counting list rows.
    assert page.visited.count("https://kaizenep.com/events/view-section/123456") == 2


async def test_unverified_delete_is_not_reported_as_deleted():
    page = FakePage(
        ["AI declaration test draft", "AI declaration test draft"],  # still there after
        CONFIRMED,
        CONFIRM_PROMPT,
    )

    result = await delete_draft(page, "123456", "AI declaration test", dry_run=False)

    assert result["deleted"] is False
    assert result["status"] == "delete-not-verified"


async def test_unexpected_confirm_dialog_aborts_before_confirming():
    page = FakePage(
        ["AI declaration test draft"],
        CONFIRMED,
        {CONFIRM_DIALOG: "Log out of all sessions?"},
    )

    result = await delete_draft(page, "123456", "AI declaration test", dry_run=False)

    assert result["deleted"] is False
    assert result["status"] == "unexpected-confirm-dialog"
    assert CONFIRM_BUTTON not in page.clicks
