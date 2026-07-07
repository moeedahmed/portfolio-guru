"""Agent-editable browser helpers.

Add task-specific browser primitives here. Core helpers from browser_harness.helpers
load this file when BH_AGENT_WORKSPACE points at this directory, or when this
repo's default agent-workspace exists.
"""

# ═══════════════════════════════════════════════════════════════
# Kaizen RCEM ePortfolio helpers
#
# Kaizen is an AngularJS 1.x SPA (Formly + ui-select + sf-typeahead).
# Full DOM map: agent-workspace/domain-skills/kaizen-rcem/
# ═══════════════════════════════════════════════════════════════

import json
import time as _time

from browser_harness.helpers import (  # noqa: F401 — re-exported for in-module calls
    capture_screenshot,
    cdp,
    click_at_xy,
    fill_input,
    goto_url,
    http_get,
    js,
    list_tabs,
    new_tab,
    page_info,
    scroll,
    switch_tab,
    type_text,
    wait,
    wait_for_element,
    wait_for_load,
    wait_for_network_idle,
)

# ── Tab management ──
_DEFAULT_TAB = None  # set on first kaizen_goto

def _find_reusable_tab():
    """Prefer an existing Kaizen/ePortfolio tab over opening a new blank tab."""
    tabs = list_tabs()
    for tab in reversed(tabs):
        url = tab.get("url", "") or ""
        if "kaizenep.com" in url or "eportfolio.rcem.ac.uk" in url:
            return tab.get("targetId")
    return None


def _is_blank_tab(url):
    return url == "about:blank" or url.startswith("chrome://new-tab-page")


def kaizen_init():
    """Open/reuse one tab for the session.
    
    Instead of new_tab() every time, stores one targetId so goto_url() reuses
    it. Prefer an existing tab, and if a new tab is unavoidable open Kaizen
    directly rather than leaving visible about:blank clutter.
    """
    global _DEFAULT_TAB
    if _DEFAULT_TAB is None:
        _DEFAULT_TAB = _find_reusable_tab()
    if _DEFAULT_TAB is None:
        new_tab(KAIZEN_URL)
        _DEFAULT_TAB = _find_reusable_tab()
    if _DEFAULT_TAB:
        switch_tab(_DEFAULT_TAB)
    return _DEFAULT_TAB

def kaizen_close_extra_tabs():
    """Close all Kaizen/about:blank tabs except the default session tab."""
    global _DEFAULT_TAB
    for t in list_tabs():
        tid = t.get("targetId")
        url = t.get("url", "") or ""
        if tid and tid != _DEFAULT_TAB and ("kaizenep.com" in url or _is_blank_tab(url)):
            cdp("Target.closeTarget", targetId=tid)
    if _DEFAULT_TAB:
        try:
            switch_tab(_DEFAULT_TAB)
        except Exception:
            pass

def kaizen_goto(path):
    """Navigate to a Kaizen path reusing the session tab.
    
    Opens a tab on first call, reuses it thereafter.
    """
    kaizen_init()
    global _DEFAULT_TAB
    if not path.startswith("http"):
        path = KAIZEN_URL + path
    goto_url(path)
    wait_for_load()
    kaizen_wait_render()
    return page_info()


KAIZEN_URL = "https://kaizenep.com"
KAIZEN_AUTH_URL = "https://auth.kaizenep.com"


def kaizen_set_field(selector, value):
    """Set an <input>, <textarea>, or <select> value on a Formly form.

    fill_input() doubles characters on Kaizen because every keystroke
    fires input+change and Formly re-applies the model on the next
    digest. This uses the prototype's native value setter, dispatches
    one input + one change, then blurs — the canonical workaround.

    Accepts any CSS selector OR a bare "#<id>" (handled via getElementById
    so UUID-style IDs that start with a digit — common on Kaizen fields —
    don't trip the CSS parser).

    Returns True if the element was found, False otherwise.
    """
    payload = json.dumps({"selector": selector, "value": value})
    return js(
        """
        ((args) => {
          let el;
          if (args.selector.startsWith('#') && !/[^A-Za-z0-9_-]/.test(args.selector.slice(1))) {
            el = document.getElementById(args.selector.slice(1));
          } else {
            el = document.querySelector(args.selector);
          }
          if (!el) return false;
          let proto;
          if (el.tagName === 'TEXTAREA')      proto = HTMLTextAreaElement.prototype;
          else if (el.tagName === 'SELECT')   proto = HTMLSelectElement.prototype;
          else                                proto = HTMLInputElement.prototype;
          const desc = Object.getOwnPropertyDescriptor(proto, 'value');
          desc.set.call(el, args.value);
          el.dispatchEvent(new Event('input',  { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          el.blur();
          return true;
        })("""
        + payload
        + """)
        """
    )


