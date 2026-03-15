"""
Kaizen Unsigned Tickets Scraper — async version for Portfolio Guru.
Ported from Medic's kaizen_scraper_structured.py.
Uses CDP connection to managed browser at localhost:18800.
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
CUTOFF_DATE = datetime(2025, 1, 1)


def _parse_date(date_str: str) -> datetime | None:
    formats = ["%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


async def scrape_unsigned_tickets(username: str = "", password: str = "") -> list[dict]:
    """
    Scrape unsigned tickets from Kaizen activities page.
    Returns structured list of unsigned tickets after CUTOFF_DATE.
    """
    results = []
    pw = None

    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(CDP_URL)

        # Find existing Kaizen page or create new one
        target_page = None
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

        # Navigate to activities
        await target_page.goto("https://kaizenep.com/activities", wait_until="domcontentloaded")
        await asyncio.sleep(4)

        # Check if login needed
        if "auth." in target_page.url or "login" in target_page.url.lower():
            if not username or not password:
                logger.error("Login required but no credentials provided")
                return []

            logger.info("Logging in to Kaizen...")
            try:
                await target_page.fill('input[type="email"], input[name="login"]', username)
                await target_page.keyboard.press("Enter")
                await asyncio.sleep(2)
                await target_page.fill('input[type="password"]', password)
                await target_page.keyboard.press("Enter")
                await asyncio.sleep(4)

                # Handle org selector
                rcem = target_page.locator('text=Royal College of Emergency Medicine').first
                if await rcem.is_visible():
                    await rcem.click()
                    await asyncio.sleep(1)
                    await target_page.click('button[type="submit"]')
                    await asyncio.sleep(3)

                await target_page.goto("https://kaizenep.com/activities", wait_until="domcontentloaded")
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"Login error: {e}")
                return []

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

                        # Filter by cutoff date
                        if event_date:
                            ticket_dt = datetime.strptime(event_date, "%Y-%m-%d")
                            if ticket_dt >= CUTOFF_DATE:
                                results.append(ticket)
                    except Exception:
                        continue
            except Exception:
                continue

        logger.info(f"Scraped {len(results)} unsigned tickets")

    except Exception as e:
        logger.error(f"Unsigned scraper error: {e}")
    finally:
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass

    return results
