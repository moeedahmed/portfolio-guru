"""
Kaizen Unsigned Tickets Scraper — async version for Portfolio Guru.
Connects via CDP when a managed browser is available (localhost:18800);
otherwise launches a headless Chromium and logs in directly using stored
credentials. Same pattern as kaizen_form_filer._connect_cdp.
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List

from playwright.async_api import async_playwright

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logger = logging.getLogger(__name__)

CDP_URL = os.environ.get("KAIZEN_CDP_URL", "http://localhost:18800")


def _parse_date(date_str: str) -> datetime | None:
    formats = ["%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


async def _login_via_rcem(page, username: str, password: str) -> bool:
    """Log in to Kaizen via the RCEM portal (two-step username then password)."""
    try:
        await page.goto("https://eportfolio.rcem.ac.uk", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)
        login_input = page.locator('input[name="login"]')
        if await login_input.count() > 0:
            await login_input.fill(username)
            await page.locator('button[type="submit"]').click()
            await asyncio.sleep(2)
        pwd_input = page.locator('input[name="password"]')
        if await pwd_input.count() > 0:
            await pwd_input.fill(password)
            await page.locator('button[type="submit"]').click()
        await page.wait_for_url("**/kaizenep.com/**", timeout=30000)
        await asyncio.sleep(3)
        return True
    except Exception as e:
        logger.error(f"RCEM login failed: {e}")
        return False


async def scrape_unsigned_tickets(
    username: str = "",
    password: str = "",
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> list[dict]:
    """Scrape unsigned tickets from Kaizen activities page.

    Connects via CDP when available, otherwise launches a headless Chromium
    and logs in fresh using the supplied credentials. Filters by event_date
    when from_date / to_date are provided (inclusive on both ends). Pass
    both as None to scan everything Kaizen returns.
    """
    results = []
    pw = None
    browser_to_close = None  # only set when we launched our own browser

    try:
        pw = await async_playwright().start()
        target_page = None

        # Try CDP first
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            for ctx in browser.contexts:
                for page in ctx.pages:
                    if "kaizenep.com" in page.url:
                        target_page = page
                        logger.info(f"CDP: reusing existing Kaizen page: {page.url}")
                        break
                if target_page:
                    break
            if not target_page:
                if browser.contexts:
                    target_page = await browser.contexts[0].new_page()
                else:
                    ctx = await browser.new_context()
                    target_page = await ctx.new_page()
        except Exception as e:
            logger.info(f"CDP unavailable ({e}) — launching headless Chromium")
            browser_to_close = await pw.chromium.launch(headless=True)
            ctx = await browser_to_close.new_context()
            target_page = await ctx.new_page()

        # Navigate to activities
        await target_page.goto("https://kaizenep.com/activities", wait_until="domcontentloaded")
        await asyncio.sleep(4)

        # Check if login needed (true for fresh headless browser, false for CDP with existing session)
        if "auth." in target_page.url or "login" in target_page.url.lower() or "eportfolio.rcem.ac.uk" in target_page.url:
            if not username or not password:
                logger.error("Login required but no credentials provided")
                return []
            logger.info("Logging in to Kaizen via RCEM…")
            if not await _login_via_rcem(target_page, username, password):
                return []
            await target_page.goto("https://kaizenep.com/activities", wait_until="domcontentloaded")
            await asyncio.sleep(3)

        # Get page content
        body_text = await target_page.inner_text("body")

        # Extract ticket data from table rows
        selectors = ["tr", ".activity-item", ".assessment-row", "[class*='activity']", "[class*='assessment']"]

        for selector in selectors:
            try:
                rows = await target_page.locator(selector).all()
                for row in rows[:30]:
                    try:
                        text = await row.inner_text()
                        if "awaiting response from" not in text.lower():
                            continue

                        lines = [l.strip() for l in text.split('\n') if l.strip()]

                        # Extract assessor name
                        assessor_match = re.search(r'Awaiting response from\s+([^\n]+)', text, re.IGNORECASE)
                        assessor = assessor_match.group(1).strip() if assessor_match else None

                        # Extract ticket type
                        ticket_type = None
                        for t in ['DOPS', 'Mini-CEX', 'LAT', 'ESLE', 'CBD', 'ACAT', 'MSF', 'QIAT',
                                   'ACAF', 'STAT', 'PROC_LOG', 'SDL', 'TEACH', 'JCF']:
                            if t in text:
                                ticket_type = t
                                break

                        # Extract date
                        date_match = re.search(r'(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})', text)
                        event_date = None
                        if date_match:
                            parsed = _parse_date(date_match.group(1))
                            if parsed:
                                event_date = parsed.strftime("%Y-%m-%d")

                        # Extract title
                        title = None
                        for line in lines[:5]:
                            if any(skip in line.lower() for skip in ['awaiting', 'assessor', 'response', 'view', 'remind']):
                                continue
                            if ticket_type and ticket_type in line:
                                title = line.strip()
                                break
                            if len(line) > 5 and not title:
                                title = line.strip()

                        ticket = {
                            "type": ticket_type,
                            "title": title,
                            "assessor_name": assessor,
                            "event_date": event_date,
                            "status": "awaiting_assessor",
                            "is_unsigned": True,
                        }

                        # Filter by date range when supplied
                        if event_date:
                            ticket_dt = datetime.strptime(event_date, "%Y-%m-%d")
                            if from_date and ticket_dt < from_date:
                                continue
                            if to_date and ticket_dt > to_date:
                                continue
                            results.append(ticket)
                        elif not from_date and not to_date:
                            # Undated ticket: include only when no filter
                            results.append(ticket)
                    except Exception:
                        continue
            except Exception:
                continue

        logger.info(f"Scraped {len(results)} unsigned tickets")

    except Exception as e:
        logger.error(f"Unsigned scraper error: {e}")
    finally:
        if browser_to_close:
            try:
                await browser_to_close.close()
            except Exception:
                pass
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass

    return results
