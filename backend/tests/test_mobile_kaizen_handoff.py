"""Security and lifecycle contract for the passwordless Kaizen connection.

No test in this module contacts Telegram, Kaizen, Cloudflare, BWS, or the
managed Chrome instance.  The real browser and tunnel are verified separately
after these deterministic boundaries are green.
"""

from __future__ import annotations

import hashlib
import inspect
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import kaizen_form_filer
from mobile_kaizen_handoff import (
    HandoffAlreadyPending,
    HandoffCapacityReached,
    HandoffStore,
    MobileBrowserManager,
    _authenticated_kaizen_url,
    create_app,
    normalise_browser_input,
    subject_key_for,
)


REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def _connect_request(telegram_user_id: int = 4242) -> dict:
    return {
        "telegram_user_id": telegram_user_id,
        "subject_key": subject_key_for(telegram_user_id),
    }


def test_store_replaces_a_users_unused_link_and_caps_pending_sessions():
    store = HandoffStore(max_pending=2)
    first = store.create(_connect_request(1))

    # Asking again replaces the unused link; the old one stops working.
    replacement = store.create(_connect_request(1))
    assert replacement.session_id != first.session_id
    assert store.exchange(first.token) is None
    assert store.get_by_id(first.session_id).request is None

    store.create(_connect_request(2))
    with pytest.raises(HandoffCapacityReached):
        store.create(_connect_request(3))


def test_store_will_not_replace_a_link_someone_is_signing_in_through():
    store = HandoffStore()
    created = store.create(_connect_request(1))
    record = store.get_by_viewer_token(store.exchange(created.token))
    assert store.claim_browser(record.session_id) is True

    with pytest.raises(HandoffAlreadyPending):
        store.create(_connect_request(1))


def test_store_keeps_only_token_digests_and_exchanges_link_once():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    store = HandoffStore(ttl=timedelta(minutes=10), clock=lambda: now)

    created = store.create(_connect_request())

    assert created.token not in repr(store)
    record = store.get_by_id(created.session_id)
    assert record is not None
    assert record.link_token_digest == hashlib.sha256(created.token.encode()).hexdigest()
    assert not hasattr(record, "link_token")

    viewer_token = store.exchange(created.token)
    assert viewer_token
    assert store.exchange(created.token) is None
    assert store.get_by_viewer_token(viewer_token).session_id == created.session_id


def test_expired_link_cannot_be_exchanged_and_payload_is_removed():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    current = {"value": now}
    store = HandoffStore(
        ttl=timedelta(minutes=10),
        clock=lambda: current["value"],
    )
    created = store.create(_connect_request())

    current["value"] = now + timedelta(minutes=11)

    assert store.exchange(created.token) is None
    record = store.get_by_id(created.session_id)
    assert record is not None
    assert record.status == "expired"
    assert record.request is None


def test_only_one_browser_can_claim_an_exchanged_session():
    store = HandoffStore()
    created = store.create(_connect_request())
    viewer = store.exchange(created.token)
    record = store.get_by_viewer_token(viewer)

    assert store.claim_browser(record.session_id) is True
    assert store.claim_browser(record.session_id) is False


