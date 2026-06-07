"""Kaizen ePortfolio provider — connects via browser-harness CDP.

All browser code is written to temp files to avoid nested quoting issues.
"""
import json, os, shutil, subprocess, tempfile
from urllib.parse import urlparse
from pathlib import Path
from typing import Optional, Dict, Any, List

from ...portfoliotypes.base import detect_portfolio_type, load_selectors

DOMAIN_SKILL_DIR = Path(__file__).parent / "domain_skill"
BROWSER_HARNESS = shutil.which("browser-harness") or os.path.expanduser("~/.local/bin/browser-harness")
# Bot starts Chrome on 18800; do NOT default to Chrome's stock 9222 — a stray
# Chrome on 9222 would silently shadow the managed profile.
DEFAULT_KAIZEN_CDP_URL = "http://localhost:18800"

# Body preview size handed to ``detect_portfolio_type``. The legacy 200-char
# window was too small to surface SAS / CESR / Non-Trainee signals — Kaizen
# renders the chrome (nav, header, breadcrumbs) before the portfolio-type
# label, so the marker words land well after byte 200 on a real dashboard.
# Pin to a broad dashboard read. Dual-access accounts can expose the higher
# portfolio link well below the initial header/nav block, so a short preview
# may see ACCS only and miss Intermediate.
KAIZEN_DASHBOARD_BODY_PREVIEW_CHARS = 30000


class KaizenInfrastructureError(RuntimeError):
    """Browser-harness, CDP, or subprocess failure — *not* a credentials problem.

    Callers should treat this as 'we couldn't even ask Kaizen', distinct from
    a credentials rejection (which is signalled by ``connect()`` returning
    ``False``). Misclassifying infra failure as bad credentials trains users
    to retype passwords that are actually fine — keep the split.
    """


def _resolve_cdp_ws(env: Optional[Dict[str, str]] = None, *, timeout: float = 3.0) -> Optional[str]:
    """Return the browser-harness CDP WebSocket URL for the managed Kaizen Chrome.

    Resolution order:
      1. ``BU_CDP_WS`` already set in the env → use it verbatim.
      2. ``KAIZEN_CDP_URL`` (default ``http://localhost:18800``) → fetch
         ``/json/version`` and return ``webSocketDebuggerUrl``.
      3. On any failure → ``None`` (caller decides whether to proceed without
         setting ``BU_CDP_WS``).
    """
    env = env if env is not None else os.environ
    existing = env.get("BU_CDP_WS")
    if existing:
        return existing
    cdp_url = env.get("KAIZEN_CDP_URL", DEFAULT_KAIZEN_CDP_URL)
    parsed = urlparse(cdp_url)
    netloc = parsed.netloc or "localhost:18800"
    scheme = parsed.scheme or "http"
    version_url = f"{scheme}://{netloc}/json/version"
    try:
        import urllib.request as _ur
        resp = _ur.urlopen(version_url, timeout=timeout)
        data = json.loads(resp.read())
    except Exception:
        return None
    ws = data.get("webSocketDebuggerUrl", "")
    return ws or None


class KaizenProvider:
    """Kaizen ePortfolio provider."""

    BASE_URL = "https://kaizenep.com"

    def __init__(self, username: str = "", password: str = ""):
        self.username = username or os.environ.get("KAIZEN_USER", "drmoeedahmed@gmail.com")
        self.password = password or os.environ.get("KAIZEN_PASS", "")
        self.portfolio_type: str = "unknown"
        self._connected = False
        self.selectors = load_selectors()

    def _run_file(self, code: str, timeout: int = 60) -> str:
        """Write code to temp file and run via browser-harness."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            tmp_path = f.name
        try:
            env = os.environ.copy()
            env["KAIZEN_USER"] = self.username
            env["KAIZEN_PASS"] = self.password
            ws = _resolve_cdp_ws(env)
            if ws:
                env["BU_CDP_WS"] = ws
            cmd = [BROWSER_HARNESS, "-c"]
            cmd.append("exec(open('" + tmp_path.replace("'", "'\\''") + "').read())")
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout, env=env
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
                raise KaizenInfrastructureError(f"browser-harness invocation failed: {exc}") from exc
            if result.returncode != 0:
                raise KaizenInfrastructureError(result.stderr[:500] or "browser-harness exited non-zero")
            return result.stdout.strip()
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def connect(self) -> bool:
        """Log into Kaizen."""
        code = """
import time
goto_url("https://eportfolio.rcem.ac.uk")
time.sleep(6)
set_react_value("input[name=login]", os.environ.get("KAIZEN_USER", ""))
set_react_value("input[name=password]", os.environ.get("KAIZEN_PASS", ""))
cdp("Runtime.evaluate", expression="document.querySelector('button[type=submit]').click()", awaitPromise=False)
time.sleep(6)
url = page_info().get("url", "")
print(url)
"""
        try:
            output = self._run_file(code, timeout=30)
        except KaizenInfrastructureError:
            # Browser-harness/CDP/subprocess failure. Do NOT classify as bad
            # credentials — let callers tell the user "couldn't reach Kaizen".
            raise
        except Exception as e:
            raise KaizenInfrastructureError(f"unexpected provider failure: {e}") from e
        if "dashboard" in output:
            self._connected = True
            self.portfolio_type = self.detect_role()
            return True
        return False

    def disconnect(self):
        """Log out and close browser."""
        if not self._connected:
            return
        code = """
