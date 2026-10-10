"""Real Chromium + production filer, no AI or external network."""
import asyncio
import json
import http.client
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserType, Locator, Page, async_playwright, Error
from telegram.ext import ConversationHandler

import bot
import kaizen_form_filer as filer
import kaizen_live_check as live_check
from ai_declaration import DECLARATION_FIELD_PRIORITY, DEFAULT_DECLARATION_TEXT
from tests.bot_simulator import BotSimulator
from tests.helpers import isolate_bot_storage, unstamp
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
        # 9 Oct 2026: the fake provider may only structure reflection words
        # actually supplied by this synthetic doctor, just as in production.
        source_fields = bot._draft_fields_for_review(draft)
        reflection_values = [str(source_fields[key]) for key in
                             bot._find_reflection_keys(source_fields, form_type)
                             if source_fields.get(key)]
        text = "\n\n".join([text, *reflection_values])
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
    assert fake.drafts[0]["values"] == expected_values(form_type, fields, bot_save=True)
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


def expected_values(form_type, fields, bot_save=False):
    """Oracle from submitted fixture values, without calling filer transforms."""
    declared = next((key for key in DECLARATION_FIELD_PRIORITY if key in fields), None)
    if bot_save:
        # 10 Oct 2026: the bot puts RCEM's AI line on the first populated
        # reflective field only (REFLECT_LOG's Description is narrative).
        declared = next((key for key in bot._find_reflection_keys(fields, form_type)
                         if fields.get(key)), declared)
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
    # Locate the headless-only shell without launching a browser. The full
    # "Chrome for Testing" binary makes macOS raise camera/microphone/screen
    # permission prompts, so tests must never start it.
    async with async_playwright() as pw:
        full = Path(pw.chromium.executable_path)
    root = next((p.parent for p in full.parents if p.name.startswith("chromium-")), full.parent)
    shells = sorted(root.glob("chromium_headless_shell-*/*/chrome-headless-shell"))
    return shells[-1] if shells else root / "chromium_headless_shell-missing"


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
            # No channel: Playwright's headless shell, never the full Chrome for Testing.
            kwargs.pop("channel", None)
            kwargs["args"] = [
                "--disable-background-networking",
                "--host-resolver-rules=MAP * ~NOTFOUND",
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ]
            return await real_launch(self, **kwargs)
        monkeypatch.setattr(BrowserType, "launch", isolated_launch)
        if not chromium_path.is_file():
            message = "Headless shell missing; run python -m playwright install chromium-headless-shell"
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


# Login/link journeys stay in this browser lane so its existing fail-closed
# loopback/DNS/network guard applies without widening a safety allowlist.
LOGIN_CASE = "Synthetic case: chest pain assessed with ECG and troponin, escalated for senior review."
CONNECT_BUTTONS = [
    ("🔑 Share my login (recommended)", "ACTION|setup_password"),
    ("🔒 Sign in on Kaizen's page", "ACTION|connect_passwordless"),
    ("❌ Cancel", "ACTION|cancel"),
]
REJECT_BUTTONS = [CONNECT_BUTTONS[1], CONNECT_BUTTONS[2]]


def login_step(sim, *fragments, buttons=()):
    """Read the latest rendered message, including an explicitly empty keyboard.

    get_last_buttons searches backwards past removed keyboards, which would
    incorrectly bless stale controls in precisely these onboarding journeys.
    """
    _kind, text, markup = sim.messages_sent[-1]
    assert text, sim.messages_sent
    for fragment in fragments:
        assert fragment in text, text
    actual = [(b.text, unstamp(b.callback_data) if b.callback_data else b.url)
              for row in markup.inline_keyboard for b in row] if markup else []
    assert actual == list(buttons), (text, actual)