# Backwards compatibility — set_react_value was the old (input-only) name
def set_react_value(selector, value):
    """Deprecated. Use kaizen_set_field — same behavior, handles textarea/select."""
    return kaizen_set_field(selector, value)


def kaizen_wait_render(network_timeout=15, settle=3):
    """Wait for Kaizen page to fully render after a route change.

    Kaizen lazy-loads section data over XHR and re-renders on AngularJS
    digest cycles. After goto_url() and wait_for_load(), call this so
    the h1 / form fields / counts are stable before scraping.
    """
    wait_for_network_idle(timeout=network_timeout)
    wait(settle)


# ── Form filling helpers (CDP equivalents of Playwright kaizen_form_filer.py) ──

def kaizen_fill_date(dom_id, value_uk):
    """Fill a Kaizen date field (startDate/endDate).
    
    Playwright pattern: click → triple-click → type → Tab → verify.
    CDP: focus+select → insertText → Tab key events → verify.
    """
    # Focus + select all
    cdp("Runtime.evaluate", expression=f"""
        (function() {{
            var el = document.getElementById('{dom_id}');
            if (!el) return false;
            el.focus();
            el.select();
            return true;
        }})()
    """, awaitPromise=False)
    _time.sleep(0.3)
    
    # Type new value
    cdp("Input.insertText", text=value_uk)
    _time.sleep(0.5)
    
    # Tab to trigger Angular watcher
    cdp("Input.dispatchKeyEvent", type="keyDown", key="Tab", code="Tab", windowsVirtualKeyCode=9)
    cdp("Input.dispatchKeyEvent", type="keyUp", key="Tab", code="Tab", windowsVirtualKeyCode=9)
    _time.sleep(0.5)
    
    # Verify
    actual = cdp("Runtime.evaluate", expression=f"document.getElementById('{dom_id}')?.value || ''", returnByValue=True, awaitPromise=False)
    val = actual.get("result", {}).get("value", "")
    return bool(val)


def kaizen_fill_stage(dom_id, stage_label):
    """Set stage of training dropdown by label.
    
    Playwright: select_option(value=angular_value) + 5s wait for curriculum load.
    CDP: set element value + dispatch change + wait for SLO section to load.
    """
    # Map stage labels to Kaizen angular values
    stage_values = {
        "ACCS": "string:4a0df1ca-ee5d-4591-8a89-a8937f6891ae",
        "CT1": "string:4a0df1ca-ee5d-4591-8a89-a8937f6891ae",
        "CT2": "string:4a0df1ca-ee5d-4591-8a89-a8937f6891ae",
        "Intermediate": "string:84d38818-57a8-4199-8781-61a809bfc7e3",
        "ST3": "string:84d38818-57a8-4199-8781-61a809bfc7e3",
        "Higher": "string:0669c338-e695-40f9-8fae-aee2ee7d68e1",
        "ST4": "string:0669c338-e695-40f9-8fae-aee2ee7d68e1",
        "ST5": "string:0669c338-e695-40f9-8fae-aee2ee7d68e1",
        "ST6": "string:0669c338-e695-40f9-8fae-aee2ee7d68e1",
        "PEM": "string:a1f7f581-a615-441b-8882-ec91f80f2995",
    }
    
    val_lower = stage_label.lower()
    angular_value = None
    for key, av in stage_values.items():
        if key.lower() in val_lower:
            angular_value = av
            break
    
    if not angular_value:
        angular_value = stage_values["Higher"]
    
    result = js(f"""
        (function() {{
            var el = document.getElementById('{dom_id}');
            if (!el) return false;
            el.value = '{angular_value}';
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }})()
    """)
    _time.sleep(5)  # MUST wait for curriculum SLO section to load
    return bool(result)


