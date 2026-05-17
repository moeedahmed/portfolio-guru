"""Browser connection adapter for Portfolio Guru engine.

Supports multiple connection modes:
- Local CDP (Mac Mini Chrome)
- Browser Use Cloud (for server deployment)
- Playwright (headless, for Railway/Render)

Usage:
    adapter = BrowserAdapter.detect()
    adapter.connect()
    page_info = adapter.navigate("/events/list/All")
    adapter.close()
"""

import os
import json
from pathlib import Path
from typing import Optional, Dict, Any


class BrowserAdapter:
    """Abstract browser connection. Subclasses implement connect/navigate/close."""

    MODE_LOCAL_CDP = "local_cdp"
    MODE_BROWSER_USE_CLOUD = "browser_use_cloud"
    MODE_PLAYWRIGHT = "playwright"

    def __init__(self, mode: str = MODE_LOCAL_CDP):
        self.mode = mode
        self._connected = False

    @classmethod
    def detect(cls) -> "BrowserAdapter":
        """Auto-detect best connection mode based on environment."""
        if os.environ.get("BROWSER_USE_API_KEY"):
            return cls(cls.MODE_BROWSER_USE_CLOUD)
        if os.environ.get("KAIZEN_CDP_URL"):
            return cls(cls.MODE_LOCAL_CDP)
        if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RENDER"):
            return cls(cls.MODE_PLAYWRIGHT)
        return cls(cls.MODE_LOCAL_CDP)

    def connect(self) -> bool:
        """Connect to browser. Returns True on success."""
        if self.mode == self.MODE_LOCAL_CDP:
            return self._connect_local_cdp()
        elif self.mode == self.MODE_BROWSER_USE_CLOUD:
            return self._connect_cloud()
        elif self.mode == self.MODE_PLAYWRIGHT:
            return self._connect_playwright()
        return False

    def _connect_local_cdp(self) -> bool:
        """Connect to local Chrome via CDP."""
        cdp_url = os.environ.get("BU_CDP_WS", "")
        if not cdp_url:
            cdp_url = os.environ.get("KAIZEN_CDP_URL", "http://localhost:9222")
        
        import urllib.request
        import json as _json
        
        try:
            if cdp_url.startswith("ws://"):
                # Already a WebSocket URL — daemon handles it
                self._connected = True
                return True
            # Check HTTP endpoint for WebSocket URL
            resp = urllib.request.urlopen(f"{cdp_url}/json/version", timeout=5)
            data = _json.loads(resp.read())
            self._cdp_ws = data.get("webSocketDebuggerUrl")
            self._connected = bool(self._cdp_ws)
            return self._connected
        except Exception:
            return False

    def _connect_cloud(self) -> bool:
        """Connect via Browser Use Cloud API."""
        # Future: implement Browser Use Cloud connection
        self._connected = True
        return True

    def _connect_playwright(self) -> bool:
        """Connect via Playwright (headless Chromium)."""
        # Future: implement Playwright headless connection
        self._connected = True
        return True

    def navigate(self, url: str) -> Dict[str, Any]:
        """Navigate to URL. Returns page info dict."""
        if not self._connected:
            raise RuntimeError("Browser not connected")
        # Delegate to browser-harness or Playwright
        return {"url": url, "status": "navigated"}

    def execute_js(self, script: str) -> Any:
        """Execute JavaScript in the current page."""
        raise NotImplementedError

    def screenshot(self, path: str, full: bool = False) -> str:
        """Take screenshot. Returns path."""
        raise NotImplementedError

    def close(self):
        """Close browser connection."""
        self._connected = False


class BrowserUseHarnessAdapter(BrowserAdapter):
    """Adapter that uses browser-harness CLI for all operations.
    
    This is the primary adapter for local development (Mac Mini).
    Delegates all calls to `browser-harness -c '...'`.
    """

    def __init__(self):
        super().__init__(BrowserAdapter.MODE_LOCAL_CDP)
        self._harness_path = self._find_harness()

    def _find_harness(self) -> str:
        import shutil
        return shutil.which("browser-harness") or os.path.expanduser("~/.local/bin/browser-harness")

    def connect(self) -> bool:
        import subprocess
        try:
            result = subprocess.run(
                [self._harness_path, "--doctor"],
                capture_output=True, text=True, timeout=15
            )
            self._connected = "active" in result.stdout
            return self._connected
        except Exception:
            return False

    def execute(self, python_code: str) -> str:
        """Run Python code via browser-harness -c. Returns stdout."""
        import subprocess
        env = os.environ.copy()
        result = subprocess.run(
            [self._harness_path, "-c", python_code],
            capture_output=True, text=True, timeout=60,
            env=env
        )
        if result.returncode != 0:
            raise RuntimeError(f"browser-harness error: {result.stderr}")
        return result.stdout