def test_public_api_requires_internal_key_and_never_echoes_the_user():
    store = HandoffStore()
    app = create_app(
        store=store,
        internal_key="local-only-key",
        public_base_url="https://handoff.example.test",
        secure_cookies=True,
        browser_manager=None,
    )
    client = TestClient(app)

    denied = client.post("/internal/handoffs", json={"telegram_user_id": 4242})
    assert denied.status_code == 401

    created = client.post(
        "/internal/handoffs",
        headers={"X-Portfolio-Handoff-Key": "local-only-key"},
        json={"telegram_user_id": 4242},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["url"].startswith("https://handoff.example.test/handoff#")
    assert "4242" not in created.text

    token = body["url"].split("#", 1)[1]
    exchanged = client.post("/api/handoff/exchange", json={"token": token})
    assert exchanged.status_code == 200
    assert exchanged.headers["cache-control"] == "no-store"
    assert "pg_handoff=" in exchanged.headers["set-cookie"]
    assert "HttpOnly" in exchanged.headers["set-cookie"]
    assert "Secure" in exchanged.headers["set-cookie"]

    replay = client.post("/api/handoff/exchange", json={"token": token})
    assert replay.status_code == 410


def test_internal_create_refuses_invalid_users_and_any_extra_fields():
    app = create_app(
        store=HandoffStore(),
        internal_key="local-only-key",
        public_base_url="https://handoff.example.test",
        secure_cookies=True,
        browser_manager=None,
    )
    client = TestClient(app)
    headers = {"X-Portfolio-Handoff-Key": "local-only-key"}

    for payload in (
        {"telegram_user_id": 0},
        {"telegram_user_id": -5},
        {},
        {"telegram_user_id": 4242, "password": "hunter2"},
        {"telegram_user_id": 4242, "username": "someone"},
    ):
        response = client.post("/internal/handoffs", headers=headers, json=payload)
        assert response.status_code == 422, payload
        assert "hunter2" not in response.text


def test_handoff_page_has_no_store_security_headers_and_mobile_controls():
    app = create_app(
        store=HandoffStore(),
        internal_key="local-only-key",
        public_base_url="https://handoff.example.test",
        secure_cookies=True,
        browser_manager=None,
    )
    response = TestClient(app).get("/handoff")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "Connect Kaizen" in response.text
    assert "browser-screen" in response.text
    assert "keyboard-bridge" in response.text
    assert "does not store your password" in response.text
    # Never overclaim: typing does pass through our browser.
    assert "never see" not in response.text.lower()

    script = TestClient(app).get("/handoff/app.js")
    assert "fetch('/api/handoff/status'" in script.text
    stylesheet = TestClient(app).get("/handoff/app.css")
    assert "#loading[hidden]" in stylesheet.text


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"type": "click", "x": 120, "y": 400}, {"type": "click", "x": 120.0, "y": 400.0}),
        ({"type": "text", "text": "safe text"}, {"type": "text", "text": "safe text"}),
        ({"type": "key", "key": "Backspace"}, {"type": "key", "key": "Backspace"}),
        ({"type": "scroll", "delta_y": 9999}, {"type": "scroll", "delta_y": 1200.0}),
    ],
)
def test_browser_input_is_allowlisted_and_bounded(message, expected):
    assert normalise_browser_input(message, width=430, height=850) == expected


@pytest.mark.parametrize(
    "message",
    [
        {"type": "click", "x": -1, "y": 5},
        {"type": "click", "x": 500, "y": 5},
        {"type": "text", "text": "x" * 129},
        {"type": "key", "key": "Control+L"},
        {"type": "navigate", "url": "https://example.com"},
    ],
)
def test_browser_input_rejects_out_of_bounds_or_privileged_actions(message):
    assert normalise_browser_input(message, width=430, height=850) is None


@pytest.mark.asyncio
async def test_browser_manager_keeps_the_session_after_the_user_signs_in():
    class FakeContext:
        async def close(self):
            return None

    class FakePage:
        def __init__(self):
            self.url = "about:blank"
            self.context = FakeContext()
            self.mouse = type("Mouse", (), {})()
            self.keyboard = type("Keyboard", (), {})()

        async def set_viewport_size(self, viewport):
            assert viewport == {"width": 430, "height": 850}

        async def goto(self, url, **kwargs):
            assert url == "https://eportfolio.rcem.ac.uk"
            self.url = "https://kaizenep.com/activities"

    class FakePlaywright:
        async def stop(self):
            return None

    class FakeSocket:
        def __init__(self):
            self.messages = []

        async def send_json(self, payload):
            self.messages.append(payload)

        async def receive_json(self):
            await __import__("asyncio").Event().wait()

        async def close(self, code=None):
            return None

    page = FakePage()
    socket = FakeSocket()
    kept = {}

    async def connect_page():
        return page, FakePlaywright()

    async def keep_session(record, authenticated_page, playwright_handle):
        kept["session_id"] = record.session_id
        kept["user"] = record.request["telegram_user_id"]
        assert authenticated_page.url == "https://kaizenep.com/activities"
        return {"status": "connected"}

    store = HandoffStore()
    created = store.create(_connect_request(4242))
    viewer = store.exchange(created.token)
    record = store.get_by_viewer_token(viewer)
    manager = MobileBrowserManager(
        connect_page=connect_page,
        keep_session=keep_session,
    )

    await manager.serve(record, socket, store)

    assert kept == {"session_id": created.session_id, "user": 4242}
    assert record.status == "complete"
    assert record.request is None
    assert record.result == {"status": "connected"}
    assert [message.get("status") for message in socket.messages] == [
        "opening",
        "login",
        "saving",
        "complete",
    ]


