"""Voice profile Kaizen sampler — read-only service boundary.

This module is the only place that reaches out to Kaizen to pull existing
portfolio entries for voice-profile learning. The Telegram flow only calls it
after the user chooses the Kaizen learning path and a sample window.

Contract:
- Pure read-only: never submits, deletes, edits, or creates Kaizen content.
- Uses the authenticated managed Chrome/CDP session first. If that session has
  expired, it may restore the session with the user's saved encrypted
  credentials before retrying the read-only scrape.
- Normal tests mock the browser-harness runner and never touch live Kaizen.
- Returns a typed result so callers can branch on availability without parsing
  free text.

Sample windows are exposed as an explicit enum so callers (and tests) can't
typo their way into a different range:

- ``RECENT_10`` — the most recently created 10 entries, ignoring date.
- ``LAST_6M`` — entries created in the past ~6 months.
- ``LAST_12M`` — entries created in the past ~12 months.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import textwrap
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


DEFAULT_KAIZEN_CDP_URL = "http://localhost:18800"
BROWSER_HARNESS = shutil.which("browser-harness") or os.path.expanduser("~/.local/bin/browser-harness")


class SampleWindow(str, Enum):
    RECENT_10 = "recent_10"
    LAST_6M = "last_6m"
    LAST_12M = "last_12m"


WINDOW_LABELS = {
    SampleWindow.RECENT_10: "Recent 10 entries",
    SampleWindow.LAST_6M: "Last 6 months",
    SampleWindow.LAST_12M: "Last 12 months",
}


class SamplerStatus(str, Enum):
    NOT_AVAILABLE = "not_available"
    NO_SAMPLES = "no_samples"
    OK = "ok"


@dataclass
class SamplerResult:
    status: SamplerStatus
    window: SampleWindow
    samples: List[str] = field(default_factory=list)
    message: Optional[str] = None
    reason: Optional[str] = None

    @property
    def has_samples(self) -> bool:
        return self.status == SamplerStatus.OK and bool(self.samples)


def parse_window(raw: str) -> Optional[SampleWindow]:
    """Map a raw callback token (e.g. ``"recent_10"``) to ``SampleWindow``.

    Returns ``None`` for unknown tokens so callers can show a friendly error
    instead of crashing on a stale button.
    """
    try:
        return SampleWindow(raw)
    except ValueError:
        return None


def _window_limit(window: SampleWindow) -> int:
    if window == SampleWindow.RECENT_10:
        return 10
    if window == SampleWindow.LAST_6M:
        return 25
    return 50


def _resolve_cdp_ws(env: dict[str, str] | None = None, timeout: float = 3.0) -> Optional[str]:
    env = env or os.environ
    existing = env.get("BU_CDP_WS")
    if existing:
        return existing
    cdp_url = env.get("KAIZEN_CDP_URL", DEFAULT_KAIZEN_CDP_URL).rstrip("/")
    try:
        with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return data.get("webSocketDebuggerUrl") or None


def _browser_script(limit: int) -> str:
    """Return browser-harness code that reads existing entries only."""
    return textwrap.dedent(
        f"""
        import json, re, time

        MAX_ROWS = {limit}
        MAX_SAMPLES = 12

        def emit(payload):
            print("VOICE_SAMPLER_JSON_START")
            print(json.dumps(payload))
            print("VOICE_SAMPLER_JSON_END")

        def read_js(expr):
            result = cdp("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
            if result.get("exceptionDetails"):
                raise RuntimeError(str(result.get("exceptionDetails")))
            return result.get("result", {{}}).get("value")

        def open_readonly(url):
            new_tab(url)
            wait_for_load()
            try:
                wait_for_network_idle(timeout=15)
            except Exception:
                pass
            wait(3)

        open_readonly("https://kaizenep.com/events/list/All")
        current_url = page_info().get("url", "")
        if "auth.kaizenep.com" in current_url or "eportfolio.rcem.ac.uk" in current_url or "login" in current_url.lower():
            emit({{"status": "not_available", "reason": "login_required", "samples": []}})
            raise SystemExit

        rows = read_js(r'''
        (() => {{
          const text = el => (el && el.textContent ? el.textContent.trim().replace(/\\s+/g, ' ') : '');
          const rows = Array.from(document.querySelectorAll('.row.event-inner'));
          return rows.map(row => {{
            const a = row.querySelector('a[href*="/events/view"], a[router-link]');
            const href = a ? a.href : '';
            return {{
              href,
              title: text(row.querySelector('h2.entry-title, .entry-title') || a),
              text: text(row).slice(0, 1000)
            }};
          }}).filter(r => r.href && /\\/events\\/view/.test(r.href));
        }})()
        ''') or []

        samples = []
        seen = set()
        for row in rows[:MAX_ROWS]:
            href = row.get("href")
            if not href or href in seen:
                continue
            seen.add(href)
            try:
                open_readonly(href)
                detail = read_js(r'''
                (() => {{
                  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
                  const fields = Array.from(document.querySelectorAll('.form-text__form-group, .form-readonly__form-group, .form-group, .field-group')).map(g => {{
                    const label = clean((g.querySelector('.form-text__control-label, .control-label, label, dt') || {{}}).textContent);
                    const value = clean((g.querySelector('.form-text__field-value, .field-value, dd, p, textarea, .ng-binding') || {{}}).textContent);
                    return {{label, value}};
                  }}).filter(f => f.label && f.value);
                  return {{
                    heading: clean((document.querySelector('h1, .event-title, .page-title') || {{}}).textContent),
                    fields
                  }};
                }})()
                ''') or {{}}
                chunks = []
                for field in detail.get("fields", []):
                    label = (field.get("label") or "").lower()
                    value = (field.get("value") or "").strip()
                    if len(value) < 80:
                        continue
                    if any(skip in label for skip in ["attach", "file", "assessor", "supervisor"]):
                        continue
                    chunks.append(value[:2500])
                if chunks:
                    samples.append("\\n\\n".join(chunks[:4]))
                if len(samples) >= MAX_SAMPLES:
                    break
            except Exception:
                continue

        emit({{"status": "ok" if samples else "no_samples", "samples": samples}})
        """
    )


def _extract_payload(stdout: str) -> dict:
    start = stdout.find("VOICE_SAMPLER_JSON_START")
    end = stdout.find("VOICE_SAMPLER_JSON_END")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("sampler did not return JSON")
    payload = stdout[start + len("VOICE_SAMPLER_JSON_START"):end].strip()
    return json.loads(payload)


def _run_browser_harness(limit: int) -> dict:
    env = os.environ.copy()
    ws = _resolve_cdp_ws(env)
    if not ws:
        return {"status": "not_available", "reason": "managed_browser_unreachable", "samples": []}
    env["BU_CDP_WS"] = ws
    result = subprocess.run(
        [BROWSER_HARNESS, "-c", _browser_script(limit)],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )
    if result.returncode != 0:
        return {"status": "not_available", "reason": "browser_harness_failed", "samples": []}
    return _extract_payload(result.stdout)


def _restore_kaizen_session(telegram_user_id: int) -> dict:
    """Restore a logged-out managed browser session using saved credentials.

    This only authenticates the browser profile. It does not create, edit,
    submit, sign, delete, or share any Kaizen content.
    """
    try:
        from engine.providers.kaizen import KaizenInfrastructureError, KaizenProvider
        from store import get_credentials
    except Exception:
        return {"ok": False, "reason": "reconnect_unavailable"}

    try:
        credentials = get_credentials(telegram_user_id)
    except Exception:
        return {"ok": False, "reason": "credentials_unavailable"}
    if not credentials:
        return {"ok": False, "reason": "credentials_missing"}

    username, password = credentials
    provider = KaizenProvider(username=username, password=password)
    try:
        connected = provider.connect()
    except KaizenInfrastructureError:
        return {"ok": False, "reason": "reconnect_infrastructure_error"}
    except Exception:
        return {"ok": False, "reason": "reconnect_failed"}

    if not connected:
        return {"ok": False, "reason": "credentials_rejected"}
    return {"ok": True}


async def sample_kaizen_entries(
    telegram_user_id: int,
    window: SampleWindow,
) -> SamplerResult:
    """Read existing Kaizen entries from the managed browser session."""
    try:
        payload = await asyncio.to_thread(_run_browser_harness, _window_limit(window))
    except subprocess.TimeoutExpired:
        payload = {"status": "not_available", "reason": "timeout", "samples": []}
    except Exception:
        payload = {"status": "not_available", "reason": "unexpected_error", "samples": []}

    if payload.get("status") == "not_available" and payload.get("reason") == "login_required":
        reconnect = await asyncio.to_thread(_restore_kaizen_session, telegram_user_id)
        if reconnect.get("ok"):
            try:
                payload = await asyncio.to_thread(_run_browser_harness, _window_limit(window))
            except subprocess.TimeoutExpired:
                payload = {"status": "not_available", "reason": "timeout_after_reconnect", "samples": []}
            except Exception:
                payload = {"status": "not_available", "reason": "unexpected_error_after_reconnect", "samples": []}
        else:
            payload = {
                "status": "not_available",
                "reason": reconnect.get("reason") or "login_required",
                "samples": [],
            }

    status = payload.get("status")
    if status == "ok" and payload.get("samples"):
        return SamplerResult(
            status=SamplerStatus.OK,
            window=window,
            samples=[str(sample) for sample in payload.get("samples", []) if str(sample).strip()],
        )
    if status == "no_samples":
        return SamplerResult(status=SamplerStatus.NO_SAMPLES, window=window)

    reason = payload.get("reason") or "unavailable"
    message = (
        "I couldn't read Kaizen entries from the managed browser session just now. "
        "You can still add 3-5 examples manually while we reconnect Kaizen learning."
    )
    if reason in {"login_required", "credentials_missing", "credentials_unavailable", "credentials_rejected"}:
        message = (
            "Kaizen needs reconnecting before I can learn from previous entries. "
            "Reconnect Kaizen, then try this again — or add examples manually for now."
        )
    return SamplerResult(
        status=SamplerStatus.NOT_AVAILABLE,
        window=window,
        message=message,
        reason=reason,
    )
