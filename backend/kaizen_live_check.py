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
DEFAULT_FORMS = ("CBD", "DOPS_2021", "REFLECT_LOG", "MINI_CEX")
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


def mapped_forms():
    """Mapped evidence forms visible in at least one profile/curriculum."""
    import bot
    import kaizen_form_filer as filer
    visible = {form for groups in (bot.TRAINING_LEVEL_FORMS, bot.FORM_CATEGORIES)
               for forms in groups.values() for form in forms}
    visible.update(bot._filter_forms_by_curriculum(sorted(visible), "2021"))
    return tuple(sorted(visible.intersection(filer.FORM_FIELD_MAP)))


def parser():
    p = argparse.ArgumentParser(description="Save and read back synthetic drafts on the operator's own Kaizen account only.")
    group = p.add_mutually_exclusive_group()
    # Catalogue imports belong after approval; bot imports credential modules.
    group.add_argument("--forms", nargs="+", help="Mapped, catalogue-visible form codes (validated after approval)")
    group.add_argument("--all-mapped", action="store_true", help="Check every mapped, catalogue-visible form")
    p.set_defaults(forms=DEFAULT_FORMS)
    return p


def synthetic_fields(form_type):
    """Schema options and explicit fake prose; no case extraction or AI call."""
    import kaizen_form_filer as filer
    from extractor import schema_form_type
    mapping = {**filer.COMMON_HEADER_FIELD_MAP, **filer.FORM_FIELD_MAP[form_type]}
    schema = filer.FORM_SCHEMAS.get(schema_form_type(form_type), {})
    specs = {s["key"]: s for s in schema.get("fields", [])}
    fields, by_target = {}, {}
    for key, target in mapping.items():
        if not_applicable_reason(form_type, key, fields):
            continue
        dom_id = filer._field_dom_id(target)
        if dom_id in by_target:
            fields[key] = by_target[dom_id]
            continue
        spec = specs.get(key, {})
        options = spec.get("options", [])
        if "date" in key or dom_id in {"startDate", "endDate"}:
            today = date.today()
            value = f"{today.day}/{today.month}/{today.year}"
        elif key == "stage_of_training":
            value = "ST5" if dom_id == "415a72f2-7cf3-420a-bee4-9a7aed746612" else "Higher"
        elif key == "event_description":
            value = MARKER
        elif key == "placement":
            value = "Anaesthetics" if "Anaesthetics" in options else "Emergency Department"
        elif spec.get("type") == "multi_select":
            value = [options[0]]
        elif options:
            value = next((option for option in options if option != "- n/a -"), options[0])
        elif spec.get("type") == "number" or (
            filer.filing_form_base(form_type) == "US_CASE" and key == "patient_age"
        ):
            # unified_dom_map.json records this as INPUT type=number, even
            # though the extraction schema calls it text. Prose is rejected.
            value = "3"
        elif key == "session_length":
            value = "30 minutes"
        elif key == "trainee_post":
            value = "ST5 Higher EM, Synthetic Hospital"
        else:
            value = f"{MARKER}. Synthetic {key}; no patient or clinical event."
        fields[key] = by_target[dom_id] = value
    # Explicit stage also covers forms with only a tag-based curriculum tree.
    fields["stage"] = "Higher"
    fields["key_capabilities"] = ["SLO6 KC1"]
    return fields


def not_applicable_reason(form_type, key, fields):
    """Omissions in this synthetic Higher-stage scenario, not missing DOM proof.

    Never exempt a field that was actually requested. A missing Higher select
    or an Other-detail request must still fail read-back normally.
    """
    import kaizen_form_filer as filer
    if filer.filing_form_base(form_type) != "PROC_LOG" or key in fields:
        return None
    if (fields.get("stage_of_training") == "Higher"
            and key in {"intermediate_procedural_skill", "accs_procedural_skill"}):
        return "outside_higher_stage_check_scenario"
    if (key in {"higher_procedural_skill_other", "procedure_other"}
            and fields.get("higher_procedural_skill")
            and not filer._is_other_choice(fields["higher_procedural_skill"])):
        return "other_option_not_selected"
    return None


def draft_url(value):
    """Only an exact Kaizen saved-document address, never a login or new form."""
    parsed = urlsplit(value or "")
    if (parsed.scheme != "https" or parsed.netloc != "kaizenep.com"
            or parsed.fragment or not re.fullmatch(r"/events/(?:(?:fillin|new-section)/[A-Za-z0-9-]+|view-section/?)", parsed.path)):
        raise GuardRefusal("The filer did not return a safe saved-draft URL.")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(key not in {"doc", "autosave", "autosaveId"} for key in query):
        raise GuardRefusal("The saved-draft URL has unexpected parameters.")
    if any(not re.fullmatch(r"[A-Za-z0-9-]+", v) for values in query.values() for v in values):
        raise GuardRefusal("The saved-draft URL has unexpected parameters.")
    if ("view-section" in parsed.path or "new-section" in parsed.path) and not query.get("doc"):
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