@pytest.mark.asyncio
async def test_browser_manager_applies_only_normalised_inputs():
    events = []

    class Mouse:
        async def click(self, x, y):
            events.append(("click", x, y))

        async def wheel(self, x, y):
            events.append(("wheel", x, y))

    class Keyboard:
        async def insert_text(self, text):
            events.append(("text", text))

        async def press(self, key):
            events.append(("key", key))

    page = type("Page", (), {"mouse": Mouse(), "keyboard": Keyboard()})()
    manager = MobileBrowserManager()

    await manager._apply_input(page, {"type": "click", "x": 25, "y": 80})
    await manager._apply_input(page, {"type": "text", "text": "typed privately"})
    await manager._apply_input(page, {"type": "key", "key": "Enter"})
    await manager._apply_input(page, {"type": "navigate", "url": "https://evil.test"})

    assert events == [
        ("click", 25.0, 80.0),
        ("text", "typed privately"),
        ("key", "Enter"),
    ]


def test_mobile_browser_launch_is_isolated_from_the_shared_managed_chrome():
    source = inspect.getsource(MobileBrowserManager._default_connect_page)

    assert "_connect_cdp" not in source
    assert "chromium.launch" in source
    assert "headless=True" in source


@pytest.mark.parametrize(
    "url",
    [
        "https://kaizenep.com/#/login",
        "https://kaizenep.com/#/signin",
        "https://kaizenep.com/interaction/abc",
        "https://auth.kaizenep.com/",
        "https://kaizenep.com/",
    ],
)
def test_pre_login_kaizen_routes_never_count_as_signed_in(url):
    assert _authenticated_kaizen_url(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://kaizenep.com/activities",
        "https://kaizenep.com/#/dashboard",
        "https://kaizenep.com/events/list",
    ],
)
def test_known_kaizen_app_routes_count_as_signed_in(url):
    assert _authenticated_kaizen_url(url) is True


def test_bot_starts_the_sign_in_page_only_when_enabled_and_without_other_secrets():
    """run_local.sh owns the sign-in page's lifecycle. It must run on its own
    port (8100 is already published as another hostname), start only when the
    option is enabled, and get an empty environment plus the one key it needs."""
    script = (REPO_ROOT / "backend/run_local.sh").read_text(encoding="utf-8")
    block = script[script.index("CONNECT_PORT=8101"):script.index("# Start bot (foreground)")]

    assert 'pg_is_truthy "${PG_ENABLE_PASSWORDLESS_CONNECT:-}"' in block
    assert "env -i" in block
    assert 'FERNET_SECRET_KEY="$FERNET_SECRET_KEY"' in block
    assert "--host 127.0.0.1" in block
    assert "--no-access-log" in block
    for secret in ("TELEGRAM_BOT_TOKEN", "STRIPE", "GOOGLE_API_KEY", "PORTFOLIO_OUTBOUND"):
        assert secret not in block
    assert "8100" not in block


class _ClosedTracker:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_default_keep_session_saves_under_the_real_user_with_no_username(monkeypatch):
    calls = {}
    context = _ClosedTracker()
    page = type("Page", (), {"context": context})()

    async def fake_save(ctx, telegram_user_id, username=None):
        calls["save"] = (ctx, telegram_user_id, username)

    monkeypatch.setattr(kaizen_form_filer, "save_session_state", fake_save)
    monkeypatch.setattr(
        kaizen_form_filer,
        "load_session_state",
        lambda telegram_user_id, username=None: {"cookies": [{"name": "s"}]},
    )
    store = HandoffStore()
    record = store.get_by_id(store.create(_connect_request(4242)).session_id)

    result = await MobileBrowserManager()._default_keep_session(record, page, None)

    assert result == {"status": "connected"}
    assert calls["save"] == (context, 4242, None)
    assert context.closed is True