def kaizen_fill_text(dom_id, value):
    """Fill a text field or textarea on a Kaizen form.
    Also works as: kaizen_fill_text('#event-description', 'value')."""
    if dom_id.startswith('#'):
        selector = dom_id
    else:
        selector = f"#{dom_id}"
    
    # Escape single quotes for JS string literal
    safe_val = value.replace("\\", "\\\\").replace("'", "\\'")
    result = js(f"""
        (function() {{
            var el = document.querySelector('{selector}');
            if (!el) return false;
            var tag = el.tagName;
            var proto = tag === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
            setter.call(el, '{safe_val}');
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            el.blur();
            return true;
        }})()
    """)
    return bool(result)


def kaizen_fill_select(dom_id, option_label):
    """Set a select dropdown by option label text."""
    result = js(f"""
        (function() {{
            var el = document.getElementById('{dom_id}');
            if (!el || el.tagName !== 'SELECT') return false;
            var options = Array.from(el.options);
            var match = options.find(function(o) {{
                return o.text.toLowerCase().indexOf('{option_label.lower()}') > -1;
            }});
            if (!match) return false;
            el.value = match.value;
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }})()
    """)
    _time.sleep(1)
    return bool(result)


def kaizen_save_draft():
    """Save the current Kaizen form as draft.
    Returns the saved URL with doc_id on success."""
    result = cdp("Runtime.evaluate", expression="""
        (function() {
            var links = Array.from(document.querySelectorAll('a'));
            var save = links.find(function(a) {
                return a.textContent && a.textContent.indexOf('Save as draft') > -1;
            });
            if (save) { save.click(); return 'clicked'; }
            return 'not found';
        })()
    """, awaitPromise=False)
    _time.sleep(5)
    url = page_info().get("url", "")
    if "?doc=" in url or "doc=" in url:
        return url
    r = result.get("result",{}).get("value","")
    return r == "clicked"


def kaizen_delete_draft(draft_uuid):
    """Delete a saved draft by its UUID.
    
    Proper flow:
    1. Navigate to view-section page (not fillin page)
    2. Click the Delete link (a.text-danger)
    3. Wait for SweetAlert2 dialog
    4. Click the confirm button (button.confirm)
    
    SweetAlert2 uses class selectors, not text matching.
    """
    goto_url(f"https://kaizenep.com/events/view-section/{draft_uuid}")
    _time.sleep(6)
    
    # Click Delete (Angular link with text-danger class)
    cdp("Runtime.evaluate", expression="""
    (function() {
        var del = document.querySelector("a.text-danger");
        if (del) { del.click(); return true; }
        return false;
    })()
    """, awaitPromise=False)
    _time.sleep(2)  # SweetAlert2 dialog render time
    
    # Click OK — SweetAlert2 uses class="confirm", NOT text matching
    cdp("Runtime.evaluate", expression="""
    (function() {
        var ok = document.querySelector("button.confirm");
        if (ok) { ok.click(); return true; }
        // Fallback: search by text
        var btns = Array.from(document.querySelectorAll("a, button"));
        var ok2 = btns.find(function(b) { return b.textContent.trim() === "OK"; });
        if (ok2) { ok2.click(); return true; }
        return false;
    })()
    """, awaitPromise=False)
    _time.sleep(3)
    return True


def kaizen_send_to_assessor():
    """Send the current form to the assessor."""
    result = js("""
        (function() {
            var links = Array.from(document.querySelectorAll('a, button'));
            var send = links.find(function(el) {
                return el.textContent && el.textContent.indexOf('Send to assessor') > -1;
            });
            if (send) { send.click(); return 'clicked'; }
            return 'not found';
        })()
    """)
    _time.sleep(5)
    return result == "clicked"


def kaizen_expand_slos():
    """Expand all SLO/curriculum accordions on the current form.
    Kaizen collapses curriculum sections under stage dropdown.
    """
    result = js("""
        (function() {
            var expanded = 0;
            // Try clicking all expand/collapse buttons
            document.querySelectorAll('.section-expander, [ng-click*="expand"], [ng-click*="toggle"], .panel-heading').forEach(function(el) {
                if (el.offsetParent !== null) {  // visible
                    el.click();
                    expanded++;
                }
            });
            return expanded;
        })()
    """)
    _time.sleep(2)
    return result or 0


