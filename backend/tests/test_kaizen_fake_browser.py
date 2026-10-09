"""Real Chromium + production filer, no AI or external network."""
import asyncio
import json
import http.client
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserType, Locator, Page, async_playwright, Error

import kaizen_form_filer as filer
import kaizen_live_check as live_check
from ai_declaration import DECLARATION_FIELD_PRIORITY, DEFAULT_DECLARATION_TEXT
from tests.kaizen_fake import FakeKaizen, HOSTS, PASSWORD, USERNAME, controls

pytestmark = [pytest.mark.kaizen_browser, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def chromium_launch_state():
    return {"unavailable": None}


@pytest.fixture(autouse=True)
def optional_chromium_launch(monkeypatch, chromium_launch_state):
    """CI requires Chromium; an optional sandbox run reports launch limits."""
    real_launch = BrowserType.launch

    async def launch(self, **kwargs):
        required = os.environ.get("PG_REQUIRE_BROWSER") == "1"
        if chromium_launch_state["unavailable"] and not required:
            pytest.skip(chromium_launch_state["unavailable"])
        # Direct DOM safety tests also need background traffic closed, even
        # though their pages are set_content-only and have no remote resources.
        kwargs.setdefault("args", ["--disable-background-networking", "--host-resolver-rules=MAP * ~NOTFOUND"])
        try:
            return await real_launch(self, **kwargs)
        except Error as exc:
            if required:
                raise
            chromium_launch_state["unavailable"] = f"Chromium cannot launch in this environment: {str(exc).splitlines()[0]}"
            pytest.skip(chromium_launch_state["unavailable"])

    monkeypatch.setattr(BrowserType, "launch", launch)


def mapped_form_group(form_type):
    base = filer.filing_form_base(form_type)
    if base.startswith("MGMT_") or base in {"BUSINESS_CASE", "COST_IMPROVE", "EQUIP_SERVICE"}:
        return "management"
    if base in {"LAT", "ACAT", "ACAF", "STAT", "MSF", "CLIN_GOV"}:
        return "leadership"
    if base.startswith("TEACH") or base in {"SDL", "EDU_ACT", "FORMAL_COURSE", "JCF"}:
        return "teaching"
    if base in {"REFLECT_LOG", "ESLE_REFLECTION", "COMPLAINT", "SERIOUS_INC", "CRIT_INCIDENT", "PDP", "APPRAISAL"}:
        return "reflection"
    if base == "FILE_UPLOAD":
        return "upload"
    return "clinical-and-other"


MAPPED_FORM_CASES = [
    pytest.param(form_type, id=f"{mapped_form_group(form_type)}:{form_type}")
    for form_type in sorted(filer.FORM_FIELD_MAP)
]


@pytest.fixture
def mapped_bot_conversation(monkeypatch, tmp_path):
    """Real case/form/review handlers; only model and profile boundaries are fake."""
    import bot
    from models import CBDData, FormDraft, FormTypeRecommendation
    from tests.bot_simulator import BotSimulator
    from tests.helpers import isolate_bot_storage

    isolate_bot_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "has_credentials", lambda uid: True)
    monkeypatch.setattr(bot, "get_credentials", lambda uid: (USERNAME, PASSWORD))
    monkeypatch.setattr(bot, "get_training_level", lambda uid: "ST5")
    monkeypatch.setattr(bot, "get_voice_profile", lambda uid: "")
    monkeypatch.setattr(bot, "check_can_file", AsyncMock(return_value=(True, 0, -1, "beta")))
    monkeypatch.setattr(bot, "classify_intent", AsyncMock(return_value="case"))
    real_route_filing = bot.route_filing

    async def conversation(form_type):
        curriculum = "2021" if form_type.endswith("_2021") or form_type == "ESLE_PART1_2" else "2025"
        monkeypatch.setattr(bot, "get_curriculum", lambda uid: curriculum)
        fields = synthetic_fields(form_type)
        if form_type in {"CBD", "CBD_2021"}:
            fields["clinical_reasoning"] = "PG CHECK - synthetic test draft, safe to delete. Synthetic clinical reasoning."
            # CBDData has no wrapper Description/end date. The filer derives
            # Description from the first reasoning sentence and uses the
            # encounter date for both wrapper date controls.
            fields["event_description"] = "PG CHECK - synthetic test draft, safe to delete."
            draft = CBDData(**{key: fields[key] for key in (
                "date_of_encounter", "stage_of_training", "clinical_reasoning", "reflection",
            )}, clinical_setting="Emergency Department", patient_presentation="Synthetic chest pain",
                trainee_role="I assessed and managed this fictional case", level_of_supervision="Indirect")
        else:
            draft_fields = dict(fields)
            base = filer.filing_form_base(form_type)
            if base == "DOPS":
                draft_fields.update(clinical_setting=fields["placement"],
                                    indication="a synthetic indication",
                                    trainee_performance="I performed the synthetic procedure under supervision.")
                fields["case_observed"] = (
                    f"This DOPS concerned {fields['procedure_name']}, {fields['placement']}, performed for a synthetic indication."
                    "\n\nI performed the synthetic procedure under supervision."
                )
            elif base == "MINI_CEX":
                draft_fields["clinical_reasoning"] = "Synthetic clinical assessment"
                fields["patient_presentation"] += "\n\nClinical assessment: Synthetic clinical assessment"
            elif base == "SDL":
                choice = next(spec["options"][0] for spec in filer.FORM_SCHEMAS["SDL"]["fields"]
                              if spec["key"] == "learning_activity_type")
                draft_fields["learning_activity_type"] = [choice]
                fields["resource_details"] += f"\n\nLearning activity type: {choice}"
            elif base == "LAT":
                # These are synthetic doctor-provided details. Reflection has
                # a merge target; clinical_setting remains an unmapped field.
                draft_fields["reflection"] = "Synthetic reflection"
                draft_fields["clinical_setting"] = "Emergency Department"
                fields["clinical_reasoning"] += "\n\nReflection: Synthetic reflection"
            elif base in {"AUDIT", "RESEARCH"}:
                draft_fields["reflection"] = "Synthetic reflection"
            elif base == "QIAT":
                draft_fields["qi_journey_aspects"] = ["Creating Conditions"]
            draft = FormDraft(form_type=form_type, uuid=filer.FORM_UUIDS[form_type], fields=draft_fields)
        # Stub provider calls, retaining _analyse_selected_form's profile/date
        # adapters and all draft-review/save safety checks.
        monkeypatch.setattr(bot, "extract_cbd_data", AsyncMock(return_value=draft))
        monkeypatch.setattr(bot, "extract_form_data", AsyncMock(return_value=draft))
        monkeypatch.setattr(bot, "recommend_form_types", AsyncMock(return_value=[
            FormTypeRecommendation(form_type=form_type, rationale="Synthetic regression evidence", uuid=filer.FORM_UUIDS[form_type]),
        ]))
        sim = BotSimulator()
        sim.filing_results = []
        async def observed_filing(**kwargs):
            result = await real_route_filing(**kwargs)
            sim.filing_results.append(result)
            return result
        monkeypatch.setattr(bot, "route_filing", observed_filing)
        context = sim._make_context()
        text = (
            "PG CHECK - synthetic test draft, safe to delete. A fictional adult with "
            "chest pain was assessed in the emergency department. I escalated early "
            "and reviewed the outcome with the team. I learnt to communicate the "
            "plan clearly and will repeat an early structured review next time."
        )
        assert await bot.handle_case_input(sim._make_text_update(text), context) == bot.AWAIT_FORM_CHOICE
        assert "FORM|best" in {data for _, data in sim.get_last_buttons()}
        assert context.user_data["case_text"] == text
        assert await bot.handle_form_choice(sim._make_callback_update(f"FORM|{form_type}"), context) == bot.AWAIT_APPROVAL
        reviewed = bot._load_draft(context)
        assert context.user_data["chosen_form"] == form_type
        assert reviewed.form_type == ("CBD" if isinstance(draft, CBDData) else form_type)
        if isinstance(draft, CBDData):
            for key in ("date_of_encounter", "stage_of_training", "clinical_reasoning", "reflection"):
                assert getattr(reviewed, key) == fields[key], (form_type, key)
        else:
            assert {key: reviewed.fields.get(key) for key in draft_fields} == draft_fields, f"{form_type}: mapped fields changed before Save"
        assert "APPROVE|draft" in {data for _, data in sim.get_last_buttons()}
        assert bot._draft_gaps(context) == [], (form_type, bot._draft_gaps(context))
        assert "💾 Save to Kaizen" in {label for label, _ in sim.get_last_buttons()}, (form_type, sim.get_last_buttons())
        # Use the real, stamped callback from the review, preserving stale-save
        # protection rather than inventing an unbound Save callback.
        save_callback = next(
            button.callback_data
            for _, _, markup in reversed(sim.messages_sent) if markup
            for row in markup.inline_keyboard for button in row
            if (button.callback_data or "").startswith("APPROVE|draft")
        )
        return sim, context, fields, save_callback

    return conversation


