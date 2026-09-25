"""Passwordless Kaizen connection: the clinician signs in, we keep the session.

The clinician opens a one-time link on their phone and signs into the real RCEM
login page inside an isolated browser running beside Portfolio Guru. Portfolio
Guru never stores the password. Once Kaizen reports an authenticated
application URL, the browser's session cookies are encrypted into the beta's
existing per-user session cache (``kaizen_form_filer.save_session_state``) --
the same cache the filer already replays before any login -- and the browser
is closed.

Keystrokes still pass through the Portfolio Guru-controlled browser, so the
honest claim is "your password is not stored", not "we never see it".

This module owns no Telegram token and exposes no bot route. Links are created
only through the loopback, key-protected ``/internal/handoffs`` endpoint.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import Cookie, FastAPI, Header, HTTPException, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict
from starlette.websockets import WebSocketDisconnect


VIEWPORT_WIDTH = 430
VIEWPORT_HEIGHT = 850
DEFAULT_TTL = timedelta(minutes=10)
DEFAULT_INTERNAL_KEY_FILE = (
    Path.home()
    / ".openclaw"
    / "data"
    / "portfolio-guru"
    / "mobile-handoff"
    / "internal.key"
)
DEFAULT_PUBLIC_URL_FILE = DEFAULT_INTERNAL_KEY_FILE.with_name("public-url")
VIEWER_COOKIE = "pg_handoff"
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class CreatedHandoff:
    session_id: str
    token: str
    expires_at: datetime


class HandoffAlreadyPending(RuntimeError):
    pass


class HandoffCapacityReached(RuntimeError):
    pass


@dataclass
class HandoffRecord:
    session_id: str
    subject_key: str
    link_token_digest: str
    created_at: datetime
    expires_at: datetime
    request: dict[str, Any] | None = field(repr=False)
    viewer_token_digest: str | None = None
    link_consumed: bool = False
    status: str = "created"
    result: dict[str, Any] | None = field(default=None, repr=False)
    error: str | None = None
    browser_active: bool = False


class HandoffStore:
    """In-memory, single-process handoff store.

    Raw link and viewer tokens are never retained.  Request data is cleared
    on expiry, completion, or failure so a service restart is a privacy-safe
    hard reset rather than a recovery mechanism.
    """

    def __init__(
        self,
        *,
        ttl: timedelta = DEFAULT_TTL,
        clock: Callable[[], datetime] = _utcnow,
        max_pending: int = 20,
    ) -> None:
        self.ttl = ttl
        self.clock = clock
        self.max_pending = max(1, max_pending)
        self._records: dict[str, HandoffRecord] = {}
        self._lock = threading.RLock()

    def create(self, request: dict[str, Any]) -> CreatedHandoff:
        now = self.clock()
        subject_key = str(request.get("subject_key") or "")
        with self._lock:
            self._prune_locked(now)
            pending = [
                record
                for record in self._records.values()
                if record.status not in {"complete", "failed", "expired"}
            ]
            if any(record.subject_key == subject_key for record in pending):
                raise HandoffAlreadyPending("this user already has a pending handoff")
            if len(pending) >= self.max_pending:
                raise HandoffCapacityReached("the handoff queue is full")
            session_id = secrets.token_urlsafe(18)
            token = secrets.token_urlsafe(32)
            record = HandoffRecord(
                session_id=session_id,
                subject_key=subject_key,
                link_token_digest=_digest(token),
                created_at=now,
                expires_at=now + self.ttl,
                request=json.loads(json.dumps(request)),
            )
            self._records[session_id] = record
        return CreatedHandoff(
            session_id=session_id,
            token=token,
            expires_at=record.expires_at,
        )

    def get_by_id(self, session_id: str) -> HandoffRecord | None:
        with self._lock:
            record = self._records.get(session_id)
            if record:
                self._expire_if_needed(record)
            return record

    def exchange(self, token: str) -> str | None:
        if not token:
            return None
        wanted = _digest(token)
        with self._lock:
            for record in self._records.values():
                self._expire_if_needed(record)
                if not hmac.compare_digest(record.link_token_digest, wanted):
                    continue
                if record.status == "expired" or record.link_consumed:
                    return None
                viewer_token = secrets.token_urlsafe(32)
                record.viewer_token_digest = _digest(viewer_token)
                record.link_consumed = True
                record.status = "ready"
                return viewer_token
        return None

    def get_by_viewer_token(self, token: str | None) -> HandoffRecord | None:
        if not token:
            return None
        wanted = _digest(token)
        with self._lock:
            for record in self._records.values():
                self._expire_if_needed(record)
                if record.viewer_token_digest and hmac.compare_digest(
                    record.viewer_token_digest, wanted
                ):
                    if record.status == "expired":
                        return None
                    return record
        return None

    def set_status(self, session_id: str, status: str) -> None:
        with self._lock:
            record = self._records[session_id]
            if record.status != "expired":
                record.status = status

    def claim_browser(self, session_id: str) -> bool:
        """Atomically reserve the one browser allowed for a handoff."""
        with self._lock:
            record = self._records.get(session_id)
            if record is None:
                return False
            self._expire_if_needed(record)
            if (
                record.status != "ready"
                or record.browser_active
                or record.request is None
            ):
                return False
            record.browser_active = True
            return True

    def complete(self, session_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            record = self._records[session_id]
            record.status = "complete"
            record.result = _public_result(result)
            record.request = None
            record.browser_active = False

    def fail(self, session_id: str, reason: str) -> None:
        with self._lock:
            record = self._records[session_id]
            record.status = "failed"
            record.error = reason[:240]
            record.request = None
            record.browser_active = False

    def _expire_if_needed(self, record: HandoffRecord) -> None:
        if record.status in {"complete", "failed", "expired"}:
            return
        if self.clock() >= record.expires_at:
            record.status = "expired"
            record.request = None
            record.result = None
            record.browser_active = False

    def _prune_locked(self, now: datetime) -> None:
        for record in self._records.values():
            self._expire_if_needed(record)
        stale_ids = [
            session_id
            for session_id, record in self._records.items()
            if record.status in {"complete", "failed", "expired"}
            and now >= record.expires_at + self.ttl
        ]
        for session_id in stale_ids:
            self._records.pop(session_id, None)


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    """Only the connection outcome is ever returned to the browser."""
    return {"status": str(result.get("status") or "failed")}


def subject_key_for(telegram_user_id: int) -> str:
    """Ownership key used to allow one pending link per user."""
    return _digest(f"telegram:{int(telegram_user_id)}")


_ALLOWED_KEYS = {
    "Backspace",
    "Delete",
    "Enter",
    "Escape",
    "Tab",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
}


def normalise_browser_input(
    message: Any,
    *,
    width: int = VIEWPORT_WIDTH,
    height: int = VIEWPORT_HEIGHT,
) -> dict[str, Any] | None:
    """Allow only the small input vocabulary needed to operate the login page."""
    if not isinstance(message, dict):
        return None
    kind = message.get("type")
    if kind == "click":
        try:
            x = float(message["x"])
            y = float(message["y"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (0 <= x <= width and 0 <= y <= height):
            return None
        return {"type": "click", "x": x, "y": y}
    if kind == "text":
        text = message.get("text")
        if not isinstance(text, str) or not text or len(text) > 128:
            return None
        return {"type": "text", "text": text}
    if kind == "key":
        key = message.get("key")
        if key not in _ALLOWED_KEYS:
            return None
        return {"type": "key", "key": key}
    if kind == "scroll":
        try:
            delta_y = float(message["delta_y"])
        except (KeyError, TypeError, ValueError):
            return None
        delta_y = max(-1200.0, min(1200.0, delta_y))
        return {"type": "scroll", "delta_y": delta_y}
    return None


class InternalHandoffRequest(BaseModel):
    # Extra keys are refused outright so nothing -- least of all a
    # credential -- can ride along with a link request.
    model_config = ConfigDict(extra="forbid")

    telegram_user_id: int


class ExchangeRequest(BaseModel):
    token: str


def _validate_internal_request(request: InternalHandoffRequest) -> None:
    if request.telegram_user_id <= 0:
        raise HTTPException(422, "a Telegram user id is required")


def _security_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; connect-src 'self' ws: wss:; "
        "style-src 'self'; script-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"
    )
    return response


def _base_url_provider(value: str | Callable[[], str]) -> Callable[[], str]:
    if callable(value):
        return value
    return lambda: value


def create_app(
    *,
    store: HandoffStore,
    internal_key: str,
    public_base_url: str | Callable[[], str],
    secure_cookies: bool,
    browser_manager: "MobileBrowserManager | None",
) -> FastAPI:
    app = FastAPI(title="Portfolio Guru mobile Kaizen handoff", docs_url=None, redoc_url=None)
    get_public_base_url = _base_url_provider(public_base_url)

    @app.middleware("http")
    async def add_security_headers(request, call_next):
        return _security_headers(await call_next(request))

    @app.exception_handler(RequestValidationError)
    async def refuse_without_echo(request, exc):
        # FastAPI's default 422 body echoes the rejected input back. A request
        # that wrongly carried a credential must never have it repeated.
        return _security_headers(
            JSONResponse({"detail": "invalid request"}, status_code=422)
        )

    @app.get("/health")
    async def health():
        return {"status": "ok", "surface": "mobile-kaizen-handoff-test"}

    @app.get("/handoff", response_class=HTMLResponse)
    async def handoff_page():
        return HANDOFF_HTML

    @app.get("/handoff/app.css", response_class=PlainTextResponse)
    async def handoff_css():
        return PlainTextResponse(HANDOFF_CSS, media_type="text/css")

    @app.get("/handoff/app.js", response_class=PlainTextResponse)
    async def handoff_js():
        return PlainTextResponse(HANDOFF_JS, media_type="application/javascript")

    @app.post("/internal/handoffs", status_code=201)
    async def create_handoff(
        request: InternalHandoffRequest,
        supplied_key: str | None = Header(
            default=None,
            alias="X-Portfolio-Handoff-Key",
        ),
    ):
        if not supplied_key or not hmac.compare_digest(supplied_key, internal_key):
            raise HTTPException(401, "unauthorised")
        _validate_internal_request(request)
        base_url = get_public_base_url().strip().rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise HTTPException(503, "public handoff URL is not ready")
        try:
            created = store.create(
                {
                    "telegram_user_id": request.telegram_user_id,
                    "subject_key": subject_key_for(request.telegram_user_id),
                }
            )
        except HandoffAlreadyPending as exc:
            raise HTTPException(409, str(exc)) from exc
        except HandoffCapacityReached as exc:
            raise HTTPException(429, str(exc)) from exc
        return {
            "url": f"{base_url}/handoff#{created.token}",
            "expires_in_seconds": int(store.ttl.total_seconds()),
        }

    @app.post("/api/handoff/exchange")
    async def exchange_handoff(request: ExchangeRequest):
        viewer_token = store.exchange(request.token)
        if not viewer_token:
            raise HTTPException(410, "this handoff link is invalid, expired, or already used")
        response = JSONResponse({"status": "ready"})
        response.set_cookie(
            VIEWER_COOKIE,
            viewer_token,
            max_age=int(store.ttl.total_seconds()),
            httponly=True,
            secure=secure_cookies,
            samesite="strict",
            path="/",
        )
        return response

    @app.get("/api/handoff/status")
    async def handoff_status(
        viewer_token: str | None = Cookie(default=None, alias=VIEWER_COOKIE),
    ):
        record = store.get_by_viewer_token(viewer_token)
        if record is None:
            raise HTTPException(401, "handoff session unavailable")
        return _status_payload(record)

    @app.websocket("/handoff/ws")
    async def handoff_socket(websocket: WebSocket):
        viewer_token = websocket.cookies.get(VIEWER_COOKIE)
        record = store.get_by_viewer_token(viewer_token)
        if record is None or browser_manager is None:
            await websocket.close(code=4401)
            return
        if not _origin_allowed(websocket.headers.get("origin"), get_public_base_url()):
            await websocket.close(code=4403)
            return
        await websocket.accept()
        await browser_manager.serve(record, websocket, store)

    return app


def _status_payload(record: HandoffRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": record.status}
    if record.status == "complete" and record.result is not None:
        payload["result"] = record.result
    elif record.status == "failed":
        payload["message"] = record.error or "The handoff could not complete."
    elif record.status == "expired":
        payload["message"] = "This handoff has expired. Return to Telegram for a new link."
    return payload


def _origin_allowed(origin: str | None, public_base_url: str) -> bool:
    if not origin:
        return False
    expected = urlparse(public_base_url)
    actual = urlparse(origin)
    return (
        actual.scheme == expected.scheme
        and actual.netloc == expected.netloc
        and bool(actual.netloc)
    )


class MobileBrowserManager:
    """Stream one isolated Playwright page and keep its session after login."""

    def __init__(
        self,
        *,
        login_url: str = "https://eportfolio.rcem.ac.uk",
        screenshot_interval: float = 0.65,
        connect_page: Callable[[], Any] | None = None,
        keep_session: Callable[[HandoffRecord, Any, Any], Any] | None = None,
        max_concurrent_browsers: int = 2,
    ) -> None:
        self.login_url = login_url
        self.screenshot_interval = screenshot_interval
        self._connect_page = connect_page or self._default_connect_page
        self._keep_session = keep_session or self._default_keep_session
        self._browser_slots = asyncio.Semaphore(max(1, max_concurrent_browsers))

    async def serve(
        self,
        record: HandoffRecord,
        websocket: WebSocket,
        store: HandoffStore,
    ) -> None:
        if not store.claim_browser(record.session_id):
            await websocket.send_json({"type": "status", "status": record.status})
            await websocket.close(code=4409)
            return
        slot_acquired = False
        if self._browser_slots.locked():
            store.set_status(record.session_id, "queued")
            await websocket.send_json({"type": "status", "status": "queued"})
        remaining = max(0.1, (record.expires_at - store.clock()).total_seconds())
        try:
            await asyncio.wait_for(self._browser_slots.acquire(), timeout=remaining)
            slot_acquired = True
        except TimeoutError:
            store.fail(record.session_id, "The temporary browser queue timed out.")
            await websocket.send_json({"type": "status", **_status_payload(record)})
            return
        store.get_by_id(record.session_id)
        if record.status == "expired" or record.request is None:
            await websocket.send_json({"type": "status", "status": "expired"})
            return
        store.set_status(record.session_id, "opening")
        page = None
        playwright_handle = None
        handed_over = False
        try:
            await websocket.send_json({"type": "status", "status": "opening"})
            page, playwright_handle = await self._connect_page()
            await page.set_viewport_size(
                {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT}
            )
            await page.goto(self.login_url, wait_until="load", timeout=30000)
            store.set_status(record.session_id, "login")
            await websocket.send_json({"type": "status", "status": "login"})

            receive_task = asyncio.create_task(websocket.receive_json())
            while store.clock() < record.expires_at:
                if _authenticated_kaizen_url(str(page.url)):
                    receive_task.cancel()
                    store.set_status(record.session_id, "saving")
                    await websocket.send_json({"type": "status", "status": "saving"})
                    result = await self._keep_session(record, page, playwright_handle)
                    handed_over = True
                    page = None
                    playwright_handle = None
                    if result.get("status") == "connected":
                        store.complete(record.session_id, result)
                        await websocket.send_json(
                            {"type": "status", **_status_payload(record)}
                        )
                    else:
                        store.fail(
                            record.session_id,
                            str(result.get("error") or "The Kaizen session could not be kept."),
                        )
                        await websocket.send_json(
                            {"type": "status", **_status_payload(record)}
                        )
                    return

                try:
                    screenshot = await page.screenshot(
                        type="jpeg",
                        quality=68,
                        animations="disabled",
                    )
                    await websocket.send_json(
                        {
                            "type": "frame",
                            "data": base64.b64encode(screenshot).decode(),
                            "width": VIEWPORT_WIDTH,
                            "height": VIEWPORT_HEIGHT,
                        }
                    )
                except Exception:
                    # Navigations can replace the page while a screenshot is in
                    # flight. The next frame is enough; never log page content.
                    pass

                done, _ = await asyncio.wait(
                    {receive_task},
                    timeout=self.screenshot_interval,
                )
                if receive_task in done:
                    message = receive_task.result()
                    await self._apply_input(page, message)
                    receive_task = asyncio.create_task(websocket.receive_json())

            store.fail(record.session_id, "The Kaizen login window expired.")
        except WebSocketDisconnect:
            store.fail(record.session_id, "The mobile login window was closed.")
        except Exception as exc:
            # First line only: Playwright's reason (e.g. a missing browser
            # binary) without any page content or typed input.
            reason = (str(exc).splitlines() or [""])[0][:200]
            logger.warning(
                "Mobile Kaizen handoff browser stopped: %s: %s",
                type(exc).__name__,
                reason,
            )
            store.fail(
                record.session_id,
                f"The temporary browser could not continue ({type(exc).__name__}).",
            )
            try:
                await websocket.send_json({"type": "status", **_status_payload(record)})
            except Exception:
                pass
        finally:
            record.browser_active = False
            if slot_acquired:
                self._browser_slots.release()
            if not handed_over:
                await _close_browser_page(page, playwright_handle)

    async def _apply_input(self, page: Any, message: Any) -> None:
        normalised = normalise_browser_input(message)
        if normalised is None:
            return
        kind = normalised["type"]
        if kind == "click":
            await page.mouse.click(normalised["x"], normalised["y"])
        elif kind == "text":
            await page.keyboard.insert_text(normalised["text"])
        elif kind == "key":
            await page.keyboard.press(normalised["key"])
        elif kind == "scroll":
            await page.mouse.wheel(0, normalised["delta_y"])

    async def _default_connect_page(self):
        from playwright.async_api import async_playwright

        playwright_handle = await async_playwright().start()
        browser = await playwright_handle.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT}
        )
        page = await context.new_page()
        return page, _OwnedBrowserHandle(playwright_handle, browser)

    async def _default_keep_session(
        self,
        record: HandoffRecord,
        page: Any,
        playwright_handle: Any,
    ) -> dict[str, Any]:
        """Encrypt the signed-in session into this user's Kaizen session cache.

        No username is recorded, so the session lands at the account-agnostic
        cache path the filer replays when it is called without credentials.
        ``save_session_state`` swallows its own errors, so the write is proven
        by reading it back rather than assumed.
        """
        import kaizen_form_filer

        telegram_user_id = int((record.request or {})["telegram_user_id"])
        try:
            await kaizen_form_filer.save_session_state(page.context, telegram_user_id)
        finally:
            await _close_browser_page(page, playwright_handle)
        state = kaizen_form_filer.load_session_state(telegram_user_id)
        if not state or not state.get("cookies"):
            return {"status": "failed", "error": "The Kaizen session could not be saved."}
        return {"status": "connected"}


class _OwnedBrowserHandle:
    def __init__(self, playwright_handle: Any, browser: Any) -> None:
        self.playwright_handle = playwright_handle
        self.browser = browser

    async def stop(self) -> None:
        try:
            await self.browser.close()
        finally:
            await self.playwright_handle.stop()


def _authenticated_kaizen_url(url: str) -> bool:
    from kaizen_form_filer import _is_kaizen_app_url, _is_kaizen_auth_url

    if not _is_kaizen_app_url(url) or _is_kaizen_auth_url(url):
        return False
    parsed = urlparse(url)
    route = f"{parsed.path}#{parsed.fragment}".lower()
    if any(
        marker in route
        for marker in ("login", "sign-in", "signin", "auth", "interaction")
    ):
        return False
    return any(
        marker in route
        for marker in ("activities", "dashboard", "events", "portfolio", "home")
    )


async def _close_browser_page(page: Any, playwright_handle: Any) -> None:
    if page is not None:
        try:
            await page.context.close()
        except Exception:
            pass
    if playwright_handle is not None:
        try:
            await playwright_handle.stop()
        except Exception:
            pass


def ensure_internal_key(path: Path = DEFAULT_INTERNAL_KEY_FILE) -> str:
    path = path.expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if len(value) < 32:
            raise RuntimeError("mobile handoff internal key is invalid")
        return value
    value = secrets.token_urlsafe(48)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value)
    return value


def _read_public_url(path: Path) -> str:
    try:
        return path.expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def create_default_app() -> FastAPI:
    key_path = Path(
        os.environ.get(
            "PG_MOBILE_HANDOFF_INTERNAL_KEY_FILE",
            str(DEFAULT_INTERNAL_KEY_FILE),
        )
    )
    public_url_file = Path(
        os.environ.get(
            "PG_MOBILE_HANDOFF_PUBLIC_URL_FILE",
            str(DEFAULT_PUBLIC_URL_FILE),
        )
    )
    secure_cookies = os.environ.get("PG_MOBILE_HANDOFF_SECURE_COOKIES", "1") != "0"
    configured_public_url = os.environ.get("PG_MOBILE_HANDOFF_PUBLIC_URL", "").strip()
    public_base_url: str | Callable[[], str]
    if configured_public_url:
        public_base_url = configured_public_url
    else:
        public_base_url = lambda: _read_public_url(public_url_file)
    return create_app(
        store=HandoffStore(),
        internal_key=ensure_internal_key(key_path),
        public_base_url=public_base_url,
        secure_cookies=secure_cookies,
        browser_manager=MobileBrowserManager(),
    )


HANDOFF_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Portfolio Guru · Connect Kaizen</title>
  <link rel="stylesheet" href="/handoff/app.css">
</head>
<body>
  <main>
    <section class="intro">
      <p class="eyebrow">PORTFOLIO GURU</p>
      <h1>Connect Kaizen</h1>
      <p>Sign in to Kaizen yourself in this temporary, isolated browser. Portfolio Guru does not store your password &mdash; it keeps only the signed-in session, so it can save drafts until Kaizen logs you out.</p>
    </section>
    <section class="browser-shell" aria-label="Temporary Kaizen browser">
      <div class="browser-bar"><span class="lock">●</span><span id="browser-label">Opening RCEM ePortfolio…</span></div>
      <div class="screen-wrap">
        <img id="browser-screen" alt="Live Kaizen login browser" draggable="false">
        <div id="loading" role="status" aria-live="polite">Preparing your secure browser…</div>
      </div>
      <div class="controls">
        <button id="backspace" type="button" aria-label="Delete previous character">⌫</button>
        <button id="tab" type="button">Next field</button>
        <button id="enter" type="button">Continue</button>
      </div>
    </section>
    <input id="keyboard-bridge" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" aria-label="Type into the selected Kaizen field">
    <section id="status-card" class="status-card" aria-live="polite">
      <strong id="status-title">Secure link ready</strong>
      <span id="status-copy">Tap the Kaizen field shown above, then type normally.</span>
    </section>
    <p class="privacy">Your typing passes through this browser to reach Kaizen but is never written down or logged. Portfolio Guru only ever saves drafts and never submits anything to a supervisor.</p>
  </main>
  <script src="/handoff/app.js" defer></script>
</body>
</html>
"""


