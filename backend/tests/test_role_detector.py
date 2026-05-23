"""Tests for the Kaizen role detector.

The detector classifies a logged-in Kaizen account as ``trainee`` or
``assessor`` (or ``unknown``) by inspecting the MyTimeline body text for the
"You cannot create any events!" barrier that pure-supervisor accounts render.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

import role_detector


def test_classify_role_returns_assessor_on_exact_barrier_text():
    body = "You cannot create any events!"
    assert role_detector.classify_role_from_timeline_text(body) == "assessor"


def test_classify_role_returns_assessor_when_barrier_embedded_in_chrome():
    # MyTimeline ships nav + a heading + the barrier line.
    body = (
        "My Timeline\n"
        "Welcome Ahmed Mahdi\n"
        "You cannot create any events!\n"
        "Logout"
    )
    assert role_detector.classify_role_from_timeline_text(body) == "assessor"


def test_classify_role_is_case_insensitive_for_barrier():
    body = "YOU CANNOT CREATE ANY EVENTS!"
    assert role_detector.classify_role_from_timeline_text(body) == "assessor"


def test_classify_role_returns_trainee_when_marker_present_without_barrier():
    body = "My Timeline\nCreate new event\nFilter by event type"
    assert role_detector.classify_role_from_timeline_text(body) == "trainee"


def test_classify_role_returns_unknown_for_empty_or_blank_body():
    assert role_detector.classify_role_from_timeline_text(None) == "unknown"
    assert role_detector.classify_role_from_timeline_text("") == "unknown"
    assert role_detector.classify_role_from_timeline_text("   \n\t") == "unknown"


def test_classify_role_returns_unknown_when_no_markers_match():
    # Transient skeleton page or unrelated content.
    body = "Loading..."
    assert role_detector.classify_role_from_timeline_text(body) == "unknown"


async def test_detect_role_returns_assessor_when_barrier_visible():
    page = AsyncMock()
    page.goto = AsyncMock(return_value=None)
    page.evaluate = AsyncMock(return_value="You cannot create any events!")

    assert await role_detector.detect_role(page) == "assessor"
    page.goto.assert_awaited_once_with(
        role_detector.MY_TIMELINE_URL, wait_until="domcontentloaded"
    )


async def test_detect_role_returns_trainee_when_marker_visible():
    page = AsyncMock()
    page.goto = AsyncMock(return_value=None)
    page.evaluate = AsyncMock(return_value="My Timeline\nCreate new event")

    assert await role_detector.detect_role(page) == "trainee"


async def test_detect_role_returns_unknown_when_navigation_fails():
    page = AsyncMock()
    page.goto = AsyncMock(side_effect=RuntimeError("Kaizen 500"))
    page.evaluate = AsyncMock()

    assert await role_detector.detect_role(page) == "unknown"
    page.evaluate.assert_not_awaited()


async def test_detect_role_returns_unknown_when_body_read_fails():
    page = AsyncMock()
    page.goto = AsyncMock(return_value=None)
    page.evaluate = AsyncMock(side_effect=RuntimeError("evaluate boom"))

    assert await role_detector.detect_role(page) == "unknown"


def test_role_detector_module_never_clicks_write_controls():
    """Mirror of the read-only contract enforced by other assessor modules."""
    source = inspect.getsource(role_detector)
    forbidden_snippets = [
        "click('text=Sign",
        "click('text=Submit",
        "click('text=Approve",
        "click('text=Delete",
        "click('text=Save",
        "click('text=Send",
        "click('text=Fill",
        'click("text=Sign',
        'click("text=Submit',
        'click("text=Approve',
        'click("text=Delete',
        'click("text=Save',
        'click("text=Send',
        'click("text=Fill',
        "get_by_text('Sign",
        "get_by_text('Submit",
        "get_by_text('Approve",
        "get_by_text('Delete",
        "get_by_text('Save",
        "get_by_text('Send",
        "get_by_text('Fill in",
    ]

    for snippet in forbidden_snippets:
        assert snippet not in source, f"Role detector source contains forbidden write action: {snippet}"
