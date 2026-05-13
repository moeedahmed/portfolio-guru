from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaizen_form_filer import _activity_date_variants, _verify_entry_saved, file_to_kaizen


class FakePage:
    def __init__(self, body: str = "", url: str = "https://kaizenep.com/events/new-section/form"):
        self.url = url
        self.goto = AsyncMock()
        self.inner_text = AsyncMock(return_value=body)


@pytest.mark.asyncio
async def test_verify_entry_saved_uses_activity_date_not_today():
    body = "Saved drafts\nProcedural Log ST3-ST6\n17/3/2026\n"
    page = FakePage(body=body)

    with patch("kaizen_form_filer.asyncio.sleep", new=AsyncMock()):
        result = await _verify_entry_saved(
            page,
            "PROC_LOG",
            {"date_of_activity": "2026-03-17"},
        )

    assert result is True


@pytest.mark.asyncio
async def test_verify_entry_saved_accepts_saved_draft_url_fallback():
    page = FakePage(url="https://kaizenep.com/events/fillin/document-id?autosave=autosave-id")

    result = await _verify_entry_saved(page, "PROC_LOG", {"date_of_activity": "2026-03-17"})

    assert result is True
    page.goto.assert_not_called()


def test_activity_date_variants_include_backdated_arcp_formats():
    variants = _activity_date_variants({"date_of_activity": "2026-03-17"})

    assert "17/3/2026" in variants
    assert "17/03/2026" in variants
    assert "17 Mar 2026" in variants


@pytest.mark.asyncio
async def test_repeated_tickets_create_new_forms_by_default():
    page = FakePage()
    browser = AsyncMock()
    browser.new_page = AsyncMock(return_value=page)
    pw = AsyncMock()
    pw.chromium.launch = AsyncMock(return_value=browser)
    ap = MagicMock()
    ap.start = AsyncMock(return_value=pw)

    fields = {
        "date_of_activity": "2026-03-17",
        "end_date": "2026-03-17",
        "stage_of_training": "Higher",
        "year_of_training": "ST5",
        "higher_procedural_skill": "Adult sedation",
        "intermediate_procedural_skill": "- n/a -",
        "accs_procedural_skill": "- n/a -",
        "age_of_patient": "Adult",
        "reflective_comments": "Procedure completed with senior support.",
    }

    with patch("kaizen_form_filer.async_playwright", return_value=ap), \
         patch("kaizen_form_filer.KAIZEN_USE_CDP", False), \
         patch("kaizen_form_filer._login", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._find_existing_draft", new=AsyncMock(return_value=True)) as find_existing, \
         patch("kaizen_form_filer._fill_field_legacy", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._save_draft_legacy", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer._verify_entry_saved", new=AsyncMock(return_value=True)), \
         patch("kaizen_form_filer.asyncio.sleep", new=AsyncMock()):
        first = await file_to_kaizen("PROC_LOG", fields, "user", "pass")
        second = await file_to_kaizen("PROC_LOG", fields, "user", "pass")

    assert first["status"] == "success"
    assert second["status"] == "success"
    find_existing.assert_not_awaited()
    assert page.goto.await_count == 2
