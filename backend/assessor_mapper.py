"""
Read-only Kaizen assessor workflow mapper.

This module is deliberately limited to navigation and extraction. It must not
click submit/sign/save/delete controls or create drafts. Assessor actions affect
another doctor's portfolio, so write-side automation belongs behind a separate
explicit approval gate after the DOM has been mapped and tested.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from playwright.async_api import Page, async_playwright

from kaizen_unsigned_scraper import _login_via_rcem

logger = logging.getLogger(__name__)

CDP_URL = os.environ.get("KAIZEN_CDP_URL", "http://localhost:18800")
ASSESSMENTS_URL = "https://kaizenep.com/events/list/Assessments"

# Texts that may exist on assessor pages but must never be clicked by this
# mapper. Tests assert these stay deny-listed.
WRITE_ACTION_LABELS = (
    "approve",
    "delete",
    "save",
    "send",
    "sign",
    "submit",
)


@dataclass
class AssessorTicketSummary:
    title: str | None = None
    href: str | None = None
    uuid: str | None = None
    state: str | None = None
    section_view: bool | None = None


@dataclass
class AssessorTicketDetail:
    summary: AssessorTicketSummary
    event_type: str | None = None
    state: str | None = None
    filled_in_by: str | None = None
    fields: list[dict[str, str | None]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    available_buttons: list[str] = field(default_factory=list)
    url: str | None = None


def _event_uuid_from_href(href: str | None) -> tuple[str | None, bool | None]:
    if not href:
        return None, None
    match = re.search(r"/events/(view|view-section)/([0-9a-f-]+)", href, re.I)
    if not match:
        return None, None
    return match.group(2), match.group(1).lower() == "view-section"


def _normalise_summary(row: dict[str, Any]) -> AssessorTicketSummary:
    uuid, section_view = _event_uuid_from_href(row.get("href"))
    return AssessorTicketSummary(
        title=row.get("title"),
        href=row.get("href"),
        uuid=uuid,
        state=row.get("state"),
        section_view=section_view,
    )


async def _ensure_logged_in(page: Page, username: str = "", password: str = "") -> None:
    await page.goto(ASSESSMENTS_URL, wait_until="domcontentloaded")
    await asyncio.sleep(3)
    if "auth.kaizenep.com" in page.url or "eportfolio.rcem.ac.uk" in page.url:
        if not username or not password:
            raise RuntimeError("Kaizen login required but no credentials were supplied")
        ok = await _login_via_rcem(page, username, password)
        if not ok:
            raise RuntimeError("Kaizen login failed")
        await page.goto(ASSESSMENTS_URL, wait_until="domcontentloaded")
        await asyncio.sleep(3)


async def extract_assessment_rows(page: Page, limit: int = 20) -> list[AssessorTicketSummary]:
    """Extract visible assessment timeline rows without changing state."""
    rows = await page.evaluate(
        """(limit) => {
          const text = el => (el && el.textContent ? el.textContent.trim().replace(/\\s+/g, ' ') : null);
          return Array.from(document.querySelectorAll('.row.event-inner')).slice(0, limit).map(r => {
            const a = r.querySelector('a[router-link], a[href*="/events/"]');
            const titleEl = r.querySelector('h2.entry-title, .entry-title');
            const stateEl = r.querySelector('.event-section-progress-state');
            return {
              title: text(titleEl || a),
              href: a ? a.href : null,
              state: text(stateEl)
            };
          }).filter(r => r.title || r.href);
        }""",
        limit,
    )
    return [_normalise_summary(row) for row in rows]


async def extract_assessment_detail(page: Page, summary: AssessorTicketSummary) -> AssessorTicketDetail:
    """Open and read one assessor ticket detail page without clicking controls."""
    if not summary.href:
        return AssessorTicketDetail(summary=summary)
    await page.goto(summary.href, wait_until="domcontentloaded")
    await asyncio.sleep(3)
    payload = await page.evaluate(
        """() => {
          const text = el => (el && el.textContent ? el.textContent.trim().replace(/\\s+/g, ' ') : null);
          const h1 = document.querySelector('h1');
          const fieldGroups = Array.from(document.querySelectorAll('.form-text__form-group, .form-readonly__form-group'));
          const fields = fieldGroups.map(g => ({
            label: text(g.querySelector('.form-text__control-label, .control-label')),
            value: text(g.querySelector('.form-text__field-value, .field-value, dd'))
          })).filter(f => f.label);
          const tags = Array.from(document.querySelectorAll('.event-tag')).map(text).filter(Boolean);
          const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], a.btn'))
            .map(text)
            .filter(Boolean);
          return {
            event_type: text(h1),
            state: text(document.querySelector('.event-section-progress-state')),
            filled_in_by: text(document.querySelector('.event-users')),
            fields,
            tags,
            available_buttons: buttons,
            url: location.href
          };
        }"""
    )
    return AssessorTicketDetail(summary=summary, **payload)


async def map_assessor_tickets(
    username: str = "",
    password: str = "",
    *,
    limit: int = 10,
    include_details: bool = False,
) -> list[AssessorTicketDetail | AssessorTicketSummary]:
    """Map visible assessor tickets using read-only browser navigation."""
    pw = await async_playwright().start()
    browser_to_close = None
    try:
        page = None
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
        except Exception:
            browser_to_close = await pw.chromium.launch(headless=True)
            context = await browser_to_close.new_context()
            page = await context.new_page()

        await _ensure_logged_in(page, username=username, password=password)
        summaries = await extract_assessment_rows(page, limit=limit)
        if not include_details:
            return summaries
        details = []
        for summary in summaries:
            details.append(await extract_assessment_detail(page, summary))
        return details
    finally:
        if browser_to_close:
            await browser_to_close.close()
        await pw.stop()


async def _amain() -> None:
    parser = argparse.ArgumentParser(description="Read-only Kaizen assessor ticket mapper")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()
    result = await map_assessor_tickets(
        username=os.environ.get("KAIZEN_USERNAME", ""),
        password=os.environ.get("KAIZEN_PASSWORD", ""),
        limit=args.limit,
        include_details=args.details,
    )
    print(json.dumps([asdict(item) for item in result], indent=2))


if __name__ == "__main__":
    asyncio.run(_amain())
