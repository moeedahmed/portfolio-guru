"""Offline reliability matrix for the cloud-migration filing gate.

This test surface is intentionally local-only. It proves route readiness for
the priority forms and keeps the cloud migration gate red until live Kaizen
draft-save proof is supplied separately.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from filing_reliability_matrix import (
    FORBIDDEN_FORM_CODES,
    PRIORITY_FORMS,
    build_matrix_report,
    build_ticket_fields,
)


def test_matrix_covers_cloud_readiness_priority_forms():
    assert PRIORITY_FORMS == (
        "CBD",
        "DOPS",
        "MINI_CEX",
        "ACAT",
        "QIAT",
        "TEACH",
        "REFLECT_LOG",
        "PROC_LOG",
    )
    assert not (set(PRIORITY_FORMS) & FORBIDDEN_FORM_CODES)


def test_priority_forms_are_offline_route_ready_but_cloud_remains_blocked():
    report = build_matrix_report()

    assert report.offline_route_ready is True
    assert report.route_ready_count == report.route_total == len(PRIORITY_FORMS)
    assert report.cloud_migration_ready is False
    assert "controlled live draft-save evidence" in report.blocked_reason
    assert all(not case.blockers for case in report.cases)


def test_matrix_rejects_fake_legacy_form_codes():
    report = build_matrix_report(("CBD", "CEX", "CDD", "ALP"))
    blocked = {case.form_type: case.blockers for case in report.cases if case.blockers}

    assert report.offline_route_ready is False
    assert "forbidden_non_canonical_form_code" in blocked["CEX"]
    assert "forbidden_non_canonical_form_code" in blocked["CDD"]
    assert "forbidden_non_canonical_form_code" in blocked["ALP"]


@pytest.mark.parametrize("form_type", PRIORITY_FORMS)
def test_generated_ticket_fields_are_draft_only_and_uk_dated(form_type):
    fields = build_ticket_fields(form_type)

    assert fields["date_of_encounter"] == "15/4/2026"
    assert fields["end_date"] == "15/4/2026"
    assert "04/15/2026" not in str(fields)
    assert fields["description"]


@pytest.mark.asyncio
@pytest.mark.parametrize("form_type", PRIORITY_FORMS)
async def test_priority_forms_route_to_deterministic_draft_save_contract(form_type):
    from filer_router import route_filing

    fields = build_ticket_fields(form_type)
    deterministic = AsyncMock(return_value={
        "status": "success",
        "filled": sorted(fields),
        "skipped": [],
        "method": "deterministic",
        "saved_url": "https://kaizenep.com/events/fillin/synthetic-draft",
    })
    browser_use = AsyncMock(return_value={
        "status": "success",
        "filled": [],
        "skipped": [],
        "method": "browser-use",
    })

    with patch("filer_router._route_deterministic", new=deterministic), \
         patch("filer_router._route_browser_use", new=browser_use):
        result = await route_filing(
            platform="kaizen",
            form_type=form_type,
            fields=fields,
            credentials={"username": "doctor@example.com", "password": "not-real"},
            submit=False,
            reuse_draft=False,
            telegram_user_id=99999999,
        )

    assert result["status"] == "success"
    assert result["method"] == "deterministic"
    browser_use.assert_not_awaited()
    deterministic.assert_awaited_once()
    kwargs = deterministic.await_args.kwargs
    assert kwargs["submit"] is False
    assert kwargs["reuse_draft"] is False
    assert kwargs["telegram_user_id"] == 99999999