@pytest_asyncio.fixture
async def login_journey(fake_browser, monkeypatch, tmp_path, caplog):
    import profile_store
    from models import FormTypeRecommendation

    fake, _ = fake_browser
    caplog.set_level(logging.DEBUG)
    isolate_bot_storage(monkeypatch, tmp_path)
    # Restore the real reader removed by the legacy suite's autouse default;
    # it now reads only our synthetic in-memory profile database.
    from sqlmodel import Session, select
    def connection(uid):
        with Session(profile_store.engine) as session:
            row = session.exec(select(profile_store.UserProfile).where(
                profile_store.UserProfile.telegram_user_id == uid)).first()
            return row.kaizen_connection if row else None
    monkeypatch.setattr(profile_store, "get_kaizen_connection", connection)
    monkeypatch.setenv("PG_ENABLE_PASSWORDLESS_CONNECT", "1")
    monkeypatch.setenv("PG_PASSWORDLESS_ALLOWLIST", "99999999")
    monkeypatch.setattr(bot, "recommend_form_types", AsyncMock(return_value=[
        FormTypeRecommendation(form_type="CBD", rationale="Synthetic case discussion", uuid=filer.FORM_UUIDS["CBD"]),
    ]))
    monkeypatch.setattr(bot, "check_can_file", AsyncMock(return_value=(True, 0, 5, "free")))
    # Replace only the CDP transport: no shared Chrome, no real login probe
    # shortcuts. _login, role detection, session cache and bot handlers run.
    async def isolated_page():
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            return page, pw
        except BaseException:
            await pw.stop()
            raise
    monkeypatch.setattr(filer, "_connect_cdp", isolated_page)
    # Prove the browser can start before the bot's error handling (or xfail)
    # can mistake a sandbox launch failure for a product/login failure.
    page, pw = await isolated_page()
    await page.context.close()
    await pw.stop()
    sim = BotSimulator()
    ctx = sim._make_context()
    ctx.job_queue = MagicMock()
    yield SimpleNamespace(fake=fake, sim=sim, ctx=ctx)
    for _kind, text, markup in sim.messages_sent:
        assert PASSWORD not in (text or "")
        assert "wrong-dummy-password" not in (text or "")
        assert PASSWORD not in str(markup)
    assert PASSWORD not in caplog.text
    assert "wrong-dummy-password" not in caplog.text
    assert PASSWORD not in repr(ctx.user_data)


async def journey_call(journey, handler, value, *, callback=False):
    sim = journey.sim
    update = sim._make_callback_update(value) if callback else sim._make_text_update(value)
    if callback:
        update.callback_query.message.reply_markup = sim.messages_sent[-1][2] if sim.messages_sent else None
    else:
        update.message.delete = AsyncMock(side_effect=sim._capture_delete)
    return await handler(update, journey.ctx)


async def password_choice(journey, *, first_case=False):
    handler, text = (bot.handle_case_input, LOGIN_CASE) if first_case else (bot.start, "/start")
    assert await journey_call(journey, handler, text) == bot.AWAIT_USERNAME
    login_step(journey.sim, "Connect Kaizen", "Stored encrypted, deleted with /reset", buttons=CONNECT_BUTTONS)
    assert await journey_call(journey, bot.setup_password_start, "ACTION|setup_password", callback=True) == bot.AWAIT_USERNAME
    login_step(journey.sim, "Share my Kaizen login", "username (email)")
    assert journey.sim.messages_sent[-1][0] == "bot_edit"


async def password_submit(journey, username=USERNAME, password=PASSWORD):
    assert await journey_call(journey, bot.setup_username, username) == bot.AWAIT_PASSWORD
    login_step(journey.sim, "What's your Kaizen password?", "delete your password message")
    assert journey.sim.messages_sent[-1][0] == "bot_edit"
    return await journey_call(journey, bot.setup_password, password)


async def connected_case(journey):
    assert await journey_call(journey, bot.handle_case_input, LOGIN_CASE) == bot.AWAIT_FORM_CHOICE
    login_step(journey.sim, "Best fit:", "Select a form to draft it.", buttons=[
        ("🩺 CBD", "FORM|best"), ("📋 Forms", "FORM|show_all"), ("❌ Cancel", "CANCEL|form"),
    ])
    assert journey.ctx.user_data["case_text"] == LOGIN_CASE


async def test_login_journey_start_share_login_to_best_fit(login_journey):
    j = login_journey
    await password_choice(j)
    assert await password_submit(j) == ConversationHandler.END
    login_step(j.sim, "Kaizen connected", "HST", "Send")
    assert bot.get_credentials(j.sim.user_id) == (USERNAME, PASSWORD)
    assert j.fake.login_attempts == 1
    await connected_case(j)


async def test_login_journey_first_case_continues_after_linking(login_journey):
    j = login_journey
    await password_choice(j, first_case=True)
    await password_submit(j)
    assert "Best fit:" in j.sim.messages_sent[-1][1]
    assert j.ctx.user_data["case_text"] == LOGIN_CASE


