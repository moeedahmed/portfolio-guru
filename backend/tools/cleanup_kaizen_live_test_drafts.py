"""Safely clean up Kaizen live integration-test drafts.

This helper is intentionally narrow: it may delete only drafts recorded in a
specific live-test manifest, and only after the opened draft still contains the
same run token marker. It must not delete by date, form type, or generic text.

Usage:
    KAIZEN_CLEANUP_CONFIRM=1 python backend/tools/cleanup_kaizen_live_test_drafts.py /tmp/kaizen-live-test-<token>.json
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright


def _load_manifest(path: Path) -> list[dict]:
    records = json.loads(path.read_text())
    if not isinstance(records, list) or not records:
        raise SystemExit("Manifest must be a non-empty JSON list")
    tokens = {r.get("run_token") for r in records}
    if len(tokens) != 1 or not next(iter(tokens)):
        raise SystemExit("Manifest must contain exactly one non-empty run_token")
    for record in records:
        if not record.get("event_id") or not record.get("required_marker"):
            raise SystemExit(f"Unsafe manifest record: {record}")
    return records


async def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: cleanup_kaizen_live_test_drafts.py <manifest.json>")
    if os.environ.get("KAIZEN_CLEANUP_CONFIRM") != "1":
        raise SystemExit("Refusing to delete without KAIZEN_CLEANUP_CONFIRM=1")

    manifest = _load_manifest(Path(sys.argv[1]))
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp("http://localhost:18800")
        context = browser.contexts[0]
        deleted: list[str] = []
        for record in manifest:
            event_id = record["event_id"]
            marker = record["required_marker"]
            page = await context.new_page()
            await page.goto(f"https://kaizenep.com/events/view-section/{event_id}", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1200)
            body = await page.inner_text("body")
            if marker not in body or "DRAFT PRIVATE" not in body:
                await page.close()
                raise SystemExit(f"Refusing to delete {event_id}: exact marker/private-draft check failed")
            await page.locator("a[title='Delete']").first.click()
            await page.wait_for_timeout(800)
            ok = page.locator('button.confirm:has-text("OK")').first
            if not await ok.is_visible(timeout=5000):
                await page.close()
                raise SystemExit(f"Refusing to continue: confirmation button not visible for {event_id}")
            await ok.click()
            await page.wait_for_timeout(3000)
            deleted.append(event_id)
            await page.close()
        await browser.close()
    print(json.dumps({"deleted_event_ids": deleted}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
