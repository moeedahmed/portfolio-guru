"""
kaizen_list_drafts.py — READ-ONLY draft inspector.

Lists all saved drafts in Kaizen with:
  - Title / form type
  - Date created
  - URL
  - Whether it's a draft (not submitted)

Does NOT delete or modify anything.

Usage:
    python3 kaizen_list_drafts.py

Output: JSON list of all drafts to stdout, plus a human-readable summary.
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from playwright.async_api import async_playwright


KAIZEN_URL = "https://kaizenep.com"


async def list_all_drafts(username: str, password: str) -> list:
    """
    Log in to Kaizen and return all saved drafts as a list of dicts.
    Read-only — no modifications.
    """
    drafts = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        # Login via RCEM portal (two-step: username then password)
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
        else:
            await page.fill('input[name="password"]', password)
            await page.click('button[type="submit"]')

        await page.wait_for_url("**/kaizenep.com/**", timeout=30000)
        await asyncio.sleep(3)

        if "kaizenep.com" not in page.url:
            print("❌ Login failed — check credentials", file=sys.stderr)
            await browser.close()
            return []

        print(f"✅ Logged in — at {page.url}", file=sys.stderr)

        # Navigate to activities
        await page.goto(f"{KAIZEN_URL}/activities", wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(5)

        # Screenshot for verification
        await page.screenshot(path="/tmp/kaizen_activities.png")
        print("📸 Screenshot saved to /tmp/kaizen_activities.png", file=sys.stderr)

        # Try to expand Saved drafts section if collapsed
        try:
            drafts_header = page.locator("text=Saved drafts").first
            if await drafts_header.is_visible(timeout=3000):
                print("Found 'Saved drafts' section — expanding...", file=sys.stderr)
                await drafts_header.click()
                await asyncio.sleep(2)
        except Exception:
            print("No collapsible drafts header found — may already be expanded", file=sys.stderr)

        # Screenshot after expanding
        await page.screenshot(path="/tmp/kaizen_drafts_expanded.png")
        print("📸 Post-expand screenshot saved to /tmp/kaizen_drafts_expanded.png", file=sys.stderr)

        # Collect all visible text + links on the page to understand structure
        page_text = await page.inner_text("body")
        
        # Look for draft links — Kaizen renders drafts as anchor tags
        all_links = await page.locator("a").all()
        print(f"Total links on page: {len(all_links)}", file=sys.stderr)

        for link in all_links:
            try:
                href = await link.get_attribute("href") or ""
                text = (await link.inner_text()).strip()

                # Kaizen draft links go to /events/edit/ or /activities/
                if not text or len(text) < 2:
                    continue
                if not any(kw in href for kw in ["/events/", "/activities/", "/edit/"]):
                    continue

                drafts.append({
                    "title": text,
                    "href": href,
                    "url": f"{KAIZEN_URL}{href}" if href.startswith("/") else href,
                })
                print(f"  Draft candidate: {text!r} → {href}", file=sys.stderr)
            except Exception:
                continue

        # Also dump a section of page text around "Saved drafts"
        if "Saved drafts" in page_text or "saved draft" in page_text.lower():
            idx = page_text.lower().find("saved draft")
            print(f"\nPage text around 'Saved drafts':\n{page_text[max(0,idx-50):idx+500]}", file=sys.stderr)
        else:
            print("\n⚠️  'Saved drafts' text not found in page body", file=sys.stderr)
            print("First 1000 chars of page:", page_text[:1000], file=sys.stderr)

        await browser.close()

    return drafts


async def main():
    # Get credentials from BWS
    import subprocess

    def get_bws_secret(secret_id: str) -> str:
        bws_token = open(os.path.expanduser("~/.openclaw/.bws-token")).read().strip()
        result = subprocess.run(
            [os.path.expanduser("~/.cargo/bin/bws"), "secret", "get", secret_id, "--output", "json"],
            env={**os.environ, "BWS_ACCESS_TOKEN": bws_token},
            capture_output=True, text=True
        )
        return json.loads(result.stdout)["value"]

    print("Fetching credentials from BWS...", file=sys.stderr)
    username = get_bws_secret("6e14d32b-6fff-480d-87b0-b3f300ee30f6")
    password = get_bws_secret("f311d41a-fa77-44f8-be42-b3f300ee3e08")

    print("Listing all Kaizen drafts (read-only)...\n", file=sys.stderr)
    drafts = await list_all_drafts(username, password)

    print(f"\n{'='*50}", file=sys.stderr)
    print(f"TOTAL DRAFT CANDIDATES FOUND: {len(drafts)}", file=sys.stderr)
    print(f"{'='*50}\n", file=sys.stderr)

    for i, d in enumerate(drafts, 1):
        print(f"{i:3}. {d['title']!r}", file=sys.stderr)

    # JSON to stdout for piping
    print(json.dumps(drafts, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