READ_POST_PATH = re.compile(r"/token|/elastic/[a-z_]+/?|/[a-z]+/changes")

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
    // Collapsed branches are not in the DOM, so read the tree's own selection
    // model first. 2021 trees name capabilities "SLO6 Key Capability: ..."
    // without a number; their position under the SLO is the number.
    const unnumbered = /^SLO\s*(\d+)\s+Key Capability\s*:/i;
    let modelRead = false;
    // Counted across every tree: two stages may sit in separate trees.
    let hit = false, plainHit = false, plainGroups = 0;
    for (const tree of (window.angular ? document.querySelectorAll('[kz-tree]') : [])) {
        const scope = angular.element(tree).isolateScope();
        if (!scope || !Array.isArray(scope.nodes)) continue;
        modelRead = true;
        const chosen = new Set((scope.selected || []).map(String));
        const walk = nodes => {
            let position = 0, groupHasPlain = false;
            for (const node of nodes || []) {
                const plain = (node.name || '').match(unnumbered);
                const sameSlo = plain && wanted && plain[1] === wanted[1];
                if (sameSlo) { position += 1; groupHasPlain = true; }
                if (matches(node.name) && chosen.has(String(node._id))) hit = true;
                if (sameSlo && String(position) === wanted[2] && chosen.has(String(node._id))) plainHit = true;
                walk(node.categories);
            }
            if (groupHasPlain) plainGroups += 1;
        };
        walk(scope.nodes);
    }
    // Position only identifies a capability when the SLO sits in one branch.
    if (hit || (plainHit && plainGroups === 1)) return {value: true};
    if (modelRead) return {value: false};
    for (const label of document.querySelectorAll('span.ng-binding, label')) {
        const row = label.closest('li');
        if (row && matches(label.textContent)) {
            const cb = row.querySelector('input[type=checkbox]');
            if (cb) return {value: cb.checked};
        }
    }
    // Tag-style elements are not used: an unselected suggestion looks the same
    // as a saved tag, so only a ticked checkbox counts as proof.
    return {missing: true};
}"""


def classify(state, expected):
    if state.get("missing"):
        return "empty", "dom_element_missing"
    value = state.get("value")
    if value in (None, "", False, []):
        return "empty", "value_not_persisted"
    # Dropdown values can be UUIDs; compare the selected label as well.
    if isinstance(value, list) and isinstance(expected, list):
        return ("landed", "exact_value_match") if sorted(value) == sorted(expected) else ("mismatch", "saved_value_differs")
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
    expected = filer.normalise_fields_for_deterministic_filing(form_type, fields)
    normaliser = filer.FORM_FIELD_NORMALISERS.get(form_type) or filer.FORM_FIELD_NORMALISERS.get(filer.filing_form_base(form_type))
    if normaliser:
        expected = normaliser(expected)
    expected, _ = apply_ai_declaration(form_type, expected, mapping)
    async with filer.async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(storage_state=state, service_workers="block")
            async def read_only(route):
                request = route.request
                parts = urlsplit(request.url)
                host = parts.hostname
                # Real Kaizen reads through POSTs too: the sign-in /token exchange,
                # elastic searches and /<collection>/changes sync. Only those
                # paths are let through; every other write stays blocked.
                read_post = request.method == "POST" and bool(READ_POST_PATH.fullmatch(parts.path))
                if (request.method not in {"GET", "HEAD"} and not read_post) or not (host == "kaizenep.com" or (host or "").endswith(".kaizenep.com") or host == "eportfolio.rcem.ac.uk"):
                    await route.abort()
                else:
                    await route.fallback()
            await context.route("**/*", read_only)
            await context.route_web_socket("**/*", lambda ws: ws.close())
            page = await context.new_page()
            # The first visit runs the sign-in hop and lands on the timeline;
            # the second opens the draft, which Kaizen shows at /events/fillin/<doc>.
            await page.goto(url, wait_until="load", timeout=30000)
            await page.wait_for_timeout(5000)
            await page.goto(url, wait_until="load", timeout=30000)
            doc = (parse_qs(urlsplit(url).query).get("doc") or [urlsplit(url).path.rsplit("/", 1)[-1]])[0]
            # Kaizen changes the address client-side, so poll rather than wait for a navigation.
            for _ in range(40):
                if urlsplit(page.url).hostname == "kaizenep.com" and doc in urlsplit(page.url).path:
                    break
                await page.wait_for_timeout(500)
            else:
                raise GuardRefusal("Read-back redirected away from the saved draft.")
            # Angular can render after load; a missing control remains a reported gap.
            try:
                await page.wait_for_function("ids => ids.every(id => document.getElementById(id))", arg=[filer._field_dom_id(t) for t in mapping.values()], timeout=10000)
            except Exception:
                pass
            rows = []
            for key, target in mapping.items():
                dom_id = filer._field_dom_id(target)
                omitted = not_applicable_reason(form_type, key, fields)
                if omitted:
                    rows.append({"field": key, "dom_id": dom_id,
                                 "classification": "not-applicable", "reason": omitted})
                    continue
                wanted = expected[key]
                if "date" in key or dom_id in {"startDate", "endDate"}:
                    wanted = filer._to_uk_date(wanted)
                elif key == "stage_of_training":
                    stages = filer.QIAT_STAGE_VALUES if dom_id == "415a72f2-7cf3-420a-bee4-9a7aed746612" else filer.STAGE_SELECT_VALUES
                    wanted = stages[wanted]
                if key in filer._MULTISELECT_WIDGET_FIELDS:
                    widget = await filer._read_widget_state(page, dom_id)
                    observed = {"missing": widget.get("missing", not widget), "value": filer._widget_selected_values(widget)}
                else:
                    observed = await page.evaluate(READ_FIELD_JS, dom_id)
                if observed.get("label") and filer._is_dops_form(form_type):
                    # Kaizen's own label for the same choice, e.g. "Emergency
                    # Medicine" for Emergency Department; never a different one.
                    from dops_filing import normalise_dops_placement, normalise_dops_procedure
                    if key == "placement":
                        wanted = normalise_dops_placement(wanted, [observed["label"]])
                    elif key in filer._DOPS_PROCEDURE_KEYS:
                        wanted = normalise_dops_procedure(wanted, [observed["label"]])
                kind, reason = classify(observed, wanted)
                rows.append({"field": key, "dom_id": dom_id, "classification": kind, "reason": reason})
            if filer._uses_tag_based_curriculum(form_type):
                # Tag-style forms keep chosen capabilities behind the "Add tags (n)"
                # button; the saved count is the proof, as in the filer's own QA.
                try:
                    await page.wait_for_function("(" + filer.TAG_COUNT_JS + ")() > 0", timeout=10000)
                except Exception:
                    pass
                count = int(await page.evaluate(filer.TAG_COUNT_JS) or 0)
                wanted_count = len(fields["key_capabilities"])
                # A saved draft only exposes tag ids, not names, without opening
                # the tag picker, so this proves how many saved, not which one.
                # "count-only" is reported as such and never counted as landed.
                for target in fields["key_capabilities"]:
                    if count == wanted_count:
                        kind, reason = "count-only", "tag_identity_not_readable"
                    else:
                        kind, reason = classify({"value": False}, True)
                    rows.append({"field": f"tag:{target}", "classification": kind, "reason": reason})
                return rows
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
    # Suppress third-party exception logs/prints before any credential retrieval.
    previous_logging = logging.root.manager.disable
    debug_env = {key: os.environ.get(key) for key in ("DEBUG", "PWDEBUG")}
    os.environ["DEBUG"] = ""
    os.environ["PWDEBUG"] = "0"
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as silence, redirect_stdout(silence), redirect_stderr(silence):
            if not forms or any(form not in mapped_forms() for form in forms):
                raise GuardRefusal("Choose mapped, catalogue-visible evidence forms only.")
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
                        entry = {"form": form, "status": "failed", "draft_url": None, "fields": unread, "reason": "filing_or_readback_failed",
                                 "uploads": {"classification": "not-applicable", "reason": "no_mapped_file_upload_fields"}}
                        try:
                            fields = synthetic_fields(form)
                            result = await filer.file_to_kaizen(form, fields, username, password, curriculum_links=["SLO6"], submit=False, telegram_user_id=OPERATOR_USER_ID)
                            # A saved URL is evidence of a save; draft_url alone can be an unconfirmed autosave.
                            candidate = result.get("saved_url") or result.get("draft_url")
                            if (result.get("status") == "failed" and not candidate
                                    and "is not available on your Kaizen profile or curriculum right now; Kaizen redirected to " in str(result.get("error", ""))):
                                entry.update(status="unavailable", reason="form_not_available_on_operator_profile")
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
                                entry["status"] = "passed" if all(r["classification"] in {"landed", "count-only", "not-applicable"} for r in entry["fields"]) and not skips and result["status"] == "success" else "partial"
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
    code = FAILED if any(e["status"] == "failed" for e in results) else PARTIAL if any(e["status"] in {"partial", "unavailable"} for e in results) else 0
    status = "unavailable" if all(e["status"] == "unavailable" for e in results) else "failed" if code == FAILED else "partial" if code else "passed"
    report = {"status": status, "browser_isolation": "separate-headless-browser-and-temporary-session-cache", "forms": results}
    return report, code


def write_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    (directory / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = ["# Kaizen operator filing check", "", f"Result: {report['status']}", "", "Synthetic drafts only. No submission or deletion. DOM values and credentials omitted.", ""]
    for form in report["forms"]:
        lines += [f"## {form['form']}", "", f"{form['status']}: landed {form['landed']}/{form['total']}", f"Draft: {form['draft_url'] or 'no confirmed draft URL'}", f"Reason: {form['reason']}", ""]
        lines += [f"- {r['field']}: {r['classification']} ({r['reason']})" for r in form["fields"] if r["classification"] != "landed"]
        lines.append(f"- uploads: {form['uploads']['classification']} ({form['uploads']['reason']})")
        lines.append("")
    (directory / "summary.md").write_text("\n".join(lines))
