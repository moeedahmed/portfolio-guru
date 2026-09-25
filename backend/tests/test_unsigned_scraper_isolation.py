"""/unsigned must read the requesting doctor's Kaizen account, never another's.

The scraper used to reuse any Kaizen page already open in the shared Chrome, or
that browser's default context, and only signed in if it hit a login page.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import kaizen_unsigned_scraper as scraper


@pytest.mark.asyncio
async def test_scan_uses_a_fresh_private_context_and_signs_in_as_the_caller():
    someone_elses_page = MagicMock(url="https://kaizenep.com/activities")
    shared_context = MagicMock(pages=[someone_elses_page])

    private_page = MagicMock(url="https://auth.kaizenep.com/interaction/x/login")
    private_page.goto = AsyncMock()
    private_page.inner_text = AsyncMock(return_value="")
    private_page.locator = MagicMock(return_value=MagicMock(all=AsyncMock(return_value=[])))
    private_context = MagicMock(new_page=AsyncMock(return_value=private_page), close=AsyncMock())

    browser = MagicMock(contexts=[shared_context], new_context=AsyncMock(return_value=private_context))
    pw = MagicMock(stop=AsyncMock())
    pw.chromium.connect_over_cdp = AsyncMock(return_value=browser)
    starter = MagicMock(start=AsyncMock(return_value=pw))

    with patch.object(scraper, "async_playwright", return_value=starter), \
         patch.object(scraper, "_login_via_rcem", new=AsyncMock(return_value=True)) as login, \
         patch.object(scraper.asyncio, "sleep", new=AsyncMock()):
        await scraper.scrape_unsigned_tickets("doc@example.nhs.uk", "pw")

    browser.new_context.assert_awaited_once()
    someone_elses_page.goto.assert_not_called()
    login.assert_awaited_once_with(private_page, "doc@example.nhs.uk", "pw")
    private_context.close.assert_awaited_once()