@pytest.mark.parametrize("form_type", MAPPED_FORM_CASES)
async def test_each_mapped_form_conversation_reaches_ready_draft(mapped_bot_conversation, form_type):
    # Keep conversation coverage runnable even when Chromium is unavailable.
    await mapped_bot_conversation(form_type)


@pytest.mark.parametrize("form_type", MAPPED_FORM_CASES)
async def test_each_mapped_form_bot_save_persists_every_field_without_submission(
    fake_browser, mapped_bot_conversation, form_type,
):
    import bot
    from telegram.ext import ConversationHandler

    fake, _ = fake_browser
    sim, context, fields, save_callback = await mapped_bot_conversation(form_type)
    result = await bot.handle_approval_approve(sim._make_callback_update(save_callback), context)
    assert result == ConversationHandler.END, sim.messages_sent
    assert context.user_data.get("last_filing_status") in {"success", "partial"}, sim.messages_sent
    assert len(sim.filing_results) == 1
    filing = sim.filing_results[0]
    # Full schema details can reach a ready review even when Kaizen's verified
    # map has a documented gap. A partial save must name only that gap; every
    # mapped control is still checked against the independent oracle below.
    expected_unmapped = {
        "AUDIT": {"reflection"}, "RESEARCH": {"reflection"},
        "LAT": {"clinical_setting"}, "QIAT": {"qi_journey_aspects"},
    }.get(filer.filing_form_base(form_type), set())
    if filing["status"] == "partial":
        assert expected_unmapped, (form_type, filing)
        assert set(filing["skipped"]) == expected_unmapped, (form_type, filing)
    else:
        assert filing["status"] == "success", (form_type, filing)
        assert filing["skipped"] == [], (form_type, filing)
    assert len(fake.drafts) == 1
    assert filer.canonical_form_type(fake.drafts[0]["form_type"]) == filer.canonical_form_type(form_type)
    assert fake.drafts[0]["values"] == expected_values(form_type, fields)
    assert fake.submit_clicks == 0, f"{form_type}: forbidden Submit/Send/Sign click"
    assert fake.forbidden_clicks == [], f"{form_type}: forbidden clicks {fake.forbidden_clicks}"


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
    monkeypatch.setattr(Page, "wait_for_timeout", yield_only)
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
                pause = "await window.waitForFakeSaveRedirect();" if parsed.path.startswith("/save/") and getattr(fake, "save_redirect_release", None) else ""
                body = f"<script>(async () => {{{pause}location.replace({json.dumps(location)})}})()</script>".encode()
                status = 200
            await request_route.fulfill(status=status, headers=headers, body=body)

        # A fulfilled redirect is a JavaScript hop, not the HTTP navigation
        # Playwright normally waits for in click(). Pacing sleeps are removed
        # above, so wait for the actual saved-document page before the filer
        # reads page.url or begins post-save QA.
        real_click = Locator.click
        async def settled_submit(self, **kwargs):
            action = await self.evaluate("el => el.form && el.type === 'submit' ? (el.getAttribute('formaction') || el.form.getAttribute('action')) : null")
            result = await real_click(self, **kwargs)
            if action and action.startswith("/save/"):
                await self.page.wait_for_url("**/events/fillin/**", wait_until="load")
            return result
        monkeypatch.setattr(Locator, "click", settled_submit)

        real_new_page = Browser.new_page
        async def routed_page(self, **kwargs):
            kwargs["service_workers"] = "block"
            page = await real_new_page(self, **kwargs)
            async def wait_for_fake_redirect(_source):
                await fake.save_redirect_release.wait()
            await page.expose_binding("waitForFakeSaveRedirect", wait_for_fake_redirect)
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
        assert fake.forbidden_clicks == [], "Draft filing must never submit, sign or delete"
        assert not blocked, f"Unexpected non-fake browser traffic: {blocked}"