HANDOFF_CSS = """
:root { color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #eef2ee; color: #102019; }
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; background: radial-gradient(circle at top, #f8fbf8 0, #e7eee9 55%, #dde7e0 100%); }
main { width: min(100%, 520px); margin: 0 auto; padding: max(24px, env(safe-area-inset-top)) 16px max(28px, env(safe-area-inset-bottom)); }
.intro { padding: 4px 4px 16px; }
.eyebrow { margin: 0 0 8px; color: #31705a; font-size: 12px; font-weight: 800; letter-spacing: .12em; }
h1 { margin: 0 0 8px; font-size: clamp(29px, 8vw, 40px); line-height: 1.05; letter-spacing: -.035em; }
.intro p:not(.eyebrow) { margin: 0; color: #45574e; font-size: 15px; line-height: 1.45; }
.browser-shell { overflow: hidden; border: 1px solid #c9d7ce; border-radius: 20px; background: #fff; box-shadow: 0 18px 50px rgba(26, 53, 40, .16); }
.browser-bar { display: flex; align-items: center; gap: 8px; height: 42px; padding: 0 14px; background: #f4f6f4; border-bottom: 1px solid #dce5df; color: #526158; font-size: 12px; }
.lock { color: #318b69; font-size: 10px; }
.screen-wrap { position: relative; width: 100%; aspect-ratio: 430 / 850; max-height: 62vh; overflow: hidden; background: #f7f8f7; touch-action: none; }
#browser-screen { display: block; width: 100%; height: 100%; object-fit: contain; user-select: none; -webkit-user-select: none; }
#loading { position: absolute; inset: 0; display: grid; place-items: center; padding: 30px; background: #f7f8f7; color: #4d5c53; text-align: center; }
#loading[hidden] { display: none; }
.controls { display: grid; grid-template-columns: 62px 1fr 1fr; gap: 8px; padding: 10px; border-top: 1px solid #e0e7e2; background: #f8faf8; }
button { min-height: 44px; border: 0; border-radius: 12px; padding: 0 13px; background: #e3ebe6; color: #183127; font: inherit; font-size: 14px; font-weight: 700; }
button:last-child { background: #1c6b50; color: #fff; }
#keyboard-bridge { position: fixed; left: -10000px; top: 0; width: 1px; height: 1px; opacity: .01; }
.status-card { display: flex; flex-direction: column; gap: 3px; margin-top: 14px; padding: 14px 16px; border-radius: 15px; background: #173f31; color: #fff; }
.status-card span { color: #d8e7df; font-size: 13px; line-height: 1.4; }
.privacy { margin: 13px 4px 0; color: #607168; font-size: 12px; line-height: 1.45; }
.done .browser-shell { display: none; }
.done .status-card { margin-top: 28px; padding: 24px; }
@media (max-height: 700px) { .screen-wrap { max-height: 52vh; } .intro p:not(.eyebrow) { font-size: 13px; } }
"""