async def test_login_journey_wrong_password_then_correct(login_journey):
    j = login_journey
    await password_choice(j)
    assert await password_submit(j, password="wrong-dummy-password") == bot.AWAIT_USERNAME
    login_step(j.sim, "didn't accept that email and password", "Type your Kaizen email to try again", buttons=REJECT_BUTTONS)
    assert not bot.has_credentials(j.sim.user_id)
    assert j.fake.login_attempts == 1
    assert await password_submit(j) == ConversationHandler.END
    login_step(j.sim, "Kaizen connected", "Send")
    assert j.fake.login_attempts == 2
    await connected_case(j)


async def test_login_journey_repeated_rejections_can_cancel(login_journey):
    j = login_journey
    await password_choice(j)
    for attempt in range(1, 4):
        assert await password_submit(j, password="wrong-dummy-password") == bot.AWAIT_USERNAME
        login_step(j.sim, "Type your Kaizen email to try again", buttons=REJECT_BUTTONS)
        assert j.fake.login_attempts == attempt  # no hidden or unbounded retry
        assert bot._load_setup_retry_credentials(j.ctx) is None
    assert await journey_call(j, bot.setup_cancel, "ACTION|cancel", callback=True) == ConversationHandler.END
    login_step(j.sim, "Setup cancelled", "Connect Kaizen to start filing", buttons=[
        ("🔗 Connect Kaizen", "ACTION|setup"),
    ])
    assert not bot.has_credentials(j.sim.user_id)
    assert j.fake.login_attempts == 3


async def test_login_journey_typo_address_rejected_then_corrected(login_journey):
    j = login_journey
    await password_choice(j)
    assert await password_submit(j, username="synthetic-docter@example.invalid") == bot.AWAIT_USERNAME
    login_step(j.sim, "didn't accept that email and password", "Type your Kaizen email", buttons=REJECT_BUTTONS)
    assert await password_submit(j) == ConversationHandler.END
    login_step(j.sim, "Kaizen connected", "Send")
    await connected_case(j)


async def test_login_journey_email_first_message_connects(login_journey):
    j = login_journey
    assert await journey_call(j, bot.handle_case_input, USERNAME) == bot.AWAIT_PASSWORD
    login_step(j.sim, "What's your Kaizen password?", "delete your password message")
    assert await journey_call(j, bot.setup_password, PASSWORD) == ConversationHandler.END
    login_step(j.sim, "Kaizen connected", "Send")
    await connected_case(j)


@pytest.mark.parametrize("landing", [
    "https://auth.kaizenep.com/verification",
    "https://kaizenep.com/welcome",
])
async def test_login_journey_unexpected_sign_in_page_refuses_success(login_journey, landing):
    j = login_journey
    j.fake.login_landing = landing
    await password_choice(j)
    state = await password_submit(j)
    # A same-host non-portfolio page must not pass the broad URL matcher.
    assert state == bot.AWAIT_PASSWORD
    login_step(j.sim, "Couldn't reach Kaizen to verify the login", "Select Retry", buttons=[
        ("🔄 Retry sign-in", "ACTION|retry_setup_login"), ("❌ Cancel", "ACTION|cancel"),
    ])
    assert not bot.has_credentials(j.sim.user_id)
    assert not any("Kaizen connected" in (text or "") for _, text, _ in j.sim.messages_sent)
    assert j.fake.login_attempts == 1


@pytest_asyncio.fixture
async def handoff_journey(login_journey, monkeypatch):
    """Real handoff/session engine; only its HTTP broker is in-process."""
    import mobile_kaizen_handoff as handoff
    j = login_journey
    store = handoff.HandoffStore()
    links = []
    async def create(uid):
        link = store.create({"telegram_user_id": uid, "subject_key": str(uid)})
        links.append(link)
        return handoff.ConnectLink(url="https://connect.invalid/handoff#" + link.token,
                                   expires_in_seconds=600, session_id=link.session_id)
    monkeypatch.setattr(bot, "_create_passwordless_link", create)
    monkeypatch.setattr(handoff, "connect_link_status", lambda sid: store.get_by_id(sid).status)
    async def sign_in():
        link = links[-1]
        record = store.get_by_viewer_token(store.exchange(link.token))
        class Socket:
            def __init__(self):
                self.sent_credentials = False
                self.messages = []
            async def send_json(self, value):
                self.messages.append(value)
            async def receive_json(self):
                if not self.sent_credentials:
                    self.sent_credentials = True
                    return {"type": "credentials", "username": USERNAME, "password": PASSWORD}
                await asyncio.Event().wait()
            async def close(self, **_kwargs):
                pass
        socket = Socket()
        await asyncio.wait_for(handoff.MobileBrowserManager(screenshot_interval=.01).serve(record, socket, store), 10)
        assert record.status == "complete", record.error
        assert PASSWORD not in json.dumps(socket.messages)
        j.ctx.job = SimpleNamespace(data={"session_id": link.session_id}, schedule_removal=MagicMock())
        await bot._passwordless_watch_job(j.ctx)
    j.sign_in = sign_in
    j.links = links
    j.handoff_store = store
    return j