def kaizen_fill_slo_checkboxes(slo_uuids=None):
    """Tick specific SLO checkboxes. If None, ticks all visible."""
    if slo_uuids:
        for uid in slo_uuids:
            js(f"""
                var cb = document.querySelector('input[type="checkbox"][value="{uid}"]');
                if (cb && !cb.checked) {{ cb.click(); }}
            """)
    else:
        js("""
            document.querySelectorAll('input[type="checkbox"]:not(:checked)').forEach(function(cb) {
                if (cb.offsetParent !== null) cb.click();
            });
        """)
    _time.sleep(1)


# Backwards compatibility
def kaizen_go_to_section(section_url):
    """Deprecated. Use kaizen_goto."""
    return kaizen_goto(section_url)


def kaizen_login(username, password):
    """Login flow. Lands on /dashboard on success.

    Returns page_info() after redirect. Raises if the URL is still on
    auth.kaizenep.com (probably wrong credentials or a 2FA step).
    """
    new_tab("https://eportfolio.rcem.ac.uk")
    wait_for_load()
    kaizen_set_field("input[name='login']", username)
    kaizen_set_field("input[name='password']", password)
    js("document.querySelector('button[type=submit]').click()")
    _time.sleep(3)
    info = page_info()
    if KAIZEN_AUTH_URL in (info.get("url") or ""):
        raise RuntimeError(f"login failed — still on {info['url']}")
    return info


# ───────────────────────────────────────────────────────────────
# Timeline (read)
# ───────────────────────────────────────────────────────────────

# URL-encoded category paths from /events/list/{category}.
KAIZEN_TIMELINE_CATEGORIES = {
    "all": "All",
    "assessments": "Assessments",
    "training_post_supervisor": "Post%20%26%20Supervisor",
    "educational_reviews": "Educational%20Review%20%26%20Meetings",
    "progression": "Progression",
    "procedural_logs": "Procedural%20Logs",
    "reflections": "Reflection",
    "msf": "MSF",
    "teaching": "Teaching%20%26%20Education",
    "research_audit_qi": "Research%2C%20Audit%20%26%20QI",
    "management": "Manage%2C%20Administer%20%26%20Lead",
    "elearning": "e-Learning",
    "exams": "Exams",
    "absence": "Absence",
    "cct": "CCT",
    "documents": "Documents",
}


def kaizen_list_timeline(category="all"):
    """Return all event rows visible on the current category page.

    category is a key from KAIZEN_TIMELINE_CATEGORIES (e.g. "assessments")
    or the raw URL-encoded path. Returns a dict:
      {"total": int_from_'NNN items' label, "rows": [{title, href, uuid, state}, ...]}

    Only the first ~10 rows render eagerly. Older entries load via
    infinite scroll — scroll the page if you need more.
    """
    path = KAIZEN_TIMELINE_CATEGORIES.get(category, category)
    kaizen_goto(f"/events/list/{path}")
    return js(
        """
        (() => {
          const rows = Array.from(document.querySelectorAll('.row.event-inner')).map(r => {
            const a = r.querySelector('a[router-link]');
            const titleEl = r.querySelector('h2.entry-title');
            const stateEl = r.querySelector('.event-section-progress-state');
            const href = a ? a.href : null;
            const m = href ? href.match(/\\/events\\/(view|view-section)\\/([0-9a-f-]+)/) : null;
            return {
              title: titleEl ? titleEl.textContent.trim().replace(/\\s+/g, ' ') : null,
              href,
              uuid: m ? m[2] : null,
              section_view: m ? m[1] === 'view-section' : null,
              state: stateEl ? stateEl.textContent.trim() : null
            };
          });
          const m = document.body.textContent.match(/(\\d+)\\s+items/);
          return { total: m ? parseInt(m[1], 10) : null, rows };
        })()
        """
    )