HANDOFF_JS = r"""
(() => {
  const screen = document.getElementById('browser-screen');
  const loading = document.getElementById('loading');
  const keyboard = document.getElementById('keyboard-bridge');
  const title = document.getElementById('status-title');
  const copy = document.getElementById('status-copy');
  let socket;
  let pointerStart;

  const setStatus = (next, message) => {
    const states = {
      opening: ['Opening Kaizen', 'Preparing an isolated browser…'],
      queued: ['Browser queued', 'Another clinician is using the secure browser. Keep this page open.'],
      login: ['Sign in yourself', 'Tap a field above and type using your phone keyboard.'],
      saving: ['Login confirmed', 'Keeping your Kaizen session…'],
      complete: ['Kaizen connected', 'You can close this page and return to Telegram.'],
      failed: ['Connection stopped', message || 'Return to Telegram and request a new link.'],
      expired: ['Link expired', 'Return to Telegram and request a new one-time link.'],
    };
    const state = states[next] || ['Working…', message || 'Please keep this page open.'];
    title.textContent = state[0];
    copy.textContent = message || state[1];
    if (next === 'complete' || next === 'failed' || next === 'expired') document.body.classList.add('done');
  };

  const send = payload => {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
  };

  const connect = () => {
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = new WebSocket(`${scheme}//${location.host}/handoff/ws`);
    socket.onmessage = event => {
      const message = JSON.parse(event.data);
      if (message.type === 'frame') {
        screen.src = `data:image/jpeg;base64,${message.data}`;
        loading.hidden = true;
      } else if (message.type === 'status') {
        setStatus(message.status, message.message);
      }
    };
    socket.onclose = () => {
      if (!document.body.classList.contains('done')) setStatus('failed', 'The secure browser connection closed. Return to Telegram for a new link.');
    };
  };

  const exchange = async () => {
    const token = location.hash.slice(1);
    history.replaceState(null, '', '/handoff');
    if (!token) {
      const existing = await fetch('/api/handoff/status', {credentials: 'same-origin'});
      if (existing.ok) { connect(); return; }
      setStatus('expired');
      return;
    }
    const response = await fetch('/api/handoff/exchange', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token}),
    });
    if (!response.ok) { setStatus('expired'); return; }
    connect();
  };

  const point = event => {
    const touch = event.touches ? event.touches[0] : event;
    const box = screen.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(430, (touch.clientX - box.left) * 430 / box.width)),
      y: Math.max(0, Math.min(850, (touch.clientY - box.top) * 850 / box.height)),
    };
  };

  screen.addEventListener('pointerdown', event => { pointerStart = {point: point(event), y: event.clientY}; });
  screen.addEventListener('pointerup', event => {
    if (!pointerStart) return;
    const delta = event.clientY - pointerStart.y;
    if (Math.abs(delta) > 28) send({type: 'scroll', delta_y: -delta * 4});
    else { send({type: 'click', ...point(event)}); keyboard.focus({preventScroll: true}); }
    pointerStart = null;
  });
  keyboard.addEventListener('input', () => {
    if (keyboard.value) send({type: 'text', text: keyboard.value.slice(0, 128)});
    keyboard.value = '';
  });
  keyboard.addEventListener('keydown', event => {
    if (['Backspace', 'Enter', 'Tab', 'Escape'].includes(event.key)) {
      event.preventDefault();
      send({type: 'key', key: event.key});
    }
  });
  document.getElementById('backspace').addEventListener('click', () => send({type: 'key', key: 'Backspace'}));
  document.getElementById('tab').addEventListener('click', () => { send({type: 'key', key: 'Tab'}); keyboard.focus({preventScroll: true}); });
  document.getElementById('enter').addEventListener('click', () => send({type: 'key', key: 'Enter'}));
  exchange().catch(() => setStatus('failed', 'The secure link could not be opened. Return to Telegram and try again.'));
})();
"""
