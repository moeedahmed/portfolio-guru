"""
kaizen_delete_draft.py — Delete named Kaizen drafts by explicit document id.

Deletion is always targeted: you name the exact doc ids and a content marker
string that must appear on each draft's own page. If the marker is missing the
draft is left untouched. There is no "delete the top N drafts" mode — the saved
drafts list on /activities pages at five rows, so row position is not a stable
identity and deleting by count silently walks into real clinical evidence.

Usage:
    # See what would happen — no clicks, no deletions
    python3 kaizen_delete_draft.py --doc-id 123456 --expect-marker "AI declaration test" --dry-run

    # Actually delete (several ids allowed)
    python3 kaizen_delete_draft.py --doc-id 123456 --doc-id 123457 --expect-marker "AI declaration test"

Output: JSON summary to stdout, progress to stderr. Exit 1 if any id failed.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys

from playwright.async_api import async_playwright

KAIZEN_URL = "https://kaizenep.com"
DRAFT_VIEW_URL = KAIZEN_URL + "/events/view-section/{doc_id}"

SHARED_DEVICE_DISMISS = "a:has-text('This is a shared device')"
DELETE_LINK = "a.text-danger:has-text('Delete')"
CONFIRM_DIALOG = ".sweet-alert.visible"
CONFIRM_BUTTON = ".sweet-alert.visible button.confirm"
CONFIRM_TEXT = "remove this event"


def get_bws_secret(secret_id: str) -> str:
    bws_token = open(os.path.expanduser("~/.openclaw/.bws-token")).read().strip()
    result = subprocess.run(
        [os.path.expanduser("~/.cargo/bin/bws"), "secret", "get", secret_id, "--output", "json"],
        env={**os.environ, "BWS_ACCESS_TOKEN": bws_token},
        capture_output=True, text=True
    )
    return json.loads(result.stdout)["value"]


async def login(page, username: str, password: str) -> bool:
    """Two-step RCEM portal login. Returns True once landed on kaizenep.com."""
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


async def _dismiss_shared_device_banner(page) -> None:
    """Kaizen interposes a shared-device interstitial that hides the page body."""
    try:
        banner = page.locator(SHARED_DEVICE_DISMISS).first
        if await banner.count() > 0 and await banner.is_visible(timeout=3000):
            await banner.click()
            await asyncio.sleep(2)
    except Exception:
        pass


async def _open_draft(page, doc_id: str) -> str:
    """Navigate to a draft's own page and return its body text."""
    await page.goto(DRAFT_VIEW_URL.format(doc_id=doc_id), wait_until="domcontentloaded", timeout=40000)
    await asyncio.sleep(3)
    await _dismiss_shared_device_banner(page)
    return await page.evaluate("() => document.body.innerText")


async def delete_draft(page, doc_id: str, expect_marker: str, dry_run: bool) -> dict:
    """
    Delete one draft, identified by doc id and guarded by a content marker.

    Refuses to click Delete unless `expect_marker` appears in the body text of
    that doc id's own page. After deleting, reloads the doc id and confirms the
    marker is gone — never infers success from the drafts list row count, which
    refills from older drafts as rows are removed.
    """
    result = {"doc_id": doc_id, "deleted": False, "status": None, "error": None}

    body = await _open_draft(page, doc_id)
    if expect_marker not in body:
        result["status"] = "skipped-marker-absent"
        result["error"] = f"marker {expect_marker!r} not found on doc {doc_id} — refusing to delete"
        return result

    if dry_run:
        result["status"] = "would-delete"
        return result

    delete_link = page.locator(DELETE_LINK).first
    if await delete_link.count() == 0:
        result["status"] = "no-delete-control"
        result["error"] = f"no Delete link on doc {doc_id}"
        return result
    await delete_link.click()

    # Kaizen confirms with a SweetAlert, not a Bootstrap modal.
    await page.wait_for_selector(CONFIRM_DIALOG, timeout=10000)
    dialog_text = await page.locator(CONFIRM_DIALOG).first.inner_text()
    if CONFIRM_TEXT not in dialog_text.lower():
        result["status"] = "unexpected-confirm-dialog"
        result["error"] = f"confirm dialog did not mention {CONFIRM_TEXT!r}: {dialog_text[:120]!r}"
        return result
    await page.locator(CONFIRM_BUTTON).first.click()
    await asyncio.sleep(3)

    # Verify by doc id, not by row count.
    body_after = await _open_draft(page, doc_id)
    if expect_marker in body_after:
        result["status"] = "delete-not-verified"
        result["error"] = f"marker still present on doc {doc_id} after delete"
        return result

    result["deleted"] = True
    result["status"] = "deleted"
    return result


async def run(username: str, password: str, doc_ids: list, expect_marker: str, dry_run: bool) -> dict:
    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            if not await login(page, username, password):
                return {"deleted": 0, "results": [], "error": "Login failed"}
            print("✅ Logged in", file=sys.stderr)

            for doc_id in doc_ids:
                try:
                    r = await delete_draft(page, doc_id, expect_marker, dry_run)
                except Exception as e:
                    r = {"doc_id": doc_id, "deleted": False, "status": "error", "error": str(e)}
                results.append(r)
                print(f"  {doc_id}: {r['status']}" + (f" — {r['error']}" if r["error"] else ""), file=sys.stderr)
        finally:
            await browser.close()

    return {
        "dry_run": dry_run,
        "deleted": sum(1 for r in results if r["deleted"]),
        "results": results,
        "error": None,
    }


async def main():
    parser = argparse.ArgumentParser(description="Delete Kaizen drafts by explicit doc id.")
    parser.add_argument("--doc-id", action="append", required=True, dest="doc_ids",
                        help="Kaizen document id to delete. Repeat for several.")
    parser.add_argument("--expect-marker", required=True,
                        help="String that must appear on the draft's page before it will be deleted.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Check the marker and report, without clicking Delete.")
    args = parser.parse_args()

    username = get_bws_secret("6e14d32b-6fff-480d-87b0-b3f300ee30f6")
    password = get_bws_secret("f311d41a-fa77-44f8-be42-b3f300ee3e08")

    summary = await run(username, password, args.doc_ids, args.expect_marker, args.dry_run)
    print(json.dumps(summary, indent=2))

    failed = summary.get("error") or any(
        r["status"] not in ("deleted", "would-delete") for r in summary["results"]
    )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
