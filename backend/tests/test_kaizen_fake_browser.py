"""Real Chromium + production filer, no AI or external network."""
import asyncio
import json
import http.client
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserType, Locator, Page, async_playwright, Error

import kaizen_form_filer as filer
from ai_declaration import DECLARATION_FIELD_PRIORITY, DEFAULT_DECLARATION_TEXT
from tests.kaizen_fake import FakeKaizen, HOSTS, PASSWORD, USERNAME, controls

pytestmark = [pytest.mark.kaizen_browser, pytest.mark.asyncio]


def synthetic_fields(form_type):
    fields = {}
    for spec in controls(form_type).values():
        key = spec["key"]
        if spec["kind"] == "date":
            value = "2026-02-03"
        elif key == "stage_of_training":
            value = "Higher"
        elif spec["kind"] == "widget":
            value = list(spec["options"][:2]) if key == "us_application" else list(spec["options"][1:3])
        elif spec["kind"] == "select":
            # ACCS placement's "Emergency Medicine" is normalised to
            # "Emergency Department" by the shared DOPS adapter. Use an
            # unambiguous offered placement to keep this a filling test.
            value = "Anaesthetics" if key == "placement" and "Anaesthetics" in spec["options"] else spec["options"][0]
        elif key == "event_description":
            value = "Synthetic browser regression"
        else:
            value = f"Synthetic fixture {key}"
        fields[key] = value
    # Exercise both aliases when two mapped keys target one control.
    for key, dom_id in {**filer.COMMON_HEADER_FIELD_MAP, **filer.FORM_FIELD_MAP[form_type]}.items():
        fields[key] = fields[controls(form_type)[dom_id]["key"]]
    return fields


def expected_values(form_type, fields):
    """Oracle from submitted fixture values, without calling filer transforms."""
    declared = next((key for key in DECLARATION_FIELD_PRIORITY if key in fields), None)
    expected = {}
    for dom_id, spec in controls(form_type).items():
        key = spec["key"]
        value = fields[key]
        if spec["kind"] == "date":
            value = "3/2/2026"
        elif key == "stage_of_training":
            value = dict(spec["options"])["Higher"]
        elif key == declared:
            value += "\n\n" + DEFAULT_DECLARATION_TEXT
        expected[dom_id] = value
    return expected


