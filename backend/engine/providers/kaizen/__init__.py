"""Kaizen ePortfolio provider — connects via browser-harness CDP.

All browser code is written to temp files to avoid nested quoting issues.
"""
import json, os, shutil, subprocess, tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List

from ...portfoliotypes.base import detect_portfolio_type, load_selectors

DOMAIN_SKILL_DIR = Path(__file__).parent / "domain_skill"
BROWSER_HARNESS = shutil.which("browser-harness") or os.path.expanduser("~/.local/bin/browser-harness")


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
            # Auto-detect Chrome CDP WebSocket URL
            if "BU_CDP_WS" not in env:
                try:
                    import urllib.request as _ur
                    resp = _ur.urlopen("http://localhost:9222/json/version", timeout=3)
                    data = json.loads(resp.read())
                    ws = data.get("webSocketDebuggerUrl", "")
                    if ws:
                        env["BU_CDP_WS"] = ws
                except Exception:
                    pass
            env.setdefault("BU_CDP_WS", "ws://localhost:9222/devtools/browser/page")
            cmd = [BROWSER_HARNESS, "-c"]
            cmd.append("exec(open('" + tmp_path.replace("'", "'\\''") + "').read())")
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, env=env
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr[:500])
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
            if "dashboard" in output:
                self._connected = True
                self.portfolio_type = self.detect_role()
                return True
            return False
        except Exception as e:
            raise RuntimeError(f"Login failed: {e}")

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
        code = """
import json
title = cdp("Runtime.evaluate", expression="document.title", returnByValue=True, awaitPromise=False)
text = cdp("Runtime.evaluate", expression="document.body.innerText.substring(0,3000)", returnByValue=True, awaitPromise=False)
t = title.get("result",{}).get("value","")
b = text.get("result",{}).get("value","")
print(json.dumps({"title": t, "body_preview": b[:200]}))
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