async def test_serious_incident_save_waits_for_deferred_redirect_before_readback(fake_browser, monkeypatch):
    """Hold the JS redirect until the save waiter starts; never rely on pacing."""
    fake, _ = fake_browser
    fake.save_redirect_release = asyncio.Event()
    real_wait = Page.wait_for_url
    async def release_redirect_when_waited(self, url, **kwargs):
        if url == "**/events/fillin/**":
            fake.save_redirect_release.set()
        return await real_wait(self, url, **kwargs)
    monkeypatch.setattr(Page, "wait_for_url", release_redirect_when_waited)
    real_verify = filer._verify_entry_saved
    async def verify_only_saved_page(page, form_type, fields=None):
        assert fake.save_redirect_release.is_set(), "Post-save verification raced the redirect"
        assert filer._saved_draft_url(page.url), "Read-back started on the transient redirect page"
        return await real_verify(page, form_type, fields)
    monkeypatch.setattr(filer, "_verify_entry_saved", verify_only_saved_page)
    form_type = "SERIOUS_INC_2021"
    fields = synthetic_fields(form_type)
    result = await filer.file_to_kaizen(form_type, fields, USERNAME, PASSWORD, telegram_user_id=99999999)
    assert result["status"] == "success", result
    assert result["saved_url"] == result["draft_url"] == fake.drafts[0]["url"]
    assert fake.drafts[0]["values"] == expected_values(form_type, fields)


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