import time
cdp("Runtime.evaluate", expression="(function(){var btns=Array.from(document.querySelectorAll('a, button'));var logout=btns.find(function(b){return b.textContent&&b.textContent.trim()==='Logout'});if(logout){logout.click();return true}return false})()", awaitPromise=False)
time.sleep(3)
for t in list_tabs():
    tid = t.get("targetId")
    if tid: cdp("Target.closeTarget", targetId=tid)
print("ok")
"""
        try:
            self._run_file(code, timeout=15)
        except Exception:
            pass
        self._connected = False

    def detect_role(self) -> str:
        """Detect portfolio type from dashboard title."""
        code = f"""
import json
title = cdp("Runtime.evaluate", expression="document.title", returnByValue=True, awaitPromise=False)
text = cdp("Runtime.evaluate", expression="document.body.innerText.substring(0,{KAIZEN_DASHBOARD_BODY_PREVIEW_CHARS})", returnByValue=True, awaitPromise=False)
t = title.get("result",{{}}).get("value","")
b = text.get("result",{{}}).get("value","")
print(json.dumps({{"title": t, "body_preview": b[:{KAIZEN_DASHBOARD_BODY_PREVIEW_CHARS}]}}))
"""
        try:
            output = self._run_file(code, timeout=15)
            data = json.loads(output)
            return detect_portfolio_type(data.get("title",""), data.get("body_preview",""))
        except Exception:
            return "unknown"

    def get_form_uuid(self, form_type: str) -> Optional[str]:
        """Get new-section UUID for a form type."""
        uuids_file = DOMAIN_SKILL_DIR / "2025-uuids.json"
        if uuids_file.exists():
            data = json.loads(uuids_file.read_text())
            for name, uuid in data.get("forms", {}).items():
                if name.startswith(form_type) or form_type in name:
                    return uuid
        selectors = self.selectors
        role_forms = selectors.get("form_types_by_role", {}).get(self.portfolio_type, {})
        return role_forms.get(form_type)


    def fill_form(self, form_type: str, fields: dict) -> bool:
        # Open a form and fill fields without saving.
        form_uuid = self.get_form_uuid(form_type)
        if not form_uuid:
            raise ValueError("Unknown form type: " + form_type)

        script = str(DOMAIN_SKILL_DIR / "fill_form.py")
        cfg = json.dumps({"form_uuid": form_uuid, "fields": fields})
        env = os.environ.copy()
        env["KAIZEN_FILL_CFG"] = cfg
        env["KAIZEN_USER"] = self.username
        env["KAIZEN_PASS"] = self.password

        try:
            result = subprocess.run(
                [BROWSER_HARNESS, "-c", 'import os,json;exec(open("' + script + '").read())'],
                capture_output=True, text=True, timeout=60, env=env
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr[:300])
            return "filled" in result.stdout
        except Exception as e:
            raise RuntimeError("Fill failed: " + str(e))

    def save_draft(self) -> Optional[str]:
        """Save current form as draft. Returns doc URL."""
        code = """
import time
links = list_tabs()
cdp("Runtime.evaluate", expression="(function(){var la=Array.from(document.querySelectorAll('a'));var s=la.find(function(a){return a.textContent&&a.textContent.indexOf('Save as draft')>-1});if(s){s.click();return 1}return 0})()", awaitPromise=False)
time.sleep(5)
u = page_info().get('url','')
print(u if 'doc=' in u else '')
"""
        try:
            output = self._run_file(code, timeout=20)
            return output if output else None
        except Exception:
            return None

    def delete_draft(self, draft_uuid: str) -> bool:
        """Delete a saved draft via SweetAlert2 dialog."""
        code = f"""
import time
goto_url("https://kaizenep.com/events/view-section/{draft_uuid}")
time.sleep(6)
cdp("Runtime.evaluate", expression="(function(){{var d=document.querySelector('a.text-danger');if(d){{d.click();return 1}}return 0}})()", awaitPromise=False)
time.sleep(2)
cdp("Runtime.evaluate", expression="(function(){{var o=document.querySelector('button.confirm');if(o){{o.click();return 1}}return 0}})()", awaitPromise=False)
time.sleep(3)
print('ok')
"""
        try:
            self._run_file(code, timeout=20)
            return True
        except Exception:
            return False

    def extract_timeline(self, category: str = "All") -> list:
        """Extract events from timeline listing."""
        from urllib.parse import quote
        cat = quote(category)
        code = f"""
import json, time, re
goto_url("https://kaizenep.com/events/list/{cat}")
time.sleep(8)
t = cdp("Runtime.evaluate", expression="document.body.innerText", returnByValue=True, awaitPromise=False)
txt = t.get("result",{{}}).get("value","")
m = re.search(r"Found (\\\\d+) items", txt)
count = m.group(1) if m else "?"
print(json.dumps({{"count": count, "preview": txt[:200]}}))
"""
        try:
            output = self._run_file(code, timeout=30)
            return json.loads(output)
        except Exception:
            return {"count": 0}
