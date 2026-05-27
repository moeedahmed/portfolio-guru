"""Reflective Practice Log Kaizen dropdown filing guardrails."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import kaizen_form_filer
from kaizen_form_filer import FORM_FIELD_MAP, _fill_select


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