@pytest.mark.parametrize("form_type", [
    pytest.param(form_type, id=f"{mapped_form_group(form_type)}:{form_type}")
    for form_type in live_check.mapped_forms()
])
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
    # Conditional fields outside the synthetic scenario (e.g. PROC_LOG "Other"
    # boxes) are reported not-applicable rather than filled.
    assert all(row["classification"] in {"landed", "not-applicable"}
               or (row["field"].startswith("tag:") and row["classification"] == "count-only")
               for row in entry["fields"])
    assert any(row["classification"] == "landed" for row in entry["fields"])
    assert any(row["field"] in {"kc:SLO6 KC1", "tag:SLO6 KC1"} for row in entry["fields"])
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


async def test_2021_curriculum_tree_ticks_the_unnumbered_capability_by_position():
    """2021 trees say "SLO6 Key Capability: ..." with no number and no stage word."""
    kcs = ("the clinical knowledge to identify when key EM practical emergency skills are indicated",
           "the knowledge and psychomotor skills to perform EM procedural skills safely",
           "will be able to supervise and guide colleagues in delivering procedural skills")
    rows = "".join(
        f'<li><input type="checkbox" id="kc{i}"><span class="ng-binding">SLO6 Key Capability: {text}</span></li>'
        for i, text in enumerate(kcs, 1)
    )
    html = (
        '<ul><li><input type="checkbox" id="slo6"><a class="ng-binding" href="#" '
        'onclick="document.getElementById(\'kcs\').hidden = false; return false">'
        'SLO6: Proficiently deliver key procedural skills needed in EM</a>'
        f'<ul id="kcs" hidden>{rows}</ul></li></ul>'
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html)
            ticked, errors = await filer._fill_curriculum_links(
                page, ["SLO6"], ["SLO6 KC2: the knowledge and psychomotor skills (2025 Update)"], "Higher",
            )
            assert errors == []
            assert len(ticked) == 1
            states = await page.evaluate("[...document.querySelectorAll('input')].map(c => c.id + ':' + c.checked)")
            assert states == ["slo6:false", "kc1:false", "kc2:true", "kc3:false"]
        finally:
            await browser.close()