def local_response(fake, request, headers):
    parsed = urlsplit(request.url)
    connection = http.client.HTTPConnection("127.0.0.1", fake.server.server_port, timeout=5)
    try:
        headers = {key: value for key, value in headers.items() if key.lower() not in {"host", "content-length"}}
        headers["X-Fake-Host"] = parsed.hostname
        connection.request(request.method, parsed.path + ("?" + parsed.query if parsed.query else ""), body=request.post_data_buffer, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def chromium_path():
    # Query the installed executable once, without launching a browser.
    async with async_playwright() as pw:
        return Path(pw.chromium.executable_path)


@pytest_asyncio.fixture
async def fake_browser(monkeypatch, tmp_path, chromium_path):
    for name in ("PG_ENV", "PG_KAIZEN_OFFLINE"):
        monkeypatch.delenv(name, raising=False)
    for name in ("PG_AI_DECLARATION", "PG_AI_DECLARATION_LABEL", "PG_AI_DECLARATION_TEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(filer, "KAIZEN_USE_CDP", False)
    monkeypatch.setattr(filer, "_SESSION_DIR", tmp_path / "sessions")

    # Fake pages render synchronously. Remove only fixed human/Angular pacing;
    # locator actions, navigation, login, cache, field filling and save are real.
    real_sleep = asyncio.sleep
    async def yield_only(*_args, **_kwargs):
        await real_sleep(0)
    monkeypatch.setattr(filer, "asyncio", SimpleNamespace(sleep=yield_only))
    real_type = Locator.type
    async def fast_type(self, text, **kwargs):
        kwargs["delay"] = 0
        return await real_type(self, text, **kwargs)
    monkeypatch.setattr(Locator, "type", fast_type)
    real_goto = Page.goto
    async def ready_page(self, url, **kwargs):
        if kwargs.get("wait_until") == "networkidle":
            kwargs["wait_until"] = "load"
        return await real_goto(self, url, **kwargs)
    monkeypatch.setattr(Page, "goto", ready_page)
    # Bad credentials would normally wait 30s for a URL that never arrives.
    real_wait = Page.wait_for_url
    async def bounded_login_wait(self, url, **kwargs):
        kwargs["timeout"] = 500
        return await real_wait(self, url, **kwargs)
    monkeypatch.setattr(Page, "wait_for_url", bounded_login_wait)

    with FakeKaizen() as fake:
        blocked = []
        async def route(request_route):
            request = request_route.request
            parsed = urlsplit(request.url)
            if parsed.hostname not in HOSTS or parsed.scheme not in {"http", "https"} or parsed.port not in {None, 80, 443}:
                blocked.append(request.url)
                await request_route.abort("blockedbyclient")
                return
            status, headers, body = await asyncio.to_thread(local_response, fake, request, await request.all_headers())
            location = next((v for k, v in headers.items() if k.lower() == "location"), None)
            if status in {301, 302, 303, 307, 308} and location:
                # Playwright does not re-route a fulfilled HTTP redirect, so hop with a
                # page-level navigation instead; every hop still goes through this handler.
                headers = {k: v for k, v in headers.items() if k.lower() not in {"location", "content-length", "content-type"}}
                headers["Content-Type"] = "text/html; charset=utf-8"
                body = f"<script>location.replace({json.dumps(location)})</script>".encode()
                status = 200
            await request_route.fulfill(status=status, headers=headers, body=body)

        real_new_page = Browser.new_page
        async def routed_page(self, **kwargs):
            kwargs["service_workers"] = "block"
            page = await real_new_page(self, **kwargs)
            await page.context.route("**/*", route)
            await page.context.route_web_socket("**/*", lambda ws: ws.close())
            return page
        monkeypatch.setattr(Browser, "new_page", routed_page)
        real_new_context = Browser.new_context
        async def routed_context(self, **kwargs):
            kwargs["service_workers"] = "block"
            context = await real_new_context(self, **kwargs)
            await context.route("**/*", route)
            await context.route_web_socket("**/*", lambda ws: ws.close())
            return context
        monkeypatch.setattr(Browser, "new_context", routed_context)
        real_launch = BrowserType.launch
        async def isolated_launch(self, **kwargs):
            # An unrouted Chromium background request cannot escape either.
            kwargs["channel"] = "chromium"  # Use the executable checked above.
            kwargs["args"] = ["--disable-background-networking", "--host-resolver-rules=MAP * ~NOTFOUND"]
            return await real_launch(self, **kwargs)
        monkeypatch.setattr(BrowserType, "launch", isolated_launch)
        if not chromium_path.is_file():
            message = "Chromium missing; run python -m playwright install chromium"
            if os.environ.get("PG_REQUIRE_BROWSER") == "1":
                pytest.fail(message)
            pytest.skip(message)
        yield fake, blocked
        assert fake.submit_clicks == 0, "Draft filing must never send to an assessor"
        assert not blocked, f"Unexpected non-fake browser traffic: {blocked}"


@pytest.mark.parametrize("form_type", sorted(filer.FORM_FIELD_MAP))
async def test_each_mapped_form_saves_exact_values_without_submission(fake_browser, form_type):
    fake, _blocked = fake_browser
    fields = synthetic_fields(form_type)
    result = await filer.file_to_kaizen(form_type, fields, USERNAME, PASSWORD, telegram_user_id=99999999)
    assert result["status"] == "success", result
    assert result["skipped"] == []
    assert len(fake.drafts) == 1
    draft = fake.drafts[0]
    assert draft["values"] == expected_values(form_type, fields)
    assert result["saved_url"] == result["draft_url"] == draft["url"]
    assert urlsplit(result["draft_url"]).path == f'/events/fillin/{draft["doc_id"]}'
    assert fake.login_attempts == 1
    assert fake.submit_clicks == 0
    assert {host for host, _ in fake.requests} == HOSTS


async def test_wrong_password_returns_normal_login_failure(fake_browser):
    fake, _ = fake_browser
    result = await filer.file_to_kaizen("CBD", synthetic_fields("CBD"), USERNAME, "wrong-dummy-password", telegram_user_id=99999999)
    assert result["status"] == "failed"
    assert result["error"] == "Could not log in to Kaizen with your saved credentials. Use /settings to reconnect."
    assert fake.login_attempts == 1
    assert fake.drafts == []
    assert filer.load_session_state(99999999, USERNAME) is None


async def test_second_filing_replays_encrypted_session_in_new_browser(fake_browser):
    fake, _ = fake_browser
    for _ in range(2):
        result = await filer.file_to_kaizen("CBD", synthetic_fields("CBD"), USERNAME, PASSWORD, telegram_user_id=99999999)
        assert result["status"] == "success", result
    assert fake.login_attempts == 1
    assert len(fake.drafts) == 2
    assert len({d["doc_id"] for d in fake.drafts}) == 2
    assert all(d["values"] == expected_values("CBD", synthetic_fields("CBD")) for d in fake.drafts)
    assert filer.load_session_state(99999999, USERNAME)["cookies"]
    cache = filer._session_cache_path(99999999, USERNAME).read_bytes()
    assert b"fake_session" not in cache
    assert b"synthetic" not in cache


async def test_routes_abort_non_fake_hosts_and_socket_guard_stays_closed(fake_browser):
    import socket
    fake, blocked = fake_browser
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            with pytest.raises(Error, match="ERR_BLOCKED_BY_CLIENT"):
                await page.goto("https://example.invalid/must-never-leave")
            assert blocked == ["https://example.invalid/must-never-leave"]
            blocked.clear()  # This deliberate rejection is the expected proof.
        finally:
            await browser.close()
    with pytest.raises(pytest.fail.Exception, match="attempted a socket connection"):
        socket.create_connection(("example.invalid", 443))
    assert fake.requests == []


@pytest.mark.parametrize("form_type", ("CBD", "DOPS_2021", "REFLECT_LOG_2021", "MINI_CEX"))
async def test_operator_check_reopens_every_field_and_curriculum(fake_browser, monkeypatch, tmp_path, form_type):
    import credentials
    import kaizen_live_check as check
    fake, _ = fake_browser
    fake.include_curriculum = True
    monkeypatch.setenv("KAIZEN_LIVE_CHECK_APPROVED", "operator-own-account")
    requested = []
    def own_connection(user_id):
        requested.append(user_id)
        return USERNAME, PASSWORD
    monkeypatch.setattr(credentials, "get_credentials", own_connection)
    report, code = await check.run_check((form_type,))
    assert code == 0, report
    entry = report["forms"][0]
    assert entry["draft_url"] == fake.drafts[0]["url"]
    assert all(row["classification"] == "landed" for row in entry["fields"])
    assert any(row["field"] == "kc:SLO6 KC1" for row in entry["fields"])
    assert requested == [check.OPERATOR_USER_ID]
    assert fake.submit_clicks == 0
    assert len(fake.drafts) == 1  # Read-back never saves a second draft.
    # Reopened URL, not the page left in memory immediately after filling.
    assert sum(path.startswith("/events/fillin/") for _, path in fake.requests) >= 2


@pytest.mark.parametrize("replacement, classification", [("", "empty"), ("altered saved text", "mismatch")])
async def test_operator_check_detects_dropped_or_altered_persistence(fake_browser, monkeypatch, tmp_path, replacement, classification):
    import credentials
    import kaizen_live_check as check
    fake, _ = fake_browser
    fake.include_curriculum = True
    fake.saved_overrides[filer.FORM_FIELD_MAP["CBD"]["clinical_reasoning"]] = replacement
    monkeypatch.setenv("KAIZEN_LIVE_CHECK_APPROVED", "operator-own-account")
    monkeypatch.setattr(credentials, "get_credentials", lambda uid: (USERNAME, PASSWORD))
    report, code = await check.run_check(("CBD",))
    assert code == check.PARTIAL, report
    row = next(r for r in report["forms"][0]["fields"] if r["field"] == "clinical_reasoning")
    assert row["classification"] == classification
    check.write_report(report, tmp_path)
    assert f"clinical_reasoning: {classification}" in (tmp_path / "summary.md").read_text()
    assert USERNAME not in (tmp_path / "results.json").read_text()
    assert PASSWORD not in (tmp_path / "results.json").read_text()


@pytest.mark.parametrize("label", ("Save and Send to assessor", "Submit", "Save and sign"))
async def test_draft_save_never_clicks_an_input_button_labelled_send_or_submit(label):
    """An <input> has no inner text, so its value attribute must pass the same guard."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(
                f'<form onsubmit="return false"><input type="submit" value="{label}" '
                'onclick="window.clicked = true"></form>'
            )
            saved = await filer._try_save_selectors(page, ['input[type="submit"][value*="Save" i]', 'input[type="submit"]'], True)
            assert saved is False
            assert await page.evaluate("window.clicked === true") is False
        finally:
            await browser.close()


@pytest.mark.parametrize("namer", (
    '<span id="name">Save and send to assessor</span>',
    '<span id="name" aria-label="Send to assessor">Save</span>',
    '<label for="save" aria-label="Send to assessor">Save</label><span id="name">Save</span>',
))
async def test_draft_save_never_clicks_a_save_button_named_send_by_another_element(namer):
    """A button's visible "Save" can be overridden by an aria-labelledby name."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(
                namer +
                '<button id="save" type="button" aria-labelledby="name" onclick="window.clicked = true">Save</button>'
            )
            saved = await filer._try_save_selectors(page, ['#save'], True)
            assert saved is False
            assert await page.evaluate("window.clicked === true") is False
        finally:
            await browser.close()