def kaizen_extract_event_detail():
    """Read every field on the current /events/view[-section]/... page.

    Uses the verified Formly read-only selectors (.form-text__*). Returns:
      {
        "event_type": str (from h1),
        "fields": [{"label": str, "value": str}, ...],
        "tags": [str, ...],            # .event-tag chips (KC links)
        "state": str,                  # complete / pending …
        "filled_in_by": str
      }
    """
    return js(
        """
        (() => {
          const text = el => (el && el.textContent ? el.textContent.trim().replace(/\\s+/g, ' ') : null);
          const h1 = document.querySelector('h1');
          const fieldGroups = Array.from(document.querySelectorAll('.form-text__form-group, .form-readonly__form-group'));
          const fields = fieldGroups.map(g => ({
            label: text(g.querySelector('.form-text__control-label, .control-label')),
            value: text(g.querySelector('.form-text__field-value, .field-value, dd'))
          })).filter(f => f.label);
          const tags = Array.from(document.querySelectorAll('.event-tag')).map(text);
          const state = text(document.querySelector('.event-section-progress-state'));
          const filledBy = text(document.querySelector('.event-users'));
          return {
            event_type: text(h1),
            fields,
            tags,
            state,
            filled_in_by: filledBy
          };
        })()
        """
    )


# Backwards compatibility
def kaizen_get_timeline_event_ids():
    """Deprecated. Use kaizen_list_timeline — returns richer data."""
    return [r for r in (kaizen_list_timeline().get("rows") or []) if r.get("uuid")]


def kaizen_filter_timeline(filter_text):
    """Click a timeline filter button by visible text (e.g. 'Assessments').

    Prefer navigating to /events/list/{category} directly via kaizen_list_timeline —
    this exists for legacy code paths that want to drive the sidebar.
    """
    payload = json.dumps(filter_text)
    js(
        f"""
        (() => {{
          const target = {payload};
          const el = Array.from(document.querySelectorAll('a, button, .filter-item'))
            .find(e => e.textContent.trim() === target);
          if (el) el.click();
        }})()
        """
    )
    kaizen_wait_render()


# ───────────────────────────────────────────────────────────────
# Curriculum / goals (read)
# ───────────────────────────────────────────────────────────────

KAIZEN_CURRICULA = {
    "higher_2025": "Higher%20EM%20curriculum%20%282025%20Update%29",
    "intermediate_2021": "Intermediate%202021",
    "higher_2021": "Higher%202021",
    "pem": "PEM%20Subspecialty%20REFORMATTED%20%20%28Aug16-July18%29",
    "all": "all",
}


def kaizen_list_slos(curriculum="higher_2025"):
    """List every SLO/goal in a curriculum with its goal_uuid and linked-event count."""
    path = KAIZEN_CURRICULA.get(curriculum, curriculum)
    kaizen_goto(f"/goals/list/{path}")
    return js(
        """
        (() => {
          const links = Array.from(document.querySelectorAll('a[href*="/goals/work/"]'));
          return links.map(a => {
            const m = a.href.match(/\\/goals\\/work\\/([0-9a-f-]+)/);
            return { title: a.textContent.trim().replace(/\\s+/g, ' '), href: a.href, uuid: m ? m[1] : null };
          });
        })()
        """
    )


def kaizen_slo_targets(goal_uuid):
    """List Key Capabilities (targets) on /goals/work/{uuid} with progress counts."""
    kaizen_goto(f"/goals/work/{goal_uuid}")
    return js(
        """
        (() => {
          const text = el => (el && el.textContent ? el.textContent.trim().replace(/\\s+/g, ' ') : null);
          const blocks = Array.from(document.querySelectorAll('[ng-repeat*="target in goalCtrl.goal.targets"]'));
          return blocks.map(b => ({
            title: text(b.querySelector('h5.entry-title, [target-title]')),
            progress: text(b.querySelector('.col-sm-12.text-info.ng-binding'))
          }));
        })()
        """
    )


# ───────────────────────────────────────────────────────────────
# Dashboard
# ───────────────────────────────────────────────────────────────