async def test_unnumbered_capability_is_never_ticked_when_two_slo_branches_are_open():
    """Position is ambiguous across Intermediate and Higher branches, so nothing is ticked."""
    branch = lambda stage: (
        f'<li><span class="ng-binding">{stage}</span><ul>'
        + "".join(f'<li><input type="checkbox" id="{stage}{i}"><span class="ng-binding">SLO6 Key Capability: skill {i}</span></li>' for i in (1, 2))
        + "</ul></li>"
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(f"<ul>{branch('Intermediate')}{branch('Higher')}</ul>")
            result = await page.evaluate(filer.TICK_KC_FALLBACK_JS, "SLO6 KC2")
            assert not result.get("checked")
            assert await page.evaluate("[...document.querySelectorAll('input')].every(c => !c.checked)")
        finally:
            await browser.close()


async def test_unnumbered_capability_is_never_ticked_when_two_stages_offer_the_slo():
    """Intermediate and Higher both show "SLO6:"; only one branch's rows are rendered."""
    rows = "".join(
        f'<li><input type="checkbox" id="kc{i}"><span class="ng-binding">SLO6 Key Capability: skill {i}</span></li>'
        for i in (1, 2)
    )
    html = (
        '<ul><li><a class="ng-binding" href="#" id="inter">SLO6: procedures</a>'
        f'<ul>{rows}</ul></li>'
        '<li><a class="ng-binding" href="#" id="higher">SLO6: procedures</a></li></ul>'
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html)
            result = await page.evaluate(filer.TICK_KC_FALLBACK_JS, "SLO6 KC2")
            assert not result.get("checked")
            assert await page.evaluate("[...document.querySelectorAll('input')].every(c => !c.checked)")
        finally:
            await browser.close()


async def test_post_save_qa_reads_a_saved_draft_as_kaizen_renders_it():
    """Saved drafts drop the blank select option and collapse the curriculum tree."""
    html = """
      <select id="placement"><option value="string:a" selected>Emergency Medicine</option><option value="string:b">Anaesthetics</option></select>
      <select id="procedure"><option value="?" selected></option><option value="string:c">Paediatric sedation (ST3-ST6 2021)</option></select>
      <div kz-tree id="tree"></div>
      <script>
        const scope = {selected: ["kc2"], nodes: [{_id: "root", name: "Specialty Learning Outcomes - Higher (2021)", categories: [
          {_id: "slo6", name: "SLO6: Proficiently deliver key procedural skills needed in EM", categories: [
            {_id: "kc1", name: "SLO6 Key Capability: the clinical knowledge"},
            {_id: "kc2", name: "SLO6 Key Capability: the knowledge and psychomotor skills"},
            {_id: "kc3", name: "SLO6 Key Capability: will be able to supervise"}]}]}]};
        window.angular = {element: () => ({isolateScope: () => scope})};
      </script>"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html)
            qa = await filer._verify_filing_qa(
                page, "DOPS_2021",
                {"placement": "Emergency Department", "procedure_name": "Paediatric sedation", "key_capabilities": ["SLO6 KC2"]},
                {"placement": "placement", "procedure_name": "procedure"},
            )
            assert "placement" in qa["filled"]
            assert "procedure_name" in qa["empty_expected"]
            assert "kc:SLO6 KC2" in qa["filled"]
            assert await page.evaluate(filer._QA_READ_KC_JS, "SLO6 KC1") is False
        finally:
            await browser.close()


async def test_post_save_qa_never_confirms_a_capability_ticked_in_the_other_stage():
    """Two stages both list unnumbered SLO6 capabilities; a tick in one proves nothing."""
    html = """
      <div kz-tree id="tree"></div>
      <script>
        const branch = (stage, picked) => ({_id: stage, name: stage, categories: [{_id: stage + "slo6", name: "SLO6: procedures", categories: [
          {_id: stage + "kc1", name: "SLO6 Key Capability: the clinical knowledge"},
          {_id: stage + "kc2", name: "SLO6 Key Capability: the psychomotor skills"}]}]});
        const scope = {selected: ["Intermediatekc2"], nodes: [branch("Intermediate"), branch("Higher")]};
        window.angular = {element: () => ({isolateScope: () => scope})};
      </script>"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html)
            assert await page.evaluate(filer._QA_READ_KC_JS, "SLO6 KC2: the psychomotor skills") is False
        finally:
            await browser.close()


async def test_post_save_qa_never_confirms_a_capability_ticked_in_another_tree():
    """Same as above, but each stage is its own kz-tree."""
    html = """
      <div kz-tree class="t" data-stage="Intermediate"></div><div kz-tree class="t" data-stage="Higher"></div>
      <script>
        const branch = stage => [{_id: stage + "slo6", name: "SLO6: procedures", categories: [
          {_id: stage + "kc1", name: "SLO6 Key Capability: the clinical knowledge"},
          {_id: stage + "kc2", name: "SLO6 Key Capability: the psychomotor skills"}]}];
        const scopes = {Intermediate: {selected: ["Intermediatekc2"], nodes: branch("Intermediate")},
                        Higher: {selected: [], nodes: branch("Higher")}};
        window.angular = {element: el => ({isolateScope: () => scopes[el.dataset.stage]})};
      </script>"""
    import kaizen_live_check as check
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html)
            assert await page.evaluate(filer._QA_READ_KC_JS, "SLO6 KC2: the psychomotor skills") is False
            assert (await page.evaluate(check.READ_KC_JS, "SLO6 KC2")).get("value") is not True
        finally:
            await browser.close()
