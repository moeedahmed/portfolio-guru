"""Reflective Practice Log Kaizen dropdown filing guardrails."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import kaizen_form_filer
from kaizen_form_filer import FORM_FIELD_MAP, _fill_field_legacy, _fill_select


def test_reflect_log_event_type_field_is_mapped_to_kaizen_dropdown_uuid():
    assert (
        FORM_FIELD_MAP["REFLECT_LOG"]["event_type"]
        == "af0d96f8-9fea-4302-9cb1-06ea7500f0e1"
    )


@pytest.mark.asyncio
async def test_reflect_log_event_type_selects_ed_patient_label(monkeypatch):
    async def _noop_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(kaizen_form_filer.asyncio, "sleep", _noop_sleep)

    select = AsyncMock()
    select.count = AsyncMock(return_value=1)
    select.select_option = AsyncMock()

    page = MagicMock()
    page.locator = MagicMock(return_value=select)

    ok = await _fill_select(
        page,
        FORM_FIELD_MAP["REFLECT_LOG"]["event_type"],
        "ED patient",
    )

    assert ok is True
    page.locator.assert_called_once_with(
        '[id="af0d96f8-9fea-4302-9cb1-06ea7500f0e1"]'
    )
    select.select_option.assert_awaited_once_with(label="ED patient")


@pytest.mark.asyncio
async def test_legacy_reflect_log_event_type_uses_verified_select_filler(monkeypatch):
    select = AsyncMock()
    select.count = AsyncMock(return_value=1)
    select.evaluate = AsyncMock(return_value="SELECT")

    page = MagicMock()
    page.locator = MagicMock(return_value=select)

    calls = []

    async def fake_fill_select(page_arg, dom_id_arg, value_arg):
        calls.append((page_arg, dom_id_arg, value_arg))
        return True

    monkeypatch.setattr(kaizen_form_filer, "_fill_select", fake_fill_select)

    ok = await _fill_field_legacy(
        page,
        FORM_FIELD_MAP["REFLECT_LOG"]["event_type"],
        "ED patient",
        "event_type",
        "REFLECT_LOG",
    )

    assert ok is True
    assert calls == [
        (
            page,
            FORM_FIELD_MAP["REFLECT_LOG"]["event_type"],
            "ED patient",
        )
    ]


@pytest.mark.asyncio
async def test_legacy_reflect_log_header_dates_use_verified_date_filler(monkeypatch):
    async def _noop_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(kaizen_form_filer.asyncio, "sleep", _noop_sleep)

    date_input = AsyncMock()
    date_input.count = AsyncMock(return_value=1)
    date_input.evaluate = AsyncMock(side_effect=["INPUT", "27/5/2026"])
    date_input.click = AsyncMock()
    date_input.type = AsyncMock()

    page = MagicMock()
    page.locator = MagicMock(return_value=date_input)
    page.keyboard.press = AsyncMock()

    ok = await _fill_field_legacy(
        page,
        "startDate",
        "2026-05-27",
        "date_of_encounter",
        "REFLECT_LOG",
    )

    assert ok is True
    date_input.click.assert_any_await()
    date_input.click.assert_any_await(click_count=3)
    date_input.type.assert_awaited_once_with("27/5/2026", delay=50)
    page.keyboard.press.assert_awaited_once_with("Tab")
