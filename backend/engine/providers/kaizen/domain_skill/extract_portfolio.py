#!/usr/bin/env python3
"""Kaizen RCEM Portfolio Extractor — autonomous extraction script.

Extracts ALL portfolio data from RCEM Kaizen via browser-harness.
Outputs structured JSON ready for Portfolio Guru ingestion.

Usage:
    BU_CDP_WS=ws://localhost:9222/... python3 extract_portfolio.py

Or via browser-harness:
    BU_CDP_WS=ws://localhost:9222/... browser-harness -c 'exec(open("agent-workspace/domain-skills/kaizen-rcem/extract_portfolio.py").read())'
"""

import json, os, sys, time
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────
KAIZEN_BASE = "https://kaizenep.com"
OUTPUT_DIR = Path(__file__).parent / "data"
CREDENTIALS = {
    "username": os.environ.get("KAIZEN_USER", ""),
    "password": os.environ.get("KAIZEN_PASS", ""),
}

# ── Helpers (mirrors agent_helpers.py to be self-contained) ─────

def js(expr):
    """Evaluate JS in the current page context."""
    r = cdp("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
    res = r.get("result", {})
    if r.get("exceptionDetails"):
        raise RuntimeError(f"JS error: {res.get('description', 'unknown')}")
    return res.get("value")


def set_react_value(selector, value):
    """Set value on AngularJS controlled inputs (fill_input workaround)."""
    sel_j = json.dumps(selector)
    val_j = json.dumps(value)
    js(f"""
        (function() {{
            var el = document.querySelector({sel_j});
            if (!el) return false;
            var setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(el, {val_j});
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }})()
    """)


def navigate(path):
    """Navigate to a Kaizen section by path."""
    js(f"window.location.href = '{KAIZEN_BASE}{path}'")
    wait_for_load()
    time.sleep(2)


def screenshot(label):
    """Take a screenshot labeled with section and sequence."""
    out = OUTPUT_DIR / "screenshots" / f"{label}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    capture_screenshot(str(out))


def extract_visible_text():
    """Get all visible text from the current page."""
    return js("""document.body.innerText""")


def extract_table_data():
    """Extract data from any table on the page."""
    return js("""
        Array.from(document.querySelectorAll('table')).map(table => ({
            headers: Array.from(table.querySelectorAll('th')).map(h => h.textContent.trim()),
            rows: Array.from(table.querySelectorAll('tr')).slice(1).map(row =>
                Array.from(row.querySelectorAll('td')).map(cell => cell.textContent.trim())
            )
        }))
    """)


# ── Extraction Steps ────────────────────────────────────────────

def login():
    """Authenticate to Kaizen (idempotent — skips if session already valid)."""
    print("[1/10] Checking session / logging in...")
    new_tab("https://eportfolio.rcem.ac.uk")
    wait_for_load()
    time.sleep(2)
    url = page_info().get("url", "")
    if "kaizenep.com" in url and "/login" not in url.lower():
        screenshot("01-already-logged-in")
        print(f"  Already authenticated — at {url}")
        return True

    has_login = js("!!document.querySelector(\"input[name='login']\")")
    if not has_login:
        print(f"  No login form found and not on Kaizen page. URL: {url}")
        screenshot("login-no-form")
        return False

    set_react_value("input[name='login']", CREDENTIALS["username"])
    set_react_value("input[name='password']", CREDENTIALS["password"])
    js("document.querySelector(\"button[type=submit]\")?.click()")
    time.sleep(5)
    url = page_info().get("url", "")
    if "kaizenep.com" not in url or "/login" in url.lower():
        print(f"  Login may have failed. Current URL: {url}")
        screenshot("login-failed")
        return False
    screenshot("01-logged-in")
    print("  Logged in successfully")
    return True


def extract_dashboard():
    """Extract dashboard overview data."""
    print("[2/10] Extracting dashboard...")
    navigate("/dashboard")
    screenshot("02-dashboard-info")

    # Dashboard widgets and curriculum info
    data = {
        "page_title": js("document.title"),
        "curriculum": extract_visible_text()[:2000],
    }
    return data


def extract_timeline_events():
    """Extract all timeline events by filtering through each type."""
    print("[3/10] Extracting timeline...")
    navigate("/")
    time.sleep(3)
    screenshot("03-timeline-home")

    filter_labels = [
        "Assessments", "Procedural Logs", "Reflections",
        "Educational Review and Meetings", "Progression",
        "Multi-Source Feedback (MSF)", "Teaching and Education",
        "Research, Audit and Quality Improvement",
        "e-Learning", "Exams", "Absence", "CCT", "Documents",
    ]

    all_events = {}

    for label in filter_labels:
        print(f"  Filter: {label}")
        try:
            js(f"""
                Array.from(document.querySelectorAll('button, a, .filter-item, .tab')).find(
                    el => el.textContent.trim().includes("{label}")
                )?.click()
            """)
            time.sleep(3)
            screenshot(f"03-timeline-{label.lower().replace(' ', '-')[:30]}")

            events = js("""
                (function() {
                    var items = [];
                    document.querySelectorAll('a[href*="/events/"], [data-event-id], .event-row, .entry-row').forEach(function(el) {
                        var m = el.href ? el.href.match(/\\/events\\/(\\d+)/) : null;
                        items.push({
                            id: m ? m[1] : (el.dataset.eventId || ''),
                            text: el.textContent.trim().substring(0, 200),
                        });
                    });
                    return items.filter(function(i) { return i.id; });
                })()
            """)
            all_events[label] = events or []
            print(f"    Found {len(all_events[label])} entries")
        except Exception as e:
            print(f"    Skipped ({e})")
            all_events[label] = []

    return all_events


def extract_event_details(events_by_type, max_per_type=3):
    """Open individual event detail views and extract field data."""
    print("[4/10] Extracting event details...")

    all_details = {}

    for event_type, events in events_by_type.items():
        if not events:
            continue
        print(f"  {event_type}: opening up to {min(max_per_type, len(events))} details...")
        type_details = []
        for event in events[:max_per_type]:
            try:
                navigate(f"/events/{event['id']}")
                time.sleep(2)

                fields = js("""
                    (function() {
                        var data = {};
                        document.querySelectorAll('.form-group, .field-group, [class*=field]').forEach(function(g) {
                            var label = g.querySelector('label, .field-label, .form-label, .kz-label');
                            var val = g.querySelector('.field-value, .form-value, .form-control-static, input:not([type=hidden]), select, textarea, .ng-binding, .ng-scope');
                            if (label) {
                                var key = label.textContent.trim().replace(/[:*\\s]+$/, '');
                                var v = '';
                                if (val) v = val.value || val.textContent.trim();
                                if (key) data[key] = v;
                            }
                        });
                        return data;
                    })()
                """)

                page_title = js("document.querySelector('h1, .page-title')?.textContent?.trim() || ''")
                type_details.append({
                    "id": event["id"],
                    "title": page_title,
                    "fields": fields or {},
                })

                screenshot(f"04-detail-{event_type.lower().replace(' ', '-')[:20]}-{event['id']}")
            except Exception as e:
                print(f"    Error on event {event['id']}: {e}")
                continue

        all_details[event_type] = type_details

    return all_details


def extract_files():
    """Extract file/document list."""
    print("[5/10] Extracting files...")
    navigate("/files")
    time.sleep(3)
    screenshot("05-files")

    files = js("""
        Array.from(document.querySelectorAll('.file-item, .document-row, tr, .file-card')).map(function(el) {
            var link = el.querySelector('a');
            return {
                name: el.textContent.trim().substring(0, 200),
                href: link ? link.href : '',
            };
        }).filter(function(f) { return f.name; })
    """)
    return files or []


def extract_goals():
    """Extract curriculum goals and SLOs."""
    print("[6/10] Extracting goals/curriculum...")
    navigate("/")
    time.sleep(2)

    # Try clicking Goals section
    try:
        js("""
            Array.from(document.querySelectorAll('a, button, .nav-item')).find(
                el => el.textContent.trim().includes('Goals')
            )?.click()
        """)
        time.sleep(3)
        screenshot("06-goals")
    except Exception:
        pass

    goals = js("""
        Array.from(document.querySelectorAll('.goal-item, .curriculum-item, .slo-item, .capability-item')).map(function(g) {
            return {
                name: g.textContent.trim().substring(0, 300),
                href: g.querySelector('a')?.href || '',
            };
        }).filter(function(g) { return g.name; })
    """)
    return goals or []


def extract_profile():
    """Extract user profile information."""
    print("[7/10] Extracting profile...")
    navigate("/profile/view")
    time.sleep(2)
    screenshot("07-profile")

    profile = js("""
        (function() {
            var data = {};
            document.querySelectorAll('.profile-field, .field-row, .info-row, .form-group').forEach(function(g) {
                var label = g.querySelector('label, .field-label, strong, .label');
                var val = g.querySelector('.value, .field-value, span:not(.label)');
                if (label) {
                    data[label.textContent.trim()] = val ? val.textContent.trim() : '';
                }
            });
            return data;
        })()
    """)
    return profile or {}


def compile_output(dashboard, events_by_type, event_details, files, goals, profile):
    """Compile all extracted data into structured JSON."""
    print("[8/10] Compiling output...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output = {
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "portfolio": "RCEM Kaizen",
        "training_stage": "Higher Trainee (2025 Update)",
        "summary": {
            "total_event_types": len(events_by_type),
            "events_found": sum(len(v) for v in events_by_type.values()),
            "details_extracted": sum(len(v) for v in event_details.values()),
        },
        "dashboard": dashboard,
        "events_by_type": events_by_type,
        "event_details": event_details,
        "files": files,
        "goals": goals,
        "profile": profile,
    }

    out_path = OUTPUT_DIR / f"portfolio-export-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps(output, indent=2, default=str))
    print(f"  Written to {out_path}")

    # Also write latest.json for easy reference
    latest_path = OUTPUT_DIR / "portfolio-export-latest.json"
    latest_path.write_text(json.dumps(output, indent=2, default=str))

    return output


# ── Main ────────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════╗")
    print("║  Kaizen Portfolio Extractor v1       ║")
    print("╚══════════════════════════════════════╝")

    # Validate credentials
    if not CREDENTIALS["username"]:
        CREDENTIALS["username"] = "drmoeedahmed@gmail.com"
    if not CREDENTIALS["password"]:
        print("ERROR: Set KAIZEN_USERNAME and KAIZEN_PASS env vars or hardcode credentials")
        sys.exit(1)

    try:
        if not login():
            print("Login failed. Check credentials or CDP connection.")
            sys.exit(1)

        dashboard = extract_dashboard()
        events_by_type = extract_timeline_events()
        event_details = extract_event_details(events_by_type)
        files = extract_files()
        goals = extract_goals()
        profile = extract_profile()

        output = compile_output(dashboard, events_by_type, event_details, files, goals, profile)

        print("\n[9/10] Done!")
        print(f"  Event types: {len(events_by_type)}")
        print(f"  Events found: {sum(len(v) for v in events_by_type.values())}")
        print(f"  Details extracted: {sum(len(v) for v in event_details.values())}")
        print(f"  Files: {len(files)}")
        print(f"  Output: {OUTPUT_DIR}/portfolio-export-latest.json")

    except Exception as e:
        print(f"\n[!] Extraction failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
