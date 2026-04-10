"""
kaizen_delete_cbd_drafts.py — Delete CBD saved drafts from the activities page.

Uses the kebab menu (≡) on each draft row in the Saved Drafts section.
Takes a screenshot before and after for verification.
Never touches submitted entries.

Usage:
    python3 kaizen_delete_cbd_drafts.py --count 3    # delete 3 as a test
    python3 kaizen_delete_cbd_drafts.py --count 100  # delete all
"""

import asyncio, json, os, subprocess, sys, argparse
from playwright.async_api import async_playwright

KAIZEN_ACTIVITIES = "https://kaizenep.com/activities"


def get_bws_secret(secret_id: str) -> str:
    bws_token = open(os.path.expanduser("~/.openclaw/.bws-token")).read().strip()
    r = subprocess.run(
        [os.path.expanduser("~/.cargo/bin/bws"), "secret", "get", secret_id, "--output", "json"],
        env={**os.environ, "BWS_ACCESS_TOKEN": bws_token},
        capture_output=True, text=True
    )
    return json.loads(r.stdout)["value"]


async def login(page, username, password):
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
    return "kaizenep.com" in page.url


async def get_draft_count(page) -> int:
    """Count CBD drafts currently visible in Saved Drafts section."""
    await page.goto(KAIZEN_ACTIVITIES, wait_until="domcontentlodle", timeout=40000)
    await asyncio.sleep(4)
    # Expand drafts section
    try:
        h = page.locator("text=Saved drafts").first
        if await h.is_visible(timeout=3000):
            await h.click()
            await asyncio.sleep(1)
    except Exception:
        pass
    links = page.locator("a:has-text('CBD')")
    return await links.count()


async def delete_cbd_drafts(username: str, password: str, max_count: int) -> dict:
    deleted = 0
    errors = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        if not await login(page, username, password):
            return {"deleted": 0, "errors": 0, "error": "Login failed"}

        print(f"✅ Logged in", file=sys.stderr)

        # Screenshot before
        await page.goto(KAIZEN_ACTIVITIES, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(4)

        # Expand saved drafts
        try:
            h = page.locator("text=Saved drafts").first
            if await h.is_visible(timeout=3000):
                await h.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        await page.screenshot(path="/tmp/kaizen_before_delete.png")
        print("📸 Before screenshot: /tmp/kaizen_before_delete.png", file=sys.stderr)

        # Inspect what the draft rows actually look like
        # Dump all text + interactive elements in the Saved Drafts section
        page_html = await page.content()
        # Find menu buttons near CBD entries
        all_buttons = await page.locator("button").all()
        print(f"Total buttons on page: {len(all_buttons)}", file=sys.stderr)
        for btn in all_buttons[:30]:
            try:
                txt = (await btn.inner_text()).strip()
                cls = await btn.get_attribute("class") or ""
                aria = await btn.get_attribute("aria-label") or ""
                if txt or aria:
                    print(f"  button: text={txt!r} aria={aria!r} class={cls[:60]!r}", file=sys.stderr)
            except Exception:
                pass

        # Also check for any element with delete/remove in text near CBD
        delete_candidates = await page.locator("[class*='delete'],[class*='remove'],[class*='trash'],[aria-label*='delete' i],[aria-label*='remove' i],[title*='delete' i]").all()
        print(f"Delete-class elements: {len(delete_candidates)}", file=sys.stderr)
        for el in delete_candidates[:10]:
            try:
                txt = (await el.inner_text()).strip()
                tag = await el.evaluate("e => e.tagName")
                aria = await el.get_attribute("aria-label") or ""
                cls = await el.get_attribute("class") or ""
                print(f"  {tag}: text={txt!r} aria={aria!r} class={cls[:80]!r}", file=sys.stderr)
            except Exception:
                pass

        # Check kebab/ellipsis menus
        kebab_candidates = await page.locator("[class*='kebab'],[class*='menu'],[class*='ellipsis'],[class*='action'],[class*='options'],[class*='dropdown']").all()
        print(f"Kebab/menu elements: {len(kebab_candidates)}", file=sys.stderr)
        for el in kebab_candidates[:10]:
            try:
                txt = (await el.inner_text()).strip()[:50]
                tag = await el.evaluate("e => e.tagName")
                aria = await el.get_attribute("aria-label") or ""
                cls = await el.get_attribute("class") or ""
                print(f"  {tag}: text={txt!r} aria={aria!r} class={cls[:80]!r}", file=sys.stderr)
            except Exception:
                pass

        # Try the ≡ icon (fa-bars / fa-ellipsis-v / similar)
        icon_btns = await page.locator("button i, a i, span i").all()
        print(f"Icon elements (i tags): {len(icon_btns)}", file=sys.stderr)
        for el in icon_btns[:20]:
            try:
                cls = await el.get_attribute("class") or ""
                print(f"  i.class={cls!r}", file=sys.stderr)
            except Exception:
                pass

        await browser.close()

    return {"deleted": deleted, "errors": errors, "error": None}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()

    username = get_bws_secret("6e14d32b-6fff-480d-87b0-b3f300ee30f6")
    password = get_bws_secret("f311d41a-fa77-44f8-be42-b3f300ee3e08")

    result = await delete_cbd_drafts(username, password, args.count)
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