def kaizen_dashboard_widgets():
    """Return all 11 dashboard widgets with their titles and body text."""
    kaizen_goto("/dashboard")
    return js(
        """
        (() => {
          const widgets = Array.from(document.querySelectorAll('.widget.panel.panel-default'));
          return widgets.map(w => {
            const rawTitle = (w.querySelector('.panel-title') || {}).textContent || '';
            const title = rawTitle
              .replace(/Reload widget Content|Collapse widget|Expand widget|Fullscreen widget/g, '')
              .trim();
            const body = (w.querySelector('.panel-body') || w).textContent.trim().replace(/\\s+/g, ' ');
            return {
              title,
              body_preview: body.slice(0, 400),
              link_count: w.querySelectorAll('a').length,
              has_chart: !!w.querySelector('canvas, svg.chart')
            };
          });
        })()
        """
    )


def kaizen_dashboard_slo_counts():
    """Pull SLO → linked-event count from the SLO widgets on /dashboard."""
    kaizen_goto("/dashboard")
    return js(
        """
        (() => {
          const out = {};
          const titles = [
            'Higher Clinical Specialty Learning outcomes (2025 Update)',
            'Higher Generic Specialty Learning Outcomes (2025 Update)',
            'Higher EM procedural skills (2025 Update)'
          ];
          document.querySelectorAll('.widget.panel.panel-default').forEach(w => {
            const t = (w.querySelector('.panel-title') || {}).textContent || '';
            const trimmed = t.replace(/Reload widget Content|Collapse widget|Expand widget|Fullscreen widget/g, '').trim();
            if (!titles.includes(trimmed)) return;
            const rows = Array.from(w.querySelectorAll('a[href*="/goals/work/"]')).map(a => {
              const next = a.nextSibling;
              const countText = next ? (next.textContent || '').trim() : '';
              const count = parseInt(countText.match(/\\d+/)?.[0] || '0', 10);
              return { title: a.textContent.trim(), count };
            });
            out[trimmed] = rows;
          });
          return out;
        })()
        """
    )


# ───────────────────────────────────────────────────────────────
# New event form (write)
# ───────────────────────────────────────────────────────────────


def kaizen_open_new_event(event_type_label):
    """Open the new-event picker and click the link matching event_type_label.

    Example: kaizen_open_new_event("CBD - Case Based Discussion (2025 update)")
    Lands on /events/new-section/{event-type-uuid} with a fresh form mounted.
    """
    kaizen_goto("/events/new")
    payload = json.dumps(event_type_label)
    clicked = js(
        f"""
        (() => {{
          const target = {payload};
          const link = Array.from(document.querySelectorAll('a'))
            .find(a => a.textContent.trim() === target);
          if (!link) return false;
          link.click();
          return true;
        }})()
        """
    )
    if not clicked:
        raise RuntimeError(f"could not find new-event link: {event_type_label!r}")
    kaizen_wait_render()
    return page_info()


def kaizen_pick_assessor(email):
    """Type into the assessor #invites typeahead and click the first suggestion.

    The form holds the assessor in input#invites (Twitter Typeahead via sf-typeahead).
    """
    kaizen_set_field("input#invites", email)
    wait(2)
    js(
        """
        (() => {
          const item = document.querySelector('#invites_listbox .tt-suggestion');
          if (item) item.click();
        })()
        """
    )


def kaizen_submit_new_event():
    """Click 'Send to assessor' on the current new-event form."""
    js(
        """
        (() => {
          const btn = Array.from(document.querySelectorAll('button'))
            .find(b => b.textContent.trim() === 'Send to assessor');
          if (!btn) throw new Error('Send to assessor button not found');
          btn.click();
        })()
        """
    )
    kaizen_wait_render()
    return page_info()


# ───────────────────────────────────────────────────────────────
# Bulk / pagination helpers
# ───────────────────────────────────────────────────────────────


def kaizen_scroll_collect_timeline(category="all", max_scrolls=50):
    """Scroll the current timeline page until all rows are loaded.

    Returns the full list from kaizen_list_timeline once row count
    stabilises across two scrolls or max_scrolls is hit.
    """
    path = KAIZEN_TIMELINE_CATEGORIES.get(category, category)
    kaizen_goto(f"/events/list/{path}")
    prev = -1
    for _ in range(max_scrolls):
        rows = js(
            "(() => document.querySelectorAll('.row.event-inner').length)()"
        )
        if rows == prev:
            break
        prev = rows
        scroll(0, 4000)
        wait(2)
    return kaizen_list_timeline(category)
