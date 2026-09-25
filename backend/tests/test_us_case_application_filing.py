"""Ultrasound Case: the "Ultrasound application used" tick-boxes reach Kaizen.

Dogfood regression (25 Sep 2026): the draft said "Ultrasound Application:
AAA", the entry saved, and the box on Kaizen was left unticked. The field had
no Kaizen mapping at all, so the filer dropped it without reporting a gap and
the closure message claimed a clean save.

On Kaizen the question is a `kz-tree` widget (a search box over a list of
tick-boxes with no ids of their own) inside DIV#69878c05-…, the id its
label's `for` points at. It is filled by the same label-matching widget
filler as ESLE's domains, which confirms the selection by reading the widget
back rather than trusting the click.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import kaizen_form_filer as kff

US_APPLICATION_DOM_ID = "69878c05-4fbc-4e1b-9307-54a3a3a9ca8a"
US_OPTIONS = ["AAA", "ELS", "FAST", "Vascular Access", "Other"]


def _tree_page(selected_after_pick):
    page = MagicMock()
    page.picked = []

    async def evaluate(script, arg=None):
        if script == kff._WIDGET_PICK_JS:
            page.picked.append(arg["wanted"])
            return arg["wanted"] in selected_after_pick
        if script == kff._WIDGET_STATE_JS:
            return {
                "missing": False,
                "options": [
                    {"text": option, "selected": option in selected_after_pick}
                    for option in US_OPTIONS
                ],
                "chips": [],
                "text": " ".join(US_OPTIONS),
            }
        return None

    page.evaluate = AsyncMock(side_effect=evaluate)
    locator = MagicMock()
    locator.count = AsyncMock(return_value=1)
    locator.click = AsyncMock()
    locator.first = locator
    page.locator = MagicMock(return_value=locator)
    return page


@pytest.fixture
def instant_sleep(monkeypatch):
    async def _noop(*args, **kwargs):
        pass

    monkeypatch.setattr("kaizen_form_filer.asyncio.sleep", _noop)


@pytest.mark.parametrize("form_type", ["US_CASE", "US_CASE_2021"])
def test_ultrasound_application_is_mapped(form_type):
    base = kff._FORM_FIELD_MAP_VARIANT_BASES.get(form_type, form_type)
    assert kff.FORM_FIELD_MAP[base]["us_application"] == US_APPLICATION_DOM_ID
    assert "us_application" in kff._MULTISELECT_WIDGET_FIELDS


@pytest.mark.asyncio
async def test_drafted_application_is_ticked(instant_sleep):
    page = _tree_page({"AAA"})

    filled = await kff._fill_multiselect_widget(
        page, US_APPLICATION_DOM_ID, ["AAA"], field_key="us_application"
    )

    assert filled is True
    assert page.picked == ["AAA"]


@pytest.mark.asyncio
async def test_application_values_are_matched_to_kaizen_labels(instant_sleep):
    """Case and spacing differences from the model still tick the real
    option; a value Kaizen does not offer is never clicked."""
    page = _tree_page({"FAST", "Vascular Access"})

    filled = await kff._fill_multiselect_widget(
        page,
        US_APPLICATION_DOM_ID,
        "fast, vascular  access, Lung",
        field_key="us_application",
    )

    assert filled is True
    assert page.picked == ["FAST", "Vascular Access"]


@pytest.mark.asyncio
async def test_a_tick_that_did_not_stick_is_not_reported_filled(instant_sleep):
    page = _tree_page(set())

    assert await kff._fill_multiselect_widget(
        page, US_APPLICATION_DOM_ID, ["AAA"], field_key="us_application"
    ) is False


@pytest.mark.asyncio
async def test_esle_domains_keep_their_own_rules(instant_sleep):
    """Generalising the filler must not drop ESLE's All-Domains exclusivity."""
    from esle_domains import ALL_DOMAINS, DECISION_MAKING

    page = MagicMock()
    page.picked = []

    async def evaluate(script, arg=None):
        if script == kff._WIDGET_PICK_JS:
            page.picked.append(arg["wanted"])
            return True
        if script == kff._WIDGET_STATE_JS:
            return {"missing": False, "options": [{"text": DECISION_MAKING, "selected": True}], "chips": []}
        return None

    page.evaluate = AsyncMock(side_effect=evaluate)
    locator = MagicMock()
    locator.count = AsyncMock(return_value=1)
    locator.click = AsyncMock()
    locator.first = locator
    page.locator = MagicMock(return_value=locator)

    await kff._fill_multiselect_widget(
        page, "7683f17f", [ALL_DOMAINS, DECISION_MAKING], field_key="domains_of_performance"
    )

    assert page.picked == [DECISION_MAKING]