async def passwordless_choice(j):
    assert await journey_call(j, bot.start, "/start") == bot.AWAIT_USERNAME
    login_step(j.sim, "Connect Kaizen", buttons=CONNECT_BUTTONS)
    assert await journey_call(j, bot.passwordless_setup_start, "ACTION|connect_passwordless", callback=True) == ConversationHandler.END
    login_step(j.sim, "Open Kaizen sign-in", "confirm here", buttons=[
        ("🔒 Open Kaizen sign-in", "https://connect.invalid/handoff#" + j.links[-1].token),
        ("🔑 Share my login (recommended)", "ACTION|setup_password"), ("❌ Cancel", "ACTION|cancel"),
    ])


async def test_login_journey_password_free_browser_to_best_fit(handoff_journey):
    j = handoff_journey
    await passwordless_choice(j)
    await j.sign_in()
    login_step(j.sim, "Kaizen connected", "HST", "Send")
    assert bot.kaizen_connection.is_passwordless(j.sim.user_id)
    assert not bot.has_credentials(j.sim.user_id)
    assert filer.load_session_state(j.sim.user_id)["cookies"]
    await connected_case(j)


async def test_login_journey_password_free_browser_never_finishes(handoff_journey):
    import mobile_kaizen_handoff as handoff
    j = handoff_journey
    await passwordless_choice(j)
    store = j.handoff_store
    link = j.links[-1]
    record = store.get_by_viewer_token(store.exchange(link.token))
    clock = {"now": datetime.now(timezone.utc)}
    store.clock = lambda: clock["now"]
    manager = handoff.MobileBrowserManager()
    open_page = manager._connect_page
    opened = {}
    async def capture_page():
        page, pw = await open_page()
        opened["page"] = page
        return page, pw
    manager._connect_page = capture_page
    class UnfinishedSignIn:
        async def send_json(self, value):
            if value.get("status") == "login":
                await opened["page"].locator('input[name="login"]').wait_for(state="visible")
                clock["now"] = record.expires_at + timedelta(seconds=1)
        async def receive_json(self):
            return {}  # No credentials entered; expires on the actual login page.
        async def close(self, **_kwargs):
            pass
    await asyncio.wait_for(manager.serve(record, UnfinishedSignIn(), store), 10)
    j.ctx.job = SimpleNamespace(data={"session_id": link.session_id}, schedule_removal=MagicMock())
    await bot._passwordless_watch_job(j.ctx)
    login_step(j.sim, "expired before Kaizen connected", "Get a new link", buttons=[
        ("🔁 Get a new link", "ACTION|passwordless_link"), CONNECT_BUTTONS[0], CONNECT_BUTTONS[2],
    ])
    assert not bot._kaizen_connected(j.sim.user_id)
    assert j.fake.login_attempts == 0
    assert ("auth.kaizenep.com", "/sign-in") in j.fake.requests


