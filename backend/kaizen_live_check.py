"""Hand-run operator draft check. Never imported by the bot or release loop.

Only statuses are retained: DOM values, storage state and provider errors may
contain private data and must never enter reports. Run in its own process.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, urlsplit

OPERATOR_USER_ID = 6912896590  # bot.ADMIN_USER_ID; deliberately no user-id option
DEFAULT_FORMS = ("CBD", "DOPS_2021", "REFLECT_LOG_2021", "MINI_CEX")
MARKER = "PG CHECK - synthetic test draft, safe to delete"
PARTIAL, FAILED, REFUSED = 2, 3, 4


class GuardRefusal(Exception):
    """Safe fixed-wording refusal, without provider error details."""


def require_approval():
    if os.environ.get("KAIZEN_LIVE_CHECK_APPROVED") != "operator-own-account":
        raise GuardRefusal("Explicit operator-own-account approval is required.")
    if os.environ.get("PG_ENV", "").strip().lower() in {"staging", "offline", "test"}:
        raise GuardRefusal("Staging/offline environments are refused.")
    if os.environ.get("PG_KAIZEN_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
        raise GuardRefusal("Offline Kaizen mode is refused.")


def parser():
    p = argparse.ArgumentParser(description="Save and read back synthetic drafts on the operator's own Kaizen account only.")
    p.add_argument("--forms", nargs="+", choices=DEFAULT_FORMS, default=DEFAULT_FORMS)
    return p


def synthetic_fields(form_type):
    """Schema options and explicit fake prose; no case extraction or AI call."""
    import kaizen_form_filer as filer
    mapping = {**filer.COMMON_HEADER_FIELD_MAP, **filer.FORM_FIELD_MAP[form_type]}
    schema = filer.FORM_SCHEMAS.get(form_type, filer.FORM_SCHEMAS.get(filer.filing_form_base(form_type), {}))
    specs = {s["key"]: s for s in schema.get("fields", [])}
    fields, by_target = {}, {}
    for key, target in mapping.items():
        dom_id = filer._field_dom_id(target)
        if dom_id in by_target:
            fields[key] = by_target[dom_id]
            continue
        options = specs.get(key, {}).get("options", [])
        if "date" in key or dom_id in {"startDate", "endDate"}:
            value = date.today().isoformat()
        elif key == "stage_of_training":
            value = "Higher"
        elif key == "event_description":
            value = MARKER
        elif key == "placement":
            value = "Anaesthetics" if "Anaesthetics" in options else "Emergency Department"
        elif options:
            value = options[0]
        else:
            value = f"{MARKER}. Synthetic {key}; no patient or clinical event."
        fields[key] = by_target[dom_id] = value
    # Explicit stage also covers forms with only a tag-based curriculum tree.
    fields["stage"] = "Higher"
    fields["key_capabilities"] = ["SLO6 KC1"]
    return fields


def draft_url(value):
    """Only an exact Kaizen saved-document address, never a login or new form."""
    parsed = urlsplit(value or "")
    if (parsed.scheme != "https" or parsed.netloc != "kaizenep.com"
            or parsed.fragment or not re.fullmatch(r"/events/(?:fillin/[A-Za-z0-9-]+|view-section/?)", parsed.path)):
        raise GuardRefusal("The filer did not return a safe saved-draft URL.")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(key not in {"doc", "autosave", "autosaveId"} for key in query):
        raise GuardRefusal("The saved-draft URL has unexpected parameters.")
    if any(not re.fullmatch(r"[A-Za-z0-9-]+", v) for values in query.values() for v in values):
        raise GuardRefusal("The saved-draft URL has unexpected parameters.")
    if "view-section" in parsed.path and not query.get("doc"):
        raise GuardRefusal("The saved-draft URL lacks a document identity.")
    return value


def skipped_fields(form_type, skipped):
    import kaizen_form_filer as filer
    mapping = {**filer.COMMON_HEADER_FIELD_MAP, **filer.FORM_FIELD_MAP[form_type]}
    handling = filer.SCHEMA_REQUIRED_FIELD_HANDLING.get(filer.filing_form_base(form_type), {})
    rows = []
    for item in skipped:
        # Never copy arbitrary text from a provider result into an artefact.
        key = item if item in mapping or item in handling else None
        if key:
            rows.append({"field": key, "classification": "empty" if key in mapping else "not-mapped",
                         "reason": handling.get(key, "filer_skipped")})
        else:
            rows.append({"field": "unclassified_filer_skip", "classification": "not-mapped", "reason": "filer_skipped_unmapped_field"})
    return rows


READ_FIELD_JS = """id => {
    const el = document.getElementById(id);
    if (!el) return {missing: true};
    if (el.tagName === 'SELECT') return {value: el.value,
        label: el.selectedOptions[0]?.textContent.trim() || ''};
    if (el.type === 'checkbox') return {value: el.checked};
    return {value: el.value === undefined ? el.textContent.trim() : el.value};
}"""

READ_KC_JS = r"""target => {
    const code = s => (s || '').match(/SLO\s*(\d+).*?(?:KC|Key Capability)\s*(\d+)\b/i);
    const wanted = code(target);
    const matches = s => {const m = code(s); return m && wanted && m[1] === wanted[1] && m[2] === wanted[2];};
    for (const label of document.querySelectorAll('span.ng-binding, label')) {
        const row = label.closest('li');
        if (row && matches(label.textContent)) {
            const cb = row.querySelector('input[type=checkbox]');
            if (cb) return {value: cb.checked};
        }
    }
    // Explicit saved tag labels only; a non-zero Add tags count is not identity proof.
    for (const tag of document.querySelectorAll('.tag, [data-tag], [ng-repeat*="tag"]')) {
        if (matches(tag.textContent) && !tag.querySelector('input')) return {value: true};
    }
    return {missing: true};
}"""


def classify(state, expected):
    if state.get("missing"):
        return "empty", "dom_element_missing"
    value = state.get("value")
    if value in (None, "", False, []):
        return "empty", "value_not_persisted"
    # Dropdown values can be UUIDs; compare the selected label as well.
    if str(value).strip().replace("\r\n", "\n") == str(expected).strip().replace("\r\n", "\n") or state.get("label") == expected:
        return "landed", "exact_value_match"
    return "mismatch", "saved_value_differs"


async def read_back(form_type, fields, url, username):
    """Fresh authenticated context; no writes, clicks, filling or assessor flow."""
    import kaizen_form_filer as filer
    from ai_declaration import apply_ai_declaration
    url = draft_url(url)
    state = filer.load_session_state(OPERATOR_USER_ID, username)
    if not state:
        raise GuardRefusal("No isolated authenticated session is available for read-back.")
    mapping = {**filer.COMMON_HEADER_FIELD_MAP, **filer.FORM_FIELD_MAP[form_type]}
    expected, _ = apply_ai_declaration(form_type, fields, mapping)
    async with filer.async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(storage_state=state, service_workers="block")
            async def read_only(route):
                request = route.request
                host = urlsplit(request.url).hostname
                if request.method not in {"GET", "HEAD"} or host not in {"kaizenep.com", "auth.kaizenep.com", "eportfolio.rcem.ac.uk"}:
                    await route.abort()
                else:
                    await route.fallback()
            await context.route("**/*", read_only)
            await context.route_web_socket("**/*", lambda ws: ws.close())
            page = await context.new_page()
            await page.goto(url, wait_until="load", timeout=30000)
            if page.url != url:
                raise GuardRefusal("Read-back redirected away from the saved draft.")
            # Angular can render after load; a missing control remains a reported gap.
            try:
                await page.wait_for_function("ids => ids.every(id => document.getElementById(id))", arg=[filer._field_dom_id(t) for t in mapping.values()], timeout=10000)
            except Exception:
                pass
            rows = []
            for key, target in mapping.items():
                dom_id = filer._field_dom_id(target)
                wanted = expected[key]
                if "date" in key or dom_id in {"startDate", "endDate"}:
                    d = date.fromisoformat(wanted)
                    wanted = f"{d.day}/{d.month}/{d.year}"
                elif key == "stage_of_training":
                    wanted = filer.STAGE_SELECT_VALUES[wanted]
                observed = await page.evaluate(READ_FIELD_JS, dom_id)
                kind, reason = classify(observed, wanted)
                rows.append({"field": key, "dom_id": dom_id, "classification": kind, "reason": reason})
            # Wait for delayed curriculum rendering too, without opening or mutating controls.
            for target in fields["key_capabilities"]:
                try:
                    await page.wait_for_function("target => (" + READ_KC_JS + ")(target).value === true", arg=target, timeout=10000)
                except Exception:
                    pass
                kind, reason = classify(await page.evaluate(READ_KC_JS, target), True)
                rows.append({"field": f"kc:{target}", "classification": kind, "reason": reason})
            return rows
        finally:
            await browser.close()


async def run_check(forms=DEFAULT_FORMS):
    """No selectable account or credentials. Entry point for a separate process."""
    require_approval()
    forms = tuple(dict.fromkeys(forms))
    if not forms or any(form not in DEFAULT_FORMS for form in forms):
        raise GuardRefusal("Choose a subset of the four supported check forms.")
    # Suppress third-party exception logs/prints before any credential retrieval.
    previous_logging = logging.root.manager.disable
    debug_env = {key: os.environ.get(key) for key in ("DEBUG", "PWDEBUG")}
    os.environ["DEBUG"] = ""
    os.environ["PWDEBUG"] = "0"
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as silence, redirect_stdout(silence), redirect_stderr(silence):
            import credentials
            import kaizen_form_filer as filer
            import filing_result_logger
            own = credentials.get_credentials(OPERATOR_USER_ID)
            if not own or not all(own):
                raise GuardRefusal("The operator has no stored password connection; reconnect before running this check.")
            username, password = own
            original = filer.KAIZEN_USE_CDP, filer._SESSION_DIR, filing_result_logger.log_filing_result
            with tempfile.TemporaryDirectory(prefix="pg-kaizen-check-") as temporary:
                # Account cache writes/invalidations stay local, never touch the bot's cache.
                filer.KAIZEN_USE_CDP = False
                filer._SESSION_DIR = Path(temporary) / "sessions"
                # The production diagnostic log accepts arbitrary provider errors/DOM
                # states. Omit it in this child; only our sanitised report persists.
                filing_result_logger.log_filing_result = lambda *_args, **_kwargs: None
                results = []
                try:
                    for form in forms:
                        mapping = {**filer.COMMON_HEADER_FIELD_MAP, **filer.FORM_FIELD_MAP[form]}
                        unread = [{"field": key, "dom_id": filer._field_dom_id(target), "classification": "empty", "reason": "not_read_back"} for key, target in mapping.items()]
                        unread.append({"field": "kc:SLO6 KC1", "classification": "empty", "reason": "not_read_back"})
                        entry = {"form": form, "status": "failed", "draft_url": None, "fields": unread, "reason": "filing_or_readback_failed"}
                        try:
                            fields = synthetic_fields(form)
                            result = await filer.file_to_kaizen(form, fields, username, password, curriculum_links=["SLO6"], submit=False, telegram_user_id=OPERATOR_USER_ID)
                            # A saved URL is evidence of a save; draft_url alone can be an unconfirmed autosave.
                            candidate = result.get("saved_url") or result.get("draft_url")
                            if candidate:
                                entry["draft_url"] = draft_url(candidate)
                            if result.get("saved_url") and result.get("status") in {"success", "partial"}:
                                entry["reason"] = "readback_failed"
                                rows = await read_back(form, fields, entry["draft_url"], username)
                                skips = skipped_fields(form, result.get("skipped", []))
                                for row in rows:
                                    matching = next((s for s in skips if s["field"] == row["field"]), None)
                                    if matching:
                                        row["filer_skip_reason"] = matching["reason"]
                                entry["fields"] = rows + [r for r in skips if r["field"] not in {v["field"] for v in rows}]
                                entry["status"] = "passed" if all(r["classification"] == "landed" for r in entry["fields"]) and not skips and result["status"] == "success" else "partial"
                                entry["reason"] = "readback_complete"
                        except Exception:
                            # Never retain provider exceptions or arbitrary result values.
                            pass
                        entry["landed"] = sum(r["classification"] == "landed" for r in entry["fields"])
                        entry["total"] = len(entry["fields"])
                        results.append(entry)
                finally:
                    filer.KAIZEN_USE_CDP, filer._SESSION_DIR, filing_result_logger.log_filing_result = original
    finally:
        logging.disable(previous_logging)
        for key, value in debug_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    code = FAILED if any(e["status"] == "failed" for e in results) else PARTIAL if any(e["status"] == "partial" for e in results) else 0
    report = {"status": "failed" if code == FAILED else "partial" if code else "passed", "browser_isolation": "separate-headless-browser-and-temporary-session-cache", "forms": results}
    return report, code


def write_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    (directory / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = ["# Kaizen operator filing check", "", f"Result: {report['status']}", "", "Synthetic drafts only. No submission or deletion. DOM values and credentials omitted.", ""]
    for form in report["forms"]:
        lines += [f"## {form['form']}", "", f"{form['status']}: landed {form['landed']}/{form['total']}", f"Draft: {form['draft_url'] or 'no confirmed draft URL'}", f"Reason: {form['reason']}", ""]
        lines += [f"- {r['field']}: {r['classification']} ({r['reason']})" for r in form["fields"] if r["classification"] != "landed"]
        lines.append("")
    (directory / "summary.md").write_text("\n".join(lines))
