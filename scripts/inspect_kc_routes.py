"""
Read-only Kaizen KC curriculum route inspector.

Purpose: capture DOM evidence (kz-tree presence, SLO anchors, Add Tags button)
for each fileable form. Navigates to /events/new-section/{uuid} only — no saves,
no submissions, no credential logging.

Usage:
    cd /Users/moeedahmed/projects/portfolio-guru/backend
    BWS_ACCESS_TOKEN=$(cat ~/.openclaw/.bws-token) \\
    python3 ../scripts/inspect_kc_routes.py

Evidence is written to docs/kc_route_evidence_YYYYMMDD.json and .md.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

# Playwright import
try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERROR: playwright not installed in this environment", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─── Forms to inspect ────────────────────────────────────────────────────────
# Only forms that have (or might have) curriculum / key-capability fields.
# Priority forms per brief — skip pure admin/mgmt forms that are already verified tag-only.
FORMS_TO_INSPECT = [
    # Core WPBA forms — current routing disputed for DOPS
    ("CBD",             "3ce5989a-b61c-4c24-ab12-711bf928b181"),
    ("DOPS",            "159831f9-6d22-4e77-851b-87e30aee37a2"),
    ("DOPS_ACCS",       "fea13c0a-4027-410a-a8cd-f66f526cfde6"),
    ("MINI_CEX",        "647665f4-a992-4541-9e17-33ba6fd1d347"),
    ("ACAT",            "6577ab06-8340-47e3-952a-708a5f800dcc"),
    ("LAT",             "eb1c7547-0f41-49e7-95de-8adffd849924"),
    ("QIAT",            "a0aa5cfc-57be-4622-b974-51d334268d57"),
    ("STAT",            "41ff54b8-35a7-414b-9bd6-97fb1c3eb189"),
    ("JCF",             "3daa9559-3c31-4ab4-883c-9a991632a9ca"),
    ("TEACH",           "1ffbd272-8447-439c-aa03-ff99e2dbc04d"),
    ("PROC_LOG",        "2d6ebac1-4633-49d1-9dc0-fa0d39a98afc"),
    ("PROCEDURAL_LOG_ACCS", "d13ccff4-4aac-495c-ae2f-aca1056c5d15"),
    ("SDL",             "743885d8-c1b8-4566-bc09-8ed9b0e09829"),
    ("US_CASE",         "558b196a-8168-4cc6-b363-6f6e4b08397a"),
    ("ESLE_PART1_2",    "4a6f3a91-10ed-45d0-bb82-3e87ae2d6d04"),
    ("REFLECT_LOG",     "32d0fcb9-05d0-4d6d-b877-ebd5daf0b4e9"),
    ("TEACH_OBS",       "30668ad8-e1db-4a27-bb2d-3e395e6acfcf"),
    ("TEACH_CONFID",    "f614bdcc-5d31-4b5b-b980-1e073e2431db"),
    ("COMPLAINT",       "f7c0ba98-5a47-4e37-b76a-ca3c5c8484cc"),
    ("SERIOUS_INC",     "9d4a7912-a615-4ae4-9fae-6be966bcf254"),
    ("EDU_ACT",         "868dc0e7-f4e9-4283-ac52-d9c8b246024b"),
    ("FORMAL_COURSE",   "c7cd9a95-e2aa-4f61-a441-b663f3c933c6"),
    ("AUDIT",           "33c454df-eb86-49f1-8ec0-ee2ccbe8c574"),
    ("RESEARCH",        "3d4c6a82-f7ab-4b11-bb36-c7487de4ff2d"),
    ("PDP",             "c2b716dd-2d2a-462e-8df0-70760673448c"),
]

# ─── DOM probe script ─────────────────────────────────────────────────────────
# Runs in the page after Angular has settled. Returns a plain dict.
DOM_PROBE_JS = """() => {
    function countSel(sel) {
        try { return document.querySelectorAll(sel).length; } catch(e) { return -1; }
    }
    function hasText(text) {
        try {
            var els = document.querySelectorAll('button, a, [role="button"]');
            for (var i = 0; i < els.length; i++) {
                if ((els[i].textContent || '').trim().toLowerCase().indexOf(text.toLowerCase()) >= 0) return true;
            }
            return false;
        } catch(e) { return false; }
    }
    function hasNgClick(frag) {
        try {
            var els = document.querySelectorAll('[ng-click]');
            for (var i = 0; i < els.length; i++) {
                if ((els[i].getAttribute('ng-click') || '').indexOf(frag) >= 0) return true;
            }
            return false;
        } catch(e) { return false; }
    }

    // kz-tree elements in the form body (not in a dialog)
    var kzTreeElsAll = countSel('[kz-tree]');

    // SLO anchor links within a kz-tree (lazy-loaded children — need SLO expansion)
    var sloAnchorsInTree = countSel('[kz-tree] li a');
    var sloListItemsInTree = countSel('[kz-tree] li');

    // KC checkboxes already visible in the form body (pre-expansion)
    var kcCheckboxesInTree = countSel('[kz-tree] input[type="checkbox"]');

    // Add Tags button signals
    var addTagsNgClick = hasNgClick('addTags');
    var addTagsText = hasText('Add tags');

    // Curriculum tree in a modal dialog (tag modal)
    var kzTreeInDialog = countSel('[role="dialog"] [kz-tree]');
    var kzTreeDialogOpen = countSel('[role="dialog"]') > 0;

    // Stage of training select (present on forms that have it inline)
    var stageSelectCount = countSel('[id="e0864e88-62cf-43aa-a9e5-51abd98a1cce"]');

    // Any kz-tree-item (the Angular component class used in the tag modal tree)
    var kzTreeItemCount = countSel('kz-tree-item');
    var kzTreeItemAnchorCount = countSel('kz-tree-item a');

    // Specific SLO structural hints — first-level accordion items in kz-tree
    // When SLOs are expanded in the modal, they load child kz-tree-item nodes.
    var curriculumRootLinks = 0;
    try {
        var treeEls = document.querySelectorAll('[kz-tree]');
        for (var i = 0; i < treeEls.length; i++) {
            curriculumRootLinks += treeEls[i].querySelectorAll(':scope > ul > li > a').length;
        }
    } catch(e) {}

    return {
        kzTreeElsAll: kzTreeElsAll,
        sloAnchorsInTree: sloAnchorsInTree,
        sloListItemsInTree: sloListItemsInTree,
        kcCheckboxesInTree: kcCheckboxesInTree,
        addTagsNgClick: addTagsNgClick,
        addTagsText: addTagsText,
        kzTreeInDialog: kzTreeInDialog,
        kzTreeDialogOpen: kzTreeDialogOpen,
        stageSelectCount: stageSelectCount,
        kzTreeItemCount: kzTreeItemCount,
        kzTreeItemAnchorCount: kzTreeItemAnchorCount,
        curriculumRootLinks: curriculumRootLinks,
        pageUrl: window.location.href.split('?')[0],
    };
}"""


def _load_credentials_from_bws():
    """Load ACCS/Intermediate credentials from BWS. Never print/return raw values."""
    token = os.environ.get("BWS_ACCESS_TOKEN", "").strip()
    bws_bin = "/Users/moeedahmed/.cargo/bin/bws"
    if not token:
        raise RuntimeError("BWS_ACCESS_TOKEN not set in environment")

    def _get(secret_id):
        result = subprocess.run(
            [bws_bin, "secret", "get", secret_id, "--output", "json"],
            capture_output=True, text=True,
            env={**os.environ, "BWS_ACCESS_TOKEN": token},
        )
        if result.returncode != 0:
            raise RuntimeError(f"BWS fetch failed for {secret_id}: {result.stderr[:200]}")
        data = json.loads(result.stdout)
        return data["value"]

    username = _get("22119562-8d7f-4e21-964a-b44c017f7e9e")
    password = _get("766d43fd-bcb1-4739-a96c-b44c017feb27")
    return username, password


async def _login(page, username: str, password: str) -> bool:
    """Log in to Kaizen via RCEM portal (mirrors kaizen_form_filer._login)."""
    try:
        await page.goto("https://eportfolio.rcem.ac.uk", wait_until="load", timeout=30000)
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
        current = page.url
        logger.info("Login success, landed at: %s", current.split("?")[0])
        return True
    except Exception as exc:
        logger.error("Login failed: %s", exc)
        return False


async def _inspect_form(page, form_type: str, uuid: str) -> dict:
    """Navigate to the new-section URL and capture DOM signals. Read-only."""
    url = f"https://kaizenep.com/events/new-section/{uuid}"
    evidence = {
        "form_type": form_type,
        "uuid": uuid,
        "url_pattern": f"new-section/{uuid}",
        "status": "unknown",
        "error": None,
        "kzTreeElsAll": 0,
        "sloAnchorsInTree": 0,
        "sloListItemsInTree": 0,
        "kcCheckboxesInTree": 0,
        "addTagsNgClick": False,
        "addTagsText": False,
        "kzTreeInDialog": 0,
        "kzTreeDialogOpen": False,
        "stageSelectCount": 0,
        "kzTreeItemCount": 0,
        "kzTreeItemAnchorCount": 0,
        "curriculumRootLinks": 0,
    }
    try:
        await page.goto(url, wait_until="load", timeout=30000)
        await asyncio.sleep(3)

        landed = page.url
        if "login" in landed or "interaction" in landed or "auth.kaizenep.com" in landed:
            evidence["status"] = "auth_bounce"
            evidence["error"] = f"Redirected to auth: {landed[:80]}"
            return evidence

        if "new-section" not in landed and form_type not in landed and uuid not in landed:
            # May have redirected to list or error page
            if "kaizenep.com" in landed:
                evidence["status"] = "redirect_not_form"
                evidence["error"] = f"Landed at: {landed[:80]}"
            else:
                evidence["status"] = "unknown_redirect"
                evidence["error"] = f"Landed at: {landed[:80]}"
            return evidence

        # Wait a bit more for Angular to render
        await asyncio.sleep(2)
        result = await page.evaluate(DOM_PROBE_JS)
        evidence.update(result)
        evidence["status"] = "ok"
        logger.info(
            "[%s] kzTree=%d sloAnchors=%d addTagsBtn=%s kzTreeInDialog=%d",
            form_type,
            result.get("kzTreeElsAll", 0),
            result.get("sloAnchorsInTree", 0),
            result.get("addTagsText", False) or result.get("addTagsNgClick", False),
            result.get("kzTreeInDialog", 0),
        )
    except Exception as exc:
        evidence["status"] = "error"
        evidence["error"] = str(exc)[:200]
        logger.warning("[%s] Error: %s", form_type, exc)
    return evidence


def _classify_route(ev: dict) -> str:
    """Derive route classification from DOM evidence."""
    if ev["status"] != "ok":
        return "UNKNOWN (inspect failed)"

    has_inline_tree = ev["kzTreeElsAll"] > 0 and ev["sloListItemsInTree"] > 0
    has_add_tags = ev["addTagsNgClick"] or ev["addTagsText"]

    if has_inline_tree and not has_add_tags:
        return "INLINE_TREE_ONLY"
    if has_inline_tree and has_add_tags:
        return "INLINE_TREE_PRIMARY (Add Tags also present)"
    if not has_inline_tree and has_add_tags:
        return "ADD_TAGS_ONLY"
    if ev["kzTreeElsAll"] > 0 and ev["sloListItemsInTree"] == 0 and has_add_tags:
        # kz-tree attr present but no SLO nodes — like CBD was previously noted
        return "ADD_TAGS_ONLY (kz-tree attr present but empty)"
    if ev["kzTreeElsAll"] > 0 and ev["sloListItemsInTree"] == 0 and not has_add_tags:
        return "UNKNOWN (kz-tree empty, no Add Tags button visible)"
    return "NO_CURRICULUM_SURFACE"


async def main():
    username, password = _load_credentials_from_bws()

    evidence_list = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = await ctx.new_page()

        logged_in = await _login(page, username, password)
        if not logged_in:
            logger.error("Login failed — cannot proceed with inspection")
            await browser.close()
            sys.exit(1)

        for form_type, uuid in FORMS_TO_INSPECT:
            logger.info("Inspecting %s ...", form_type)
            ev = await _inspect_form(page, form_type, uuid)
            ev["route_classification"] = _classify_route(ev)
            evidence_list.append(ev)
            await asyncio.sleep(1)

        await browser.close()

    # ─── Write evidence artifact (no credentials) ─────────────────────────────
    today = date.today().isoformat().replace("-", "")
    repo_root = Path(__file__).parent.parent
    docs_dir = repo_root / "docs"
    docs_dir.mkdir(exist_ok=True)

    json_path = docs_dir / f"kc_route_evidence_{today}.json"
    md_path   = docs_dir / f"kc_route_evidence_{today}.md"

    with open(json_path, "w") as f:
        json.dump(evidence_list, f, indent=2)

    with open(md_path, "w") as f:
        f.write(f"# KC Route Evidence — {today}\n\n")
        f.write("Captured via read-only DOM inspection of Kaizen new-section pages.\n")
        f.write("No saves, submissions, or mutations performed.\n\n")
        f.write("## Route Classification Key\n\n")
        f.write("- **INLINE_TREE_ONLY** — `kz-tree` with SLO list items visible; no Add Tags button\n")
        f.write("- **INLINE_TREE_PRIMARY** — `kz-tree` with SLOs AND Add Tags button both present\n")
        f.write("- **ADD_TAGS_ONLY** — No usable in-form kz-tree SLOs; Add Tags button present\n")
        f.write("- **NO_CURRICULUM_SURFACE** — Neither kz-tree nor Add Tags detected\n")
        f.write("- **UNKNOWN** — Inspection failed or inconclusive\n\n")
        f.write("## Results\n\n")
        f.write("| Form | kzTree | sloListItems | kcCBs | AddTags(ng) | AddTags(txt) | Route Classification |\n")
        f.write("|------|--------|-------------|-------|-------------|--------------|----------------------|\n")
        for ev in evidence_list:
            f.write(
                f"| {ev['form_type']} "
                f"| {ev.get('kzTreeElsAll', '?')} "
                f"| {ev.get('sloListItemsInTree', '?')} "
                f"| {ev.get('kcCheckboxesInTree', '?')} "
                f"| {ev.get('addTagsNgClick', '?')} "
                f"| {ev.get('addTagsText', '?')} "
                f"| {ev.get('route_classification', '?')} |\n"
            )
        f.write("\n## Raw Evidence\n\n")
        for ev in evidence_list:
            f.write(f"### {ev['form_type']}\n\n")
            f.write(f"- status: {ev['status']}\n")
            f.write(f"- url_pattern: {ev['url_pattern']}\n")
            for k, v in ev.items():
                if k not in ("form_type", "uuid", "url_pattern", "status", "route_classification", "pageUrl"):
                    f.write(f"- {k}: {v}\n")
            f.write(f"- **route_classification: {ev.get('route_classification','?')}**\n\n")

    print(f"\nEvidence written to:\n  {json_path}\n  {md_path}\n")
    print("\nSummary:")
    for ev in evidence_list:
        print(f"  {ev['form_type']:30s}  {ev.get('route_classification', 'UNKNOWN')}")


if __name__ == "__main__":
    asyncio.run(main())