@pytest.mark.asyncio
async def test_default_keep_session_fails_when_the_session_did_not_persist(monkeypatch):
    # save_session_state swallows its own errors, so a missing read-back must
    # be reported as a failure rather than a successful connection.
    context = _ClosedTracker()
    page = type("Page", (), {"context": context})()

    async def fake_save(ctx, telegram_user_id, username=None):
        return None

    monkeypatch.setattr(kaizen_form_filer, "save_session_state", fake_save)
    monkeypatch.setattr(
        kaizen_form_filer, "load_session_state", lambda telegram_user_id, username=None: None
    )
    store = HandoffStore()
    record = store.get_by_id(store.create(_connect_request(4242)).session_id)

    result = await MobileBrowserManager()._default_keep_session(record, page, None)

    assert result["status"] == "failed"
    assert context.closed is True


def test_connected_session_is_where_the_filer_looks_when_it_has_no_password():
    """The whole design rests on this: a session saved by the link (no
    username) must be the one the filer replays when called without
    credentials (username="")."""
    assert kaizen_form_filer._session_cache_path(4242, "") == (
        kaizen_form_filer._session_cache_path(4242)
    )



def _load_proof_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "kaizen_passwordless_proof", REPO_ROOT / "scripts/kaizen_passwordless_proof.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_proof_test_draft_refuses_without_a_kept_session(monkeypatch):
    """With no kept session the filer must never be reached, so it can never
    fall through to a login attempt with empty credentials."""
    proof = _load_proof_script()
    monkeypatch.setattr(
        kaizen_form_filer, "load_session_state", lambda telegram_user_id, username=None: None
    )

    async def must_not_file(user_id):
        raise AssertionError("filer reached without a kept session")

    monkeypatch.setattr(proof, "_save_test_draft", must_not_file)

    assert proof.cmd_save_test_draft(4242) == 1


def test_proof_test_draft_is_labelled_synthetic_and_carries_no_credentials():
    proof = _load_proof_script()
    fields = proof._synthetic_cbd_fields()

    assert proof.TEST_DRAFT_MARKER in fields["clinical_reasoning"]
    assert not {"username", "password", "credentials"} & {key.lower() for key in fields}


def test_create_connect_link_refuses_a_non_local_service(monkeypatch, tmp_path):
    import mobile_kaizen_handoff as handoff

    key = tmp_path / "internal.key"
    key.write_text("k" * 40)
    monkeypatch.setenv("PG_KAIZEN_CONNECT_BROKER_URL", "https://evil.example")

    with pytest.raises(handoff.ConnectLinkUnavailable):
        handoff.create_connect_link(4242, key_path=key)


def test_create_connect_link_reports_a_stopped_service_safely(monkeypatch, tmp_path):
    import mobile_kaizen_handoff as handoff

    import urllib.error
    import urllib.request

    key = tmp_path / "internal.key"
    key.write_text("k" * 40)
    monkeypatch.delenv("PG_KAIZEN_CONNECT_BROKER_URL", raising=False)

    def refused(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", refused)

    with pytest.raises(handoff.ConnectLinkUnavailable) as raised:
        handoff.create_connect_link(4242, key_path=key, timeout=1)
    assert "k" * 40 not in str(raised.value)


def test_create_connect_link_returns_the_service_link(monkeypatch, tmp_path):
    import io
    import json as _json
    import urllib.request

    import mobile_kaizen_handoff as handoff

    key = tmp_path / "internal.key"
    key.write_text("k" * 40)
    monkeypatch.delenv("PG_KAIZEN_CONNECT_BROKER_URL", raising=False)
    sent = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        sent["url"] = request.full_url
        sent["key"] = request.get_header("X-portfolio-handoff-key")
        sent["body"] = _json.loads(request.data)
        return Response(_json.dumps(
            {"url": "https://connect.emgurus.com/handoff#t", "expires_in_seconds": 600}
        ).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    link = handoff.create_connect_link(4242, key_path=key)

    assert link.url == "https://connect.emgurus.com/handoff#t"
    assert sent == {
        "url": "http://127.0.0.1:8101/internal/handoffs",
        "key": "k" * 40,
        "body": {"telegram_user_id": 4242},
    }


def test_taps_outside_the_letterboxed_picture_are_ignored():
    from mobile_kaizen_handoff import HANDOFF_JS

    assert "Math.min(box.width / 430, box.height / 850)" in HANDOFF_JS
    assert "if (at)" in HANDOFF_JS
