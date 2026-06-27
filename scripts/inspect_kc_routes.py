"""
Read-only Kaizen KC curriculum route inspector.

Purpose: capture DOM evidence (kz-tree presence, SLO anchors, Add Tags button)
for each fileable form. Navigates to /events/new-section/{uuid} only — no saves,
no submissions, no credential logging.

Now profile-aware (ACCS, Intermediate, HST) and stage-aware (before vs after selecting stage dropdown).

Usage:
    cd /Users/moeedahmed/projects/portfolio-guru/backend
    BWS_ACCESS_TOKEN=$(cat ~/.openclaw/.bws-token) \\
    python3 ../scripts/inspect_kc_routes.py
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
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

STAGE_SELECT_VALUES = {
    "ACCS":         "string:39b9fe64-b1e7-4726-81e2-73aaead0ee95",
    "Intermediate": "string:0669c338-e695-40f9-8fae-aee2ee7d68e1",
    "Higher":       "string:3815019a-e2be-4824-a4fb-555b55ffeab2",
    "PEM":          "string:fc7caa86-b83c-48d0-9b86-0fb73617d2b5",
}

# Forms to inspect
FORMS_TO_INSPECT = [
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

    var kzTreeElsAll = countSel('[kz-tree]');
    var sloAnchorsInTree = countSel('[kz-tree] li a');
    var sloListItemsInTree = countSel('[kz-tree] li');
    var kcCheckboxesInTree = countSel('[kz-tree] input[type="checkbox"]');
    var addTagsNgClick = hasNgClick('addTags');
    var addTagsText = hasText('Add tags');
    var kzTreeInDialog = countSel('[role="dialog"] [kz-tree]');
    var kzTreeDialogOpen = countSel('[role="dialog"]') > 0;
    var stageSelectCount = countSel('[id="e0864e88-62cf-43aa-a9e5-51abd98a1cce"]');
    var kzTreeItemCount = countSel('kz-tree-item');
    var kzTreeItemAnchorCount = countSel('kz-tree-item a');

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

def _profile_secret_config():
    """Read profile credential secret IDs from environment, never source code."""
    return {
        "ACCS": {
            "username": os.environ.get("KAIZEN_ACCS_USERNAME_SECRET_ID", "").strip(),
            "password": os.environ.get("KAIZEN_ACCS_PASSWORD_SECRET_ID", "").strip(),
        },
        "Intermediate": {
            "username": os.environ.get("KAIZEN_INTERMEDIATE_USERNAME_SECRET_ID", "").strip()
            or os.environ.get("KAIZEN_ACCS_USERNAME_SECRET_ID", "").strip(),
            "password": os.environ.get("KAIZEN_INTERMEDIATE_PASSWORD_SECRET_ID", "").strip()
            or os.environ.get("KAIZEN_ACCS_PASSWORD_SECRET_ID", "").strip(),
        },
        "HST": {
            "username": os.environ.get("KAIZEN_HST_USERNAME_SECRET_ID", "").strip(),
            "password": os.environ.get("KAIZEN_HST_PASSWORD_SECRET_ID", "").strip(),
        },
    }


def _load_all_credentials_from_bws():
    """Load ACCS/Intermediate and HST credentials from BWS. Never print/return raw values."""
    token = os.environ.get("BWS_ACCESS_TOKEN", "").strip()
    bws_bin = "/Users/moeedahmed/.cargo/bin/bws"
    if not token:
        try:
            bws_token_path = os.path.expanduser("~/.openclaw/.bws-token")
            if os.path.exists(bws_token_path):
                with open(bws_token_path) as f:
                    token = f.read().strip()
        except Exception:
            pass

    if not token:
        raise RuntimeError("BWS_ACCESS_TOKEN not set in environment or ~/.openclaw/.bws-token")

    def _get(secret_id):
        result = subprocess.run(
            [bws_bin, "secret", "get", secret_id, "--output", "json"],
            capture_output=True, text=True,
            env={**os.environ, "BWS_ACCESS_TOKEN": token},
        )
        if result.returncode != 0:
            raise RuntimeError("BWS fetch failed")
        data = json.loads(result.stdout)
        return data["value"]

    creds = {}
    for profile, secret_ids in _profile_secret_config().items():
        username_id = secret_ids["username"]
        password_id = secret_ids["password"]
        if not username_id or not password_id:
            logger.warning("Credential secret IDs not configured for profile %s", profile)
            continue
        try:
            creds[profile] = (_get(username_id), _get(password_id))
        except Exception:
            logger.warning("Could not load credentials for profile %s", profile)

    return creds

async def _login(page, username: str, password: str) -> bool:
    """Log in to Kaizen via RCEM portal."""
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

async def _select_stage_option(page, value: str) -> bool:
    try:
        dropdown = page.locator('select[id="e0864e88-62cf-43aa-a9e5-51abd98a1cce"]').first
        if await dropdown.count() > 0:
            await dropdown.select_option(value=value)
            return True
        # Try generic stage selector
        dropdown = page.locator('select[ng-model*="stage"], select[ng-model*="Stage"]').first
        if await dropdown.count() > 0:
            await dropdown.select_option(value=value)
            return True
    except Exception as e:
        logger.warning("Failed to select option %s: %s", value, e)
    return False

async def _inspect_form_profile_aware(page, form_type: str, uuid: str, profile_name: str, stage_name: str, stage_val: str) -> dict:
    """Navigate to the new-section URL and capture DOM signals. Read-only."""
    url = f"https://kaizenep.com/events/new-section/{uuid}"
    evidence = {
        "profile": profile_name,
        "form_type": form_type,
        "uuid": uuid,
        "url_pattern": f"new-section/{uuid}",
        "status": "unknown",
        "error": None,
        "selected_stage": stage_name,
        "before_kzTreeCount": 0,
        "before_sloListItems": 0,
        "before_kcCheckboxes": 0,
        "after_kzTreeCount": 0,
        "after_sloListItems": 0,
        "after_kcCheckboxes": 0,
        "addTagsPresent": False,
        "stageSelectCount": 0,
        "kzTreeItemCount": 0,
        "kzTreeItemAnchorCount": 0,
        "curriculumRootLinks": 0,
        "raw_before": None,
        "raw_after": None,
    }
    try:
        await page.goto(url, wait_until="load", timeout=30000)
        await asyncio.sleep(4)

        landed = page.url
        if "login" in landed or "interaction" in landed or "auth.kaizenep.com" in landed:
            evidence["status"] = "auth_bounce"
            evidence["error"] = f"Redirected to auth: {landed[:80]}"
            return evidence

        if "new-section" not in landed and form_type not in landed and uuid not in landed:
            if "kaizenep.com" in landed:
                evidence["status"] = "redirect_not_form"
                evidence["error"] = f"Landed at: {landed[:80]}"
            else:
                evidence["status"] = "unknown_redirect"
                evidence["error"] = f"Landed at: {landed[:80]}"
            return evidence

        # Angular settle
        await asyncio.sleep(2)
        before_result = await page.evaluate(DOM_PROBE_JS)
        evidence["raw_before"] = before_result
        evidence["before_kzTreeCount"] = before_result.get("kzTreeElsAll", 0)
        evidence["before_sloListItems"] = before_result.get("sloListItemsInTree", 0)
        evidence["before_kcCheckboxes"] = before_result.get("kcCheckboxesInTree", 0)
        evidence["addTagsPresent"] = before_result.get("addTagsText", False) or before_result.get("addTagsNgClick", False)
        evidence["stageSelectCount"] = before_result.get("stageSelectCount", 0)
        evidence["kzTreeItemCount"] = before_result.get("kzTreeItemCount", 0)
        evidence["kzTreeItemAnchorCount"] = before_result.get("kzTreeItemAnchorCount", 0)
        evidence["curriculumRootLinks"] = before_result.get("curriculumRootLinks", 0)

        if before_result.get("stageSelectCount", 0) > 0:
            logger.info("[%s - %s] Stage selector found, selecting %s (%s)", profile_name, form_type, stage_name, stage_val)
            selected = await _select_stage_option(page, stage_val)
            if selected:
                await asyncio.sleep(5)  # wait for Angular rendering/HTTP reqs
                after_result = await page.evaluate(DOM_PROBE_JS)
                evidence["raw_after"] = after_result
                evidence["after_kzTreeCount"] = after_result.get("kzTreeElsAll", 0)
                evidence["after_sloListItems"] = after_result.get("sloListItemsInTree", 0)
                evidence["after_kcCheckboxes"] = after_result.get("kcCheckboxesInTree", 0)
                evidence["addTagsPresent"] = after_result.get("addTagsText", False) or after_result.get("addTagsNgClick", False)
                evidence["status"] = "ok"
            else:
                evidence["status"] = "error"
                evidence["error"] = f"Failed to select stage {stage_name}"
        else:
            # No stage dropdown, stage is pre-determined or not applicable
            evidence["raw_after"] = before_result
            evidence["after_kzTreeCount"] = before_result.get("kzTreeElsAll", 0)
            evidence["after_sloListItems"] = before_result.get("sloListItemsInTree", 0)
            evidence["after_kcCheckboxes"] = before_result.get("kcCheckboxesInTree", 0)
            evidence["status"] = "ok"

        logger.info(
            "[%s - %s] beforeTree=%d afterTree=%d afterSLOs=%d addTags=%s",
            profile_name,
            form_type,
            evidence["before_kzTreeCount"],
            evidence["after_kzTreeCount"],
            evidence["after_sloListItems"],
            evidence["addTagsPresent"],
        )
    except Exception as exc:
        evidence["status"] = "error"
        evidence["error"] = str(exc)[:200]
        logger.warning("[%s - %s] Error: %s", profile_name, form_type, exc)
    return evidence

def _classify_route(before_kz, after_kz, after_slo, after_kc, add_tags) -> str:
    """Derive route classification from DOM evidence."""
    # Classification rules:
    # - INLINE_TREE_PRIMARY: inline tree has SLOs and KCs after any required stage selection
    # - ADD_TAGS_ONLY: no inline tree at all, but has Add Tags button
    # - FALLBACK_INLINE_THEN_TAGS: has inline tree but might need tags / has mixed indicators
    # - NO_CURRICULUM_SURFACE: neither kz-tree nor Add Tags
    # - UNKNOWN: inspect failed

    has_inline_tree = after_kz > 0 and after_slo > 0
    has_add_tags = add_tags

    if has_inline_tree and not has_add_tags:
        return "INLINE_TREE_ONLY"
    if has_inline_tree and has_add_tags:
        return "INLINE_TREE_PRIMARY"
    if not has_inline_tree and has_add_tags:
        return "ADD_TAGS_ONLY"
    if after_kz > 0 and after_slo == 0 and has_add_tags:
        return "ADD_TAGS_ONLY"
    if after_kz == 0 and not has_add_tags:
        return "NO_CURRICULUM_SURFACE"
    return "UNKNOWN"

async def main():
    creds = _load_all_credentials_from_bws()

    profile_configs = [
        {"name": "ACCS", "creds_key": "ACCS", "stage_name": "ACCS", "stage_val": STAGE_SELECT_VALUES["ACCS"]},
        {"name": "Intermediate", "creds_key": "Intermediate", "stage_name": "Intermediate", "stage_val": STAGE_SELECT_VALUES["Intermediate"]},
        {"name": "HST", "creds_key": "HST", "stage_name": "Higher", "stage_val": STAGE_SELECT_VALUES["Higher"]}
    ]

    all_evidence = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])

        for config in profile_configs:
            p_name = config["name"]
            c_key = config["creds_key"]
            if c_key not in creds:
                logger.warning(f"Profile {p_name} is UNAVAILABLE due to missing BWS credentials")
                # Add mock entries so we explicitly mark them as unavailable
                for form_type, uuid in FORMS_TO_INSPECT:
                    all_evidence.append({
                        "profile": p_name,
                        "form_type": form_type,
                        "uuid": uuid,
                        "url_pattern": f"new-section/{uuid}",
                        "status": "unavailable",
                        "error": "Credentials not available in BWS",
                        "selected_stage": config["stage_name"],
                        "before_kzTreeCount": 0,
                        "before_sloListItems": 0,
                        "before_kcCheckboxes": 0,
                        "after_kzTreeCount": 0,
                        "after_sloListItems": 0,
                        "after_kcCheckboxes": 0,
                        "addTagsPresent": False,
                        "stageSelectCount": 0,
                    })
                continue

            username, password = creds[c_key]
            ctx = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = await ctx.new_page()

            logged_in = await _login(page, username, password)
            if not logged_in:
                logger.error(f"Login failed for profile {p_name}")
                await ctx.close()
                for form_type, uuid in FORMS_TO_INSPECT:
                    all_evidence.append({
                        "profile": p_name,
                        "form_type": form_type,
                        "uuid": uuid,
                        "url_pattern": f"new-section/{uuid}",
                        "status": "login_failed",
                        "error": "Login failed",
                        "selected_stage": config["stage_name"],
                        "before_kzTreeCount": 0,
                        "before_sloListItems": 0,
                        "before_kcCheckboxes": 0,
                        "after_kzTreeCount": 0,
                        "after_sloListItems": 0,
                        "after_kcCheckboxes": 0,
                        "addTagsPresent": False,
                        "stageSelectCount": 0,
                    })
                continue

            for form_type, uuid in FORMS_TO_INSPECT:
                logger.info("[%s] Inspecting %s ...", p_name, form_type)
                ev = await _inspect_form_profile_aware(page, form_type, uuid, p_name, config["stage_name"], config["stage_val"])
                all_evidence.append(ev)
                await asyncio.sleep(1)

            await ctx.close()

        await browser.close()

    # ─── Process & Deduplicate Route Table ──────────────────────────────────────
    # Table should be keyed by canonical form/schema, not repeated per profile;
    # attach profile evidence separately.
    today = date.today().isoformat().replace("-", "")
    repo_root = Path(__file__).parent.parent
    docs_dir = repo_root / "docs"
    docs_dir.mkdir(exist_ok=True)

    json_path = docs_dir / f"kc_route_evidence_{today}.json"
    md_path   = docs_dir / f"kc_route_evidence_{today}.md"

    # Save all raw evidence first
    with open(json_path, "w") as f:
        json.dump(all_evidence, f, indent=2)

    # Group evidence by form_type
    evidence_by_form = {}
    for form_type, _ in FORMS_TO_INSPECT:
        evidence_by_form[form_type] = []

    for ev in all_evidence:
        form_type = ev["form_type"]
        if form_type in evidence_by_form:
            evidence_by_form[form_type].append(ev)

    # Classify each canonical form. Shared forms are keyed once, with profile
    # evidence attached. If profiles disagree, keep the route fallback-safe.
    canonical_routes = {}
    for form_type, ev_list in evidence_by_form.items():
        # filter out unavailable/failed
        valid_evs = [e for e in ev_list if e["status"] == "ok"]
        if not valid_evs:
            canonical_routes[form_type] = "UNKNOWN"
            continue

        # Let's see if any valid profile evidence shows inline tree
        inline_profiles = []
        tag_profiles = []
        no_curr_profiles = []

        for ev in valid_evs:
            b_kz = ev["before_kzTreeCount"]
            a_kz = ev["after_kzTreeCount"]
            a_slo = ev["after_sloListItems"]
            a_kc = ev["after_kcCheckboxes"]
            add_tags = ev["addTagsPresent"]

            classification = _classify_route(b_kz, a_kz, a_slo, a_kc, add_tags)
            if classification in ("INLINE_TREE_PRIMARY", "INLINE_TREE_ONLY"):
                inline_profiles.append(ev["profile"])
            elif classification == "ADD_TAGS_ONLY":
                tag_profiles.append(ev["profile"])
            elif classification == "NO_CURRICULUM_SURFACE":
                no_curr_profiles.append(ev["profile"])

        if inline_profiles and tag_profiles:
            canonical_routes[form_type] = "FALLBACK_INLINE_THEN_TAGS"
        elif inline_profiles:
            canonical_routes[form_type] = "INLINE_TREE_PRIMARY"
        elif tag_profiles:
            canonical_routes[form_type] = "ADD_TAGS_ONLY"
        elif no_curr_profiles:
            canonical_routes[form_type] = "NO_CURRICULUM_SURFACE"
        else:
            canonical_routes[form_type] = "UNKNOWN"

    with open(md_path, "w") as f:
        f.write(f"# KC Route Evidence — {today}\n\n")
        f.write("Captured via profile-aware and stage-aware read-only DOM inspection of Kaizen new-section pages.\n")
        f.write("No saves, submissions, or mutations performed.\n\n")

        f.write("## Route Classification Key\n\n")
        f.write("- **INLINE_TREE_PRIMARY** — `kz-tree` inline curriculum tree rendered (initially or after stage selection).\n")
        f.write("- **ADD_TAGS_ONLY** — No inline `kz-tree` rendered; Add Tags button present.\n")
        f.write("- **FALLBACK_INLINE_THEN_TAGS** — Try inline tree first, fallback to Add Tags modal if no checkboxes found.\n")
        f.write("- **NO_CURRICULUM_SURFACE** — Neither inline tree nor Add Tags button detected.\n")
        f.write("- **UNKNOWN** — Inspection failed or inconclusive.\n\n")

        f.write("## Canonical Form Route Table\n\n")
        f.write("| Canonical Form | Route Classification | ACCS Route | Intermediate Route | HST Route | Notes / Evidence |\n")
        f.write("|----------------|----------------------|------------|--------------------|-----------|------------------|\n")

        for form_type, _ in FORMS_TO_INSPECT:
            evs = {e["profile"]: e for e in evidence_by_form[form_type]}

            def get_prof_route(prof):
                ev = evs.get(prof)
                if not ev or ev["status"] != "ok":
                    return "N/A" if ev and ev["status"] == "unavailable" else "FAIL"
                return _classify_route(
                    ev["before_kzTreeCount"],
                    ev["after_kzTreeCount"],
                    ev["after_sloListItems"],
                    ev["after_kcCheckboxes"],
                    ev["addTagsPresent"]
                )

            accs_r = get_prof_route("ACCS")
            int_r = get_prof_route("Intermediate")
            hst_r = get_prof_route("HST")

            # Notes
            notes = []
            for prof in ("ACCS", "Intermediate", "HST"):
                ev = evs.get(prof)
                if ev and ev["status"] == "ok" and ev["stageSelectCount"] > 0:
                    notes.append(f"{prof}: dropdown set {ev['selected_stage']} (before kz={ev['before_kzTreeCount']}, after kz={ev['after_kzTreeCount']}, slos={ev['after_sloListItems']})")

            notes_str = "; ".join(notes) if notes else "No stage selector"
            f.write(f"| {form_type} | {canonical_routes[form_type]} | {accs_r} | {int_r} | {hst_r} | {notes_str} |\n")

        f.write("\n## Raw Profile-Specific Evidence\n\n")
        for prof in ("ACCS", "Intermediate", "HST"):
            f.write(f"### Profile: {prof}\n\n")
            f.write("| Form | Status | Stage Dropdown | Before kz | After kz | After SLOs | After KCs | Add Tags | Route |\n")
            f.write("|------|--------|----------------|-----------|----------|------------|-----------|----------|-------|\n")
            for form_type, _ in FORMS_TO_INSPECT:
                evs = {e["profile"]: e for e in evidence_by_form[form_type]}
                ev = evs.get(prof)
                if not ev:
                    f.write(f"| {form_type} | Missing | - | - | - | - | - | - | - |\n")
                    continue
                if ev["status"] != "ok":
                    f.write(f"| {form_type} | {ev['status']} ({ev.get('error')}) | - | - | - | - | - | - | - |\n")
                    continue

                route = _classify_route(
                    ev["before_kzTreeCount"],
                    ev["after_kzTreeCount"],
                    ev["after_sloListItems"],
                    ev["after_kcCheckboxes"],
                    ev["addTagsPresent"]
                )
                f.write(
                    f"| {form_type} "
                    f"| {ev['status']} "
                    f"| {'Yes' if ev['stageSelectCount'] > 0 else 'No'} "
                    f"| {ev['before_kzTreeCount']} "
                    f"| {ev['after_kzTreeCount']} "
                    f"| {ev['after_sloListItems']} "
                    f"| {ev['after_kcCheckboxes']} "
                    f"| {ev['addTagsPresent']} "
                    f"| {route} |\n"
                )
            f.write("\n")

    print(f"\nEvidence written to:\n  {json_path}\n  {md_path}\n")

if __name__ == "__main__":
    asyncio.run(main())