@pytest.mark.parametrize("session_failure", ["expired", "bounced"])
async def test_login_journey_session_failure_reconnect_resumes_save(handoff_journey, session_failure):
    j = handoff_journey
    await passwordless_choice(j)
    await j.sign_in()
    login_step(j.sim, "Kaizen connected", "Send")
    draft = {"_type": "FORM", "form_type": "CBD", "uuid": filer.FORM_UUIDS["CBD"], "fields": synthetic_fields("CBD")}
    j.ctx.user_data["draft_data"] = draft
    j.ctx.user_data["case_text"] = LOGIN_CASE + " I learned to escalate chest pain early; next time I will document my reasoning."
    source_fields = draft["fields"]
    reflection_values = [str(source_fields[key]) for key in
                         bot._find_reflection_keys(source_fields, "CBD")
                         if source_fields.get(key)]
    j.ctx.user_data["case_text"] = "\n\n".join([j.ctx.user_data["case_text"], *reflection_values])
    j.ctx.user_data["case_user_text"] = [j.ctx.user_data["case_text"]]
    approval = bot._build_approval_keyboard(context=j.ctx)
    approve_callback = approval.inline_keyboard[0][0].callback_data
    assert unstamp(approve_callback) == "APPROVE|draft" and approve_callback != "APPROVE|draft"
    if session_failure == "expired":
        j.fake.accept_sessions = False
    else:
        j.fake.form_bounces = -1
    assert await journey_call(j, bot.handle_approval_approve, approve_callback, callback=True) == bot.AWAIT_APPROVAL
    login_step(j.sim, "Kaizen has signed you out", "Your draft is kept", "Tap below to sign in again", buttons=[
        ("🔒 Sign in again", "ACTION|pwl_reconnect"), ("➕ New case", "ACTION|reset"),
    ])
    assert j.ctx.user_data["draft_data"] == draft and j.fake.drafts == []
    j.fake.form_bounces = 0
    await journey_call(j, bot.passwordless_reconnect, "ACTION|pwl_reconnect", callback=True)
    login_step(j.sim, "Open Kaizen sign-in", buttons=[
        ("🔒 Open Kaizen sign-in", "https://connect.invalid/handoff#" + j.links[-1].token),
        ("❌ Cancel", "ACTION|cancel"),
    ])
    await j.sign_in()
    login_step(j.sim, "connected again", "draft is still waiting", buttons=[("💾 Save to Kaizen", "ACTION|pwl_reconnected")])
    await journey_call(j, bot.passwordless_reconnected, "ACTION|pwl_reconnected", callback=True)
    assert j.ctx.user_data["last_filing_status"] == "success"
    assert len(j.fake.drafts) == 1
    assert j.fake.drafts[0]["values"] == expected_values("CBD", draft["fields"])
    assert j.fake.submit_clicks == 0
    login_step(j.sim, "Saved! Your draft is ready on Kaizen", buttons=[
        ("🔗 Open saved draft", j.fake.drafts[0]["url"]),
        ("📋 Another form", "ACTION|same_case_another"), ("➕ New case", "ACTION|file"),
    ])
    assert all(unstamp(b.callback_data) not in {"ACTION|pwl_reconnect", "ACTION|pwl_reconnected", "APPROVE|draft"}
               for row in j.sim.messages_sent[-1][2].inline_keyboard for b in row)


async def test_login_journey_reset_then_real_browser_reconnect(login_journey):
    j = login_journey
    await password_choice(j)
    await password_submit(j)
    login_step(j.sim, "Kaizen connected", "Send")
    page, pw = await filer._connect_cdp()
    try:
        assert await filer._login(page, USERNAME, PASSWORD)
        await filer.save_session_state(page.context, j.sim.user_id, USERNAME)
    finally:
        await page.context.close()
        await pw.stop()
    assert filer.load_session_state(j.sim.user_id, USERNAME)["cookies"]
    await journey_call(j, bot.reset_data, "/reset")
    login_step(j.sim, "Reset Portfolio Guru?", "Cases already saved in Kaizen are unaffected", buttons=[
        ("🗑️ Delete data", "CONFIRM|reset"), ("🛡️ Keep data", "CONFIRM|keep"),
    ])
    assert bot.has_credentials(j.sim.user_id)
    assert await journey_call(j, bot.handle_reset_confirm, "CONFIRM|reset", callback=True) == bot.AWAIT_USERNAME
    login_step(j.sim, "data is clear", "Connect Kaizen", buttons=CONNECT_BUTTONS)
    assert not bot.has_credentials(j.sim.user_id)
    assert filer.load_session_state(j.sim.user_id, USERNAME) is None
    await journey_call(j, bot.setup_password_start, "ACTION|setup_password", callback=True)
    login_step(j.sim, "Share my Kaizen login", "username (email)")
    assert await password_submit(j) == ConversationHandler.END
    login_step(j.sim, "Kaizen connected", "Send")
    assert j.fake.login_attempts == 3
    await connected_case(j)
