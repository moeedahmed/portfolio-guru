"""One fail-closed boundary for the staging bot's real Kaizen effects."""
from __future__ import annotations

import os
import sys
from copy import deepcopy


def enabled() -> bool:
    return os.environ.get("PG_ENV") == "staging" or os.environ.get("PG_KAIZEN_OFFLINE", "").lower() in {"1", "true", "yes", "on"}


def require_online() -> None:
    if enabled():
        raise RuntimeError("test bot: offline Kaizen mode refuses real browser or HTTP access")


def async_playwright():
    require_online()
    from playwright.async_api import async_playwright as factory
    return factory()


def filing_result(fields: dict, *, submit: bool = False) -> dict:
    if submit:
        raise RuntimeError("offline test copy supports drafts only")
    return {
        "status": "success", "filled": list(fields), "skipped": [],
        "error": None, "method": "offline-test-copy", "offline": True,
        "fields": deepcopy(fields), "draft_url": None,
    }


_installed = False


def install_network_guard() -> None:
    """Catch residual browser/HTTP routes, including helper subprocesses.

    Installed only in offline mode before bot imports. Live gets no hook.
    Telegram, Vertex and BWS still work; Kaizen/RCEM and the sign-in broker,
    shared CDP and real browser drivers are refused even outside the router.
    """
    global _installed
    if _installed or not enabled():
        return

    def guard(event, args):
        if not enabled():
            return
        if event == "socket.getaddrinfo":
            host = str(args[0]).lower().rstrip(".")
            if any(host == domain or host.endswith("." + domain) for domain in (
                "kaizenep.com", "rcem.ac.uk", "connect.emgurus.com",
            )):
                require_online()
        elif event == "socket.connect":
            address = args[1]
            if isinstance(address, tuple) and len(address) > 1 and address[1] in (18800, 8101):
                require_online()
        elif event == "subprocess.Popen":
            command = str(args[0]).lower()
            if any(part in command for part in ("playwright", "chromium", "chrome", "browser-harness")):
                require_online()

    sys.addaudithook(guard)
    _installed = True
