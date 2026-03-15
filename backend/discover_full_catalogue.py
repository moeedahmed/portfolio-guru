#!/usr/bin/env python3
"""
Discover ALL Kaizen form UUIDs via CDP browser.
Extracts eventType._id from Angular scope for every form on /events/new.
"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

CDP_URL = "http://localhost:18800"


async def discover_all_forms():
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(CDP_URL)

    page = None
    for ctx in browser.contexts:
        for p in ctx.pages:
            if "kaizenep.com" in p.url:
                page = p
                break
        if page:
            break

    if not page:
        if browser.contexts:
            page = await browser.contexts[0].new_page()
        else:
            ctx = await browser.new_context()
            page = await ctx.new_page()

    await page.goto("https://kaizenep.com/events/new", wait_until="networkidle", timeout=30000)
    await asyncio.sleep(3)

    # Extract all eventType entries from Angular scope
    forms = await page.evaluate("""() => {
        const results = [];

        // Find all links with ng-click containing getRouteForEventType
        const links = document.querySelectorAll('a[ng-click*="getRouteForEventType"]');
        for (const a of links) {
            try {
                const scope = angular.element(a).scope();
                if (scope && scope.eventType) {
                    const et = scope.eventType;
                    results.push({
                        name: et.name || a.innerText.trim(),
                        id: et._id,
                        group: scope.group ? scope.group.name : null,
                    });
                }
            } catch(e) {}
        }

        return results;
    }""")

    catalogue = {}
    for form in forms:
        name = form['name'].strip()
        uuid = form['id']
        group = form.get('group', '')
        if name and uuid:
            # De-duplicate (some appear in multiple groups)
            if name not in catalogue:
                catalogue[name] = uuid
                print(f"  [{group}] {name}: {uuid}")

    # Save
    output_path = Path(__file__).parent / "kaizen_full_catalogue.json"
    with open(output_path, "w") as f:
        json.dump(catalogue, f, indent=2, ensure_ascii=False)

    print(f"\nTOTAL: {len(catalogue)} unique forms")
    print(f"Saved to {output_path}")

    await pw.stop()


if __name__ == "__main__":
    asyncio.run(discover_all_forms())
