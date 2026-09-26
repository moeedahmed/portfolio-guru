"""Coverage must describe the real registration and observed execution."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler, MessageHandler, filters

from tests.helpers import TEST_USER, build_offline_application, make_command_update, make_callback_update
from tests.test_e2e_offline import offline_app, _prepare_update
from tests.whole_bot_coverage import CATEGORIES, Coverage, inventory


def test_inventory_uses_real_builder_and_rejects_new_registration():
    app = build_offline_application()
    slots = inventory(app)
    assert {"health", "gather", "assignbeta"} <= {
        command for slot in slots for command in slot.commands
    }
    coverage = Coverage(slots)
    assert coverage.receipt()["status"] == "pending"
    app.add_handler(CommandHandler("unclassified_new_command", AsyncMock()))
    with pytest.raises(AssertionError, match="registration"):
        Coverage(inventory(app))


def test_unexecuted_slots_cannot_be_reported_as_covered():
    coverage = Coverage(inventory(build_offline_application()))
    receipt = coverage.receipt()
    assert receipt["uncovered"]
    assert receipt["covered"] == 0
    assert coverage.summary().startswith("PENDING")
    with pytest.raises(AssertionError, match="PENDING"):
        coverage.require_complete()


COMMAND_EXPECTATIONS = {
    "arcp": "Review date", "assignbeta": "Admin only", "beta": "Beta request submitted",
    "bulk": "Bulk filing is coming soon", "cancel": "Cancelled", "chase": "coming soon",
    "curriculum": "Which curriculum", "delete": "Your Portfolio Guru data is clear",
    "filingreport": "Admin only", "funnelreport": "Admin only", "gather": "Gathering mode",
    "health": "Portfolio Health", "help": "Portfolio Guru help", "link": "Link Telegram",
    "listusers": "Admin only", "pathway": "Current view", "plan": "Your plan",
    "privacy": "Privacy & consent", "reset": "Your Portfolio Guru data is clear",
    "setbeta": "Admin only", "settier": "Admin only", "settings": "Kaizen: not connected",
    "setup": "Step 1 of 3", "start": "Step 1 of 3", "unsigned": "Connect your Kaizen",
    "upgrade": "Your plan", "voice": "Writing style setup",
}


@pytest.fixture(scope="module")
def coverage_run():
    combined = Coverage(inventory(build_offline_application()))
    yield combined
    output = Path(__file__).resolve().parents[2] / ".artifacts/whole-bot-offline"
    output.mkdir(parents=True, exist_ok=True)
    combined.write(output / "coverage.json")
    (output / "summary.txt").write_text(combined.summary() + "\n")


@pytest.mark.asyncio
async def test_command_dispatch_receipt(offline_app, monkeypatch, tmp_path, coverage_run):
    import bot
    app, collector = offline_app
    monkeypatch.setattr(bot, "has_credentials", lambda uid: False)
    import credentials
    from sqlmodel import Session, select
    link = MagicMock(side_effect=AssertionError("No-argument link must not mutate"))
    beta_store = MagicMock()
    monkeypatch.setattr("supabase_sync.consume_link_token", link)
    monkeypatch.setattr("supabase_sync.store_beta_request", beta_store)
    reset_mirror = MagicMock()
    monkeypatch.setattr("supabase_sync.delete_user_data", reset_mirror)
    coverage = Coverage(inventory(app))
    coverage.observe()
    errors = []
    async def capture_error(update, context):
        errors.append(type(context.error).__name__)
    app.add_error_handler(capture_error)
    commands = sorted({c for slot in coverage.slots for c in slot.commands})
    try:
        for command in commands:
            for slot in coverage.slots:
                if slot.conversation:
                    slot.conversation._conversations.clear()
            app.user_data[TEST_USER.id].clear()
            coverage.observed.clear()
            collector.sent.clear()
            if command in {"reset", "delete"}:
                with Session(credentials.engine) as session:
                    session.add(credentials.UserCredential(
                        telegram_user_id=TEST_USER.id, kaizen_username_enc=b"synthetic",
                        kaizen_password_enc=b"synthetic"))
                    session.commit()
                reset_mirror.reset_mock()
            update = make_command_update(command)
            _prepare_update(update, app.bot)
            await app.process_update(update)
            assert not errors, command
            expected = COMMAND_EXPECTATIONS[command]
            assert any(expected in text for text in collector.texts), command
            assert not any("Traceback" in text for text in collector.texts)
            if command == "cancel":
                assert not any(slot.conversation and slot.conversation._conversations
                               for slot in coverage.slots)
            if command == "beta":
                assert any(m["chat_id"] == bot.ADMIN_USER_ID for m in collector.sent)
            key = coverage.observed[-1][0]
            slot = next(s for s in coverage.slots if s.key == key)
            if command in {"reset", "delete"}:
                reset_mirror.assert_called_once_with(TEST_USER.id)
                with Session(credentials.engine) as session:
                    assert session.exec(select(credentials.UserCredential)).first() is None
            if command == "beta":
                beta_store.assert_called_once_with(TEST_USER.id, TEST_USER.username or "")
            if command == "link":
                link.assert_not_called()
            boundary = command in {"reset", "delete", "beta", "link"}
            if boundary:
                from tests.whole_bot_audit import record_command_boundary
                record_command_boundary(command)
            # /link without a code proves its prompt, not its account mutation.
            if CATEGORIES[slot.callback] != "protected-boundary" or boundary:
                coverage.validate(slot, f"command:{command}", boundary=boundary)
                coverage.record_unit("command:" + command, f"command:{command}", slots=[slot], boundary=boundary)
    finally:
        coverage.write(tmp_path / "coverage.json")
        coverage.restore()
    assert set(commands) == COMMAND_EXPECTATIONS.keys()
    assert coverage.receipt()["status"] == "pending"
    coverage_run.absorb(coverage)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["retry_recommend", "retry_template"])
async def test_retry_dispatches_intended_branch(offline_app, monkeypatch, payload, coverage_run):
    import bot
    from models import FormDraft, FormTypeRecommendation
    app, collector = offline_app
    coverage = Coverage(inventory(app))
    slot = next(s for s in coverage.slots if s.state == bot.AWAIT_FORM_CHOICE
                and "retry_recommend" in s.selector)
    key = (TEST_USER.id, TEST_USER.id)
    slot.conversation._conversations[key] = slot.state
    case = "45M with chest pain, positive troponin; I assessed ACS and escalated to cardiology."
    app.user_data[TEST_USER.id].update(case_text=case)
    monkeypatch.setattr(bot, "get_training_level", lambda uid: "ST5")
    monkeypatch.setattr(bot, "get_curriculum", lambda uid: "2025")
    recommend = AsyncMock(return_value=[FormTypeRecommendation(form_type="CBD", rationale="Case review", uuid="synthetic")])
    extract = AsyncMock(return_value=FormDraft(form_type="MINI_CEX", uuid="synthetic", fields={
        "patient_presentation": "Chest pain assessment", "clinical_reasoning": "Assessed ACS",
        "reflection": "Earlier escalation", "clinical_setting": "Emergency Department"}))
    monkeypatch.setattr(bot, "recommend_form_types", recommend)
    monkeypatch.setattr(bot, "extract_form_data", extract)
    filing = AsyncMock(side_effect=AssertionError("Retry must not file"))
    monkeypatch.setattr(bot, "route_filing", filing)
    if payload == "retry_template":
        app.user_data[TEST_USER.id]["chosen_form"] = "MINI_CEX"
    coverage.observe()
    try:
        update = make_callback_update("ACTION|" + payload)
        _prepare_update(update, app.bot)
        await app.process_update(update)
        expected_state = bot.AWAIT_FORM_CHOICE if payload == "retry_recommend" else bot.AWAIT_APPROVAL
        assert (slot.key, expected_state) in coverage.observed
        assert slot.conversation._conversations[key] == expected_state
        if payload == "retry_recommend":
            recommend.assert_awaited_once_with(case, input_source="retry")
            extract.assert_not_awaited()
            assert any("Best fit: Case-Based Discussion" in t for t in collector.texts)
            assert app.user_data[TEST_USER.id]["form_recommendations"][0].form_type == "CBD"
        else:
            extract.assert_awaited_once()
            assert extract.await_args.args[:2] == (case, "MINI_CEX")
            recommend.assert_not_awaited()
            assert any("Here is your Mini-Clinical Evaluation Exercise draft" in t for t in collector.texts)
        filing.assert_not_awaited()
        from tests.whole_bot_audit import _PENDING, assert_dispatch_result
        if any(e.get("slot") == slot.key for e in _PENDING):
            assert_dispatch_result(_PENDING, slot, expected_state)
        coverage.record_unit("callback:ACTION|" + payload, payload, slots=[slot])
        state_unit = next(k for k, u in coverage.units.items()
                          if u["kind"] == "state-input-transition" and slot.key in u["registration_candidates"]
                          and k.endswith("/transition:" + payload))
        coverage.record_unit(state_unit, payload, slots=[slot],
                             transition={"from": slot.state, "to": expected_state, "input": "ACTION|" + payload})
        coverage.record_unit("behaviour:retry", "test_retry_dispatches_intended_branch[" + payload + "]", slots=[slot])
        assert coverage.receipt()["status"] == "pending"
        coverage_run.absorb(coverage)
    finally:
        coverage.restore()


@pytest.mark.parametrize("kind", ["callback", "state"])
def test_new_callback_or_state_slot_requires_review(kind):
    app = build_offline_application()
    if kind == "callback":
        app.add_handler(CallbackQueryHandler(AsyncMock(), pattern=r"^NEW\|"))
    else:
        conv = next(h for h in app.handlers[0] if isinstance(h, ConversationHandler))
        conv.states[99999] = [MessageHandler(filters.TEXT, AsyncMock())]
    with pytest.raises(AssertionError, match="registration"):
        Coverage(inventory(app))


def test_receipt_is_deterministic_and_write_failure_propagates(tmp_path):
    coverage = Coverage(inventory(build_offline_application()))
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    coverage.write(first)
    coverage.write(second)
    assert first.read_bytes() == second.read_bytes()
    with pytest.raises(OSError):
        coverage.write(tmp_path)


def test_semantic_credit_requires_proven_slots_and_boundaries(tmp_path):
    coverage = Coverage(inventory(build_offline_application()))
    starts = [s for s in coverage.slots if "start" in s.commands]
    coverage.observed = [(starts[0].key, -1)]
    with pytest.raises(AssertionError, match="Unobserved"):
        coverage.record_unit("command:start", "tuple is not dispatch", slots=[starts[0]])
    coverage.events = [{"slot": starts[0].key, "callback": starts[0].callback,
                        "command": "start", "dispatched": True}]
    coverage.record_unit("command:start", "one context asserted", slots=[starts[0]])
    with pytest.raises(AssertionError, match="Unobserved"):
        coverage.record_unit("command:start", "unproven shared behaviour", slots=starts)
    assert len(coverage.unit_evidence["command:start"]["offline"]) == 1
    assert coverage.unit_evidence["command:start"]["offline"][0]["slots"] == [starts[0].key]
    with pytest.raises(AssertionError, match="branch catalogue"):
        coverage.record_unit("callback-family:FORM", "one form is not every form", slots=[])
    protected = next(s for s in coverage.slots if s.callback == "handle_approval_approve")
    coverage.observed.append((protected.key, -1))
    coverage.events.append({"slot": protected.key, "callback": protected.callback,
                            "payload": "APPROVE|draft", "dispatched": True})
    with pytest.raises(AssertionError, match="boundary evidence"):
        coverage.record_unit("callback:APPROVE|draft", "guard only", slots=[protected])
    coverage.record_unit("callback:APPROVE|draft", "stubbed final effect asserted", slots=[protected], boundary=True)
    assert coverage.unit_evidence["callback:APPROVE|draft"]["offline"][0]["status"] == "protected-boundary-covered"
    with pytest.raises(AssertionError, match="transcript"):
        coverage.record_unit("command:start", "missing live proof", layer="live")
    artifact = tmp_path / "synthetic-transcript.json"
    artifact.write_text('{"fixture": true}')
    coverage.record_unit("command:start", "schema test only", layer="live", artifact=str(artifact))
    transition_unit = next(k for k, u in coverage.units.items() if u["kind"] == "state-input-transition")
    with pytest.raises(AssertionError, match="Unexpected semantic transition"):
        coverage.record_unit(transition_unit, "wrong live annotation", layer="live",
                             transition={"from": -1, "to": -1, "input": "wrong"}, artifact=str(artifact))
    assert coverage.receipt()["status"] == "pending"


def test_selector_delta_preserves_unrelated_actions():
    coverage = Coverage(inventory(build_offline_application()))
    global_route = next(s.handler for s in coverage.slots if s.callback == "handle_action_button")
    # Buttons that move the case conversation stay inside case_conv; the global
    # handler threw their returned state away (Retry left the case stuck).
    for payload in ("retry_recommend", "retry_template", "retry_filing", "pwl_reconnected", "add_reflection_detail"):
        assert not global_route.pattern.match("ACTION|" + payload)
    for payload in ("settings", "health", "retry_filing_extra", "retry_recommend_extra", "retry_template_extra"):
        assert global_route.pattern.match("ACTION|" + payload)


"""Real-dispatch scenarios closing catalogue gaps; external boundaries are fake."""
from unittest.mock import AsyncMock, MagicMock
import pytest
from telegram.ext import CallbackQueryHandler
import bot
from tests.helpers import TEST_USER, make_callback_update, make_text_update
from tests.test_e2e_offline import offline_app, _prepare_update
from tests.whole_bot_coverage import inventory
from tests.whole_bot_catalogue import input_samples


@pytest.fixture
def scenario(offline_app, monkeypatch):
    from models import FormDraft
    app, collector = offline_app
    monkeypatch.setattr(bot, "has_credentials", lambda uid: False)
    monkeypatch.setattr(bot, "get_training_level", lambda uid: "ST5")
    monkeypatch.setattr(bot, "get_curriculum", lambda uid: "2025")
    monkeypatch.setattr(bot, "get_voice_profile", lambda uid: None)
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "get_credentials", lambda uid: ("synthetic", "synthetic"))
    monkeypatch.setattr(type(app.bot), "get_file", AsyncMock(side_effect=RuntimeError("synthetic media unavailable")))
    draft = FormDraft(form_type="MINI_CEX", uuid="synthetic", fields={
        "patient_presentation": "Synthetic chest pain", "clinical_setting": "ED",
        "clinical_reasoning": "Assessed and escalated", "reflection": "Earlier escalation next time"})
    monkeypatch.setattr(bot, "extract_form_data", AsyncMock(return_value=draft))
    monkeypatch.setattr(bot, "classify_intent", AsyncMock(return_value="edit"))
    filing = AsyncMock(return_value={"status": "success", "filled": [], "skipped": []})
    monkeypatch.setattr(bot, "route_filing", filing)
    errors = []
    async def error(update, context): errors.append(str(context.error))
    app.add_error_handler(error)
    return app, collector, draft, filing, errors


MEDIA_CASES = [("_setup_wrong_input", kind) for kind in ("voice", "audio", "image", "video", "document")]
MEDIA_CASES += [("voice_collect_example", kind) for kind in ("image", "voice")]
MEDIA_CASES += [("handle_approval_media_feedback", kind) for kind in ("audio", "video", "document")]
MEDIA_CASES += [("handle_edit_value", kind) for kind in ("voice", "audio", "image", "video", "document")]
MEDIA_CASES += [("handle_template_review_media", kind) for kind in ("voice", "audio", "video")]
MEDIA_CASES += [("handle_pending_media_context", kind) for kind in ("voice", "audio")]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,kind", MEDIA_CASES)
async def test_registered_media_recovers_without_filing(scenario, monkeypatch, owner, kind):
    app, collector, draft, filing, errors = scenario
    monkeypatch.setattr(bot, "has_credentials", lambda uid: True)
    update = input_samples(app.bot)[kind]
    slot = next(s for s in inventory(app) if s.state is not None and s.callback == owner and s.handler.check_update(update))
    slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] = slot.state
    data = app.user_data[TEST_USER.id]
    data.update(draft_data={"_type": "FORM", **draft.model_dump()}, pending_draft_data={"_type": "FORM", **draft.model_dump()},
                case_text="Synthetic case", chosen_form="MINI_CEX", _setup_state_hint="username")
    if owner == "handle_pending_media_context": data["_pending_doc"] = {"kind": "video"}
    before = data["draft_data"].copy()
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors
    assert collector.texts and any(any(word in t.lower() for word in ("type", "text", "video", "could", "email", "read", "document")) for t in collector.texts)
    assert data.get("draft_data") == before
    filing.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,text", [("handle_form_search_text", "mini"),
    ("handle_template_review_text", "Please add that I escalated earlier"),
    ("handle_edit_value_with_intent", "Please add that I escalated earlier")])
async def test_registered_text_state_keeps_selected_case(scenario, owner, text):
    app, collector, draft, filing, errors = scenario
    slot = next(s for s in inventory(app) if s.state is not None and s.callback == owner)
    slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] = slot.state
    app.user_data[TEST_USER.id].update(draft_data={"_type": "FORM", **draft.model_dump()},
        pending_draft_data={"_type": "FORM", **draft.model_dump()}, case_text="Synthetic case", chosen_form="MINI_CEX")
    update = make_text_update(text)
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors and collector.texts
    assert any("Mini" in t or "draft" in t.lower() or "form" in t.lower() for t in collector.texts)
    filing.assert_not_awaited()


RECOVERY_CALLBACKS = """ACTION|back_to_menu ACTION|back_to_missing ACTION|confirm_refresh_for_health
ACTION|confirm_refresh_portfolio ACTION|continue_thin ACTION|health ACTION|health_limited
ACTION|refresh_portfolio ACTION|reset ACTION|retry_setup_login ACTION|settings
AMEND|cancel_choice AMEND|update_current CANCEL|doc_intent CANCEL|edit REVIEW|draft APPROVE|submit""".split()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", RECOVERY_CALLBACKS)
async def test_registered_callback_recovery(scenario, payload):
    app, collector, draft, filing, errors = scenario
    slots = inventory(app)
    candidates = [s for s in slots if isinstance(s.handler, CallbackQueryHandler) and s.handler.pattern.match(payload)]
    state_slot = next((s for s in candidates if s.state is not None), None)
    if state_slot:
        state_slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] = state_slot.state
    update = make_callback_update(payload)
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors
    assert collector.texts and any(any(w in t.lower() for w in ("case", "draft", "connect", "cancel", "kaizen", "setting", "detail", "no longer active")) for t in collector.texts), payload
    filing.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("payload", "expected_text"), [
    ("ACTION|pwl_reconnect", "isn't available right now"),
    ("ACTION|pwl_reconnected", "can't see a Kaizen sign-in yet"),
])
async def test_passwordless_sign_in_again_buttons_never_save_without_a_session(
    scenario, monkeypatch, payload, expected_text
):
    """Real dispatch of the passwordless "sign in again" buttons.

    With the sign-in page down, "Sign in again" explains and stops; with no
    working kept session, "I've signed in" keeps waiting. Neither may reach
    the filer.
    """
    app, collector, draft, filing, errors = scenario
    monkeypatch.setattr(bot, "_create_passwordless_link", AsyncMock(return_value=None))
    monkeypatch.setattr(bot, "_probe_kept_kaizen_session", AsyncMock(return_value=False))
    app.user_data[TEST_USER.id].update(draft_data={"_type": "FORM", **draft.model_dump()})
    update = make_callback_update(payload)
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors
    assert any(expected_text in t for t in collector.texts), collector.texts
    filing.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["3m", "6m", "12m", "all", "cancel", "custom"])
async def test_unsigned_range_has_observed_destination(scenario, monkeypatch, choice):
    app, collector, draft, filing, errors = scenario
    scrape = AsyncMock(return_value=[])
    monkeypatch.setattr(bot, "scrape_unsigned_tickets", scrape)
    update = make_callback_update("UNSIGNED|" + choice)
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors
    expected = "Cancelled" if choice == "cancel" else "date range" if choice == "custom" else "No unsigned tickets"
    assert any(expected in t for t in collector.texts)
    assert scrape.await_count == (0 if choice in {"cancel", "custom"} else 1)


@pytest.mark.asyncio
async def test_checkout_reaches_stubbed_final_boundary(scenario, monkeypatch):
    app, collector, draft, filing, errors = scenario
    checkout = AsyncMock(return_value="https://checkout.stripe.com/synthetic")
    monkeypatch.setattr("stripe_handler.create_checkout_session", checkout)
    update = make_callback_update("UPGRADE|pro_plus")
    _prepare_update(update, app.bot)
    await app.process_update(update)
    checkout.assert_awaited_once_with(TEST_USER.id, "pro_plus")
    assert not errors and any("complete payment" in t for t in collector.texts)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["curriculum_links", "date_of_encounter", "key_capabilities", "other", "reflection"])
async def test_pushback_reaches_stubbed_record_boundary(scenario, monkeypatch, field):
    app, collector, draft, filing, errors = scenario
    record = MagicMock()
    monkeypatch.setattr("filing_coverage.record_pushback", record)
    update = make_callback_update("PUSHBACK|CBD|" + field)
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors
    record.assert_called_once_with("CBD", field)


"""Aggregate proof can never turn missing, stale or skipped evidence green."""
import json
from pathlib import Path
import pytest
from tests.whole_bot_aggregate import aggregate


def evidence(root, run_id="run"):
    from tests.whole_bot_coverage import REGISTRATION_DIGEST
    from tests.telegram_live_policy import COMMAND_POLICY
    from tests.whole_bot_catalogue import reviewed_units, PRODUCER_DIGEST, requirements_digest
    declarations = list(reviewed_units(inventory(build_offline_application())).values())
    binding = {"run_id": run_id, "target": "portfolio_guru_bot", "candidate_sha": "a" * 40, "runtime_sha": "a" * 40}
    for name in ("run-context", "runtime", "runtime-process"):
        (root / (name + ".json")).write_text(json.dumps({**binding, "status": "passed", "exit_code": 0}))
    for name in ("catalogue", "offline", "live_graph", "clinical", "cleanup"):
        value = {**binding, "status": "passed", "registration_digest": REGISTRATION_DIGEST}
        if name == "catalogue":
            value.update(catalogue_complete=True, unclassified=[], uncovered=[], producer_digest=PRODUCER_DIGEST,
                         requirements_digest=requirements_digest(declarations), units=[{**u,
                         "status": "protected-boundary-covered" if u["classification"] == "protected-boundary" else "covered",
                         "scenarios": ["asserted"]} for u in declarations])
        if name == "live_graph":
            value.update(routes=[{"root": c, "status": "observed"} for c, policy in COMMAND_POLICY.items() if policy == "safe"],
                         commands={c: "observed" if policy == "safe" else "protected-command-not-invoked" for c, policy in COMMAND_POLICY.items()}, failures=[])
        if name == "clinical":
            value.update(journeys={"cbd": "passed", "unstructured": "passed"}, protected=[{"payload": "APPROVE|draft", "status": "protected-boundary-reached"}])
        (root / (name + ".json")).write_text(json.dumps(value))
    (root / "offline.xml").write_text('<testsuites><testsuite tests="1"><testcase name="asserted"/></testsuite></testsuites>')
    (root / "live.xml").write_bytes((root / "offline.xml").read_bytes())
    (root / "live-process.json").write_text(json.dumps({**binding, "status": "passed", "exit_code": 0}))
    for name in ("live_graph", "clinical", "cleanup"):
        (root / (name + "-transcript.json")).write_text(json.dumps({**binding, "events": [{"action": "send:/cancel", "received": "Cancelled"}]}))


def test_complete_layers_are_required(tmp_path):
    assert aggregate(tmp_path, "run")["status"] == "pending"
    evidence(tmp_path)
    assert aggregate(tmp_path, "run")["status"] == "passed"


@pytest.mark.parametrize("defect", ["skip", "stale", "empty", "unknown", "cleanup", "malformed", "catalogue", "missing-unit"])
def test_incomplete_or_failed_layer_never_passes(tmp_path, defect):
    evidence(tmp_path)
    if defect == "skip":
        (tmp_path / "offline.xml").write_text('<testsuite><testcase><skipped/></testcase></testsuite>')
    elif defect == "empty":
        (tmp_path / "clinical-transcript.json").write_text("[]")
    elif defect == "malformed":
        (tmp_path / "clinical.json").write_text("broken")
    else:
        name = "catalogue" if defect in {"catalogue", "missing-unit"} else "cleanup" if defect == "cleanup" else "live_graph"
        p = tmp_path / (name + ".json")
        value = json.loads(p.read_text())
        if defect == "stale": value["run_id"] = "old"
        elif defect == "unknown": value["failures"] = ["unknown control"]
        elif defect == "cleanup": value["status"] = "failed"
        elif defect == "missing-unit": value["units"].pop()
        else: value["unclassified"] = ["new control"]
        p.write_text(json.dumps(value))
    assert aggregate(tmp_path, "run")["status"] != "passed"


def test_offline_network_guard_refuses_before_connect():
    import socket
    with socket.socket() as sock, pytest.raises(pytest.fail.Exception, match="Offline test"):
        sock.connect(("192.0.2.1", 443))
    with pytest.raises(pytest.fail.Exception, match="Offline test"):
        socket.getaddrinfo("example.invalid", 443)


@pytest.mark.parametrize("offline_ok", [False, True])
def test_aggregate_driver_finishes_offline_before_readiness(tmp_path, monkeypatch, offline_ok):
    from types import SimpleNamespace
    from tests import whole_bot_aggregate as driver
    def offline(args, **kwargs):
        assert args == ["bash", "scripts/verify_release.sh"]
        assert "TELETHON_SESSION" not in kwargs["env"]
        if offline_ok:
            evidence(tmp_path, kwargs["env"]["WHOLE_BOT_RUN_ID"])
            for name in ("live_graph", "clinical", "cleanup"):
                driver.write_layer(tmp_path, name, kwargs["env"]["WHOLE_BOT_RUN_ID"], "pending")
        return {"exit_code": 0 if offline_ok else 1, "status": "passed" if offline_ok else "failed", "reason": "exit"}
    monkeypatch.setattr(driver, "bounded_process", offline)
    readiness = MagicMock(return_value=False)
    monkeypatch.setattr("tests.telegram_live_harness.has_telethon_env", readiness)
    assert driver.run(tmp_path) == (20 if offline_ok else 1)
    assert readiness.call_count == 0  # Candidate identity precedes credential readiness.


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["open", "skip", "later", "review", "recapture", "cancel-draft", "prepare-writeback", "request-save-draft", "confirm-save-draft"])
async def test_supervisor_controls_dispatch_and_guard_missing_session(scenario, monkeypatch, action):
    app, collector, draft, filing, errors = scenario
    connect = AsyncMock()
    monkeypatch.setattr("supervisor_bot.connect_cdp_page", connect)
    update = make_callback_update("SUP|" + action + "|aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors and collector.texts
    assert any(any(word in t.lower() for word in ("draft", "no longer", "skip", "queue", "review")) for t in collector.texts)
    connect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["last_12m", "last_6m", "recent_10"])
async def test_voice_sample_window_observes_empty_provider_result(scenario, monkeypatch, window):
    from types import SimpleNamespace
    from voice_sampler import SamplerStatus, parse_window
    app, collector, draft, filing, errors = scenario
    sample = AsyncMock(return_value=SimpleNamespace(status=SamplerStatus.NO_SAMPLES, reason=None))
    monkeypatch.setattr("voice_sampler.sample_kaizen_entries", sample)
    slot = next(s for s in inventory(app) if s.callback == "voice_collect_example" and s.state is not None)
    slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] = slot.state
    app.user_data[TEST_USER.id]["voice_kaizen_path_started"] = True
    update = make_callback_update("VOICE|kaizen_sample|" + window)
    _prepare_update(update, app.bot)
    await app.process_update(update)
    sample.assert_awaited_once_with(TEST_USER.id, parse_window(window))
    assert not errors and any("couldn't find any entries" in t for t in collector.texts)


@pytest.mark.asyncio
async def test_voice_done_reaches_profile_storage_boundary(scenario, monkeypatch):
    app, collector, draft, filing, errors = scenario
    generate = AsyncMock(return_value='{"style":"concise"}')
    store = MagicMock()
    monkeypatch.setattr("voice_profile.generate_voice_profile", generate)
    monkeypatch.setattr(bot, "store_voice_profile", store)
    monkeypatch.setattr("extractor._generate", AsyncMock(return_value="Synthetic sample draft"))
    slot = next(s for s in inventory(app) if s.callback == "voice_collect_example" and s.state is not None)
    slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] = slot.state
    app.user_data[TEST_USER.id]["voice_examples"] = ["Synthetic example one", "Synthetic example two", "Synthetic example three"]
    update = make_callback_update("VOICE|done")
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors and any("Voice profile activated" in t for t in collector.texts)
    store.assert_called_once_with(TEST_USER.id, generate.return_value, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["UNKNOWN|value", "FORM|"])
async def test_unknown_and_malformed_callbacks_cannot_file(scenario, payload):
    app, collector, draft, filing, errors = scenario
    update = make_callback_update(payload)
    _prepare_update(update, app.bot)
    before = dict(app.user_data[TEST_USER.id])
    await app.process_update(update)
    await app.process_update(update)  # Replay is also inert outside a matching conversation.
    assert not errors and dict(app.user_data[TEST_USER.id]) == before
    filing.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["CHASE_LOG|cancel", "CHASE_LOG|synthetic@example.invalid"])
async def test_chase_controls_use_only_stubbed_local_record(scenario, monkeypatch, payload):
    app, collector, draft, filing, errors = scenario
    log = MagicMock(return_value={"chase_number": 1, "date": "2026-01-01"})
    monkeypatch.setattr("chase_guard.log_chase", log)
    update = make_callback_update(payload)
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors and any("closed" in t or "logged" in t for t in collector.texts)
    assert log.call_count == int(not payload.endswith("cancel"))


@pytest.mark.asyncio
@pytest.mark.parametrize("sentiment", ["good", "bad"])
async def test_feedback_stubs_credentials_and_external_record(scenario, monkeypatch, sentiment):
    import builtins
    import io
    from types import SimpleNamespace
    app, collector, draft, filing, errors = scenario
    real_open = builtins.open
    def synthetic_secret(path, *args, **kwargs):
        if str(path).endswith("/.bws-token"):
            return io.StringIO("synthetic-machine-token")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", synthetic_secret)
    machine = MagicMock(return_value=SimpleNamespace(stdout='{"value":"synthetic-notion-token"}'))
    write = MagicMock()
    monkeypatch.setattr("subprocess.run", machine)
    monkeypatch.setattr("urllib.request.urlopen", write)
    update = make_callback_update("FEEDBACK|" + sentiment + "|CBD|success")
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors
    machine.assert_called_once()
    write.assert_called_once()
    assert write.call_args.args[0].full_url == "https://api.notion.com/v1/pages"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["APPROVE|submit", "ATTACH|yes", "ATTACH|no"])
async def test_protected_save_controls_stop_at_verified_boundary(scenario, monkeypatch, payload):
    app, collector, draft, filing, errors = scenario
    monkeypatch.setattr(bot, "has_credentials", lambda uid: True)
    slot = next(s for s in inventory(app) if s.state == bot.AWAIT_APPROVAL and isinstance(s.handler, CallbackQueryHandler) and s.handler.pattern.match(payload))
    slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] = slot.state
    draft.fields.update(date_of_encounter="2026-01-01", stage_of_training="Higher/ST4-ST6",
        trainee_role="Assessed and escalated", level_of_supervision="Indirect",
        reflection="I learned to escalate earlier and will brief the team sooner next time.")
    app.user_data[TEST_USER.id].update(draft_data={"_type": "FORM", **draft.model_dump()},
        case_text="Synthetic case: I assessed chest pain and escalated. I learned to escalate earlier and will brief the team sooner next time.", chosen_form="MINI_CEX")
    if payload.startswith("ATTACH|"):
        app.user_data[TEST_USER.id]["awaiting_attachment_confirmation"] = True
    update = make_callback_update(payload)
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors
    if payload == "APPROVE|submit":
        filing.assert_not_awaited()
        assert any("only saves Kaizen entries as drafts" in t for t in collector.texts)
        from tests.whole_bot_audit import record_guard
        record_guard(payload)
        return
    assert filing.await_count == 1, collector.texts
    assert filing.await_args.kwargs["form_type"] == "MINI_CEX"
    assert any("saved" in t.lower() for t in collector.texts)


@pytest.mark.asyncio
@pytest.mark.parametrize("graph_fails", [False, True])
async def test_whole_live_composition_is_ordered_and_always_cancels(tmp_path, monkeypatch, graph_fails):
    from types import SimpleNamespace
    from tests import test_whole_bot_live as live
    from tests.telegram_live_harness import write_transcript_artifact, TelegramExchange
    evidence(tmp_path)
    for key, value in {"TELEGRAM_E2E_ARTIFACT_DIR": str(tmp_path), "WHOLE_BOT_ARTIFACT_DIR": str(tmp_path),
        "TELEGRAM_QA_USER_ID": "99999", "WHOLE_BOT_RUN_ID": "run", "WHOLE_BOT_LIVE_PROOF": "1",
        "TELEGRAM_BOT_USERNAME": "portfolio_guru_bot", "PORTFOLIO_GURU_EXPECTED_SHA": "a" * 40}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(live, "telethon_env", lambda: {"bot_username": "portfolio_guru_bot", "session": "fake", "api_hash": "fake"})
    monkeypatch.setattr(live, "registered_catalogue", lambda: {})
    order = []
    async def graph(*args, **kwargs):
        order.append("graph")
        if graph_fails:
            raise AssertionError("synthetic graph failure")
        (tmp_path / "whole-bot-transcript.json").write_bytes((tmp_path / "live_graph-transcript.json").read_bytes())
        return {**json.loads((tmp_path / "live_graph.json").read_text()), "protected": []}
    clinical = AsyncMock(side_effect=AssertionError("protected generation must not run"))
    async def workflow(client, target, steps, *, strict):
        assert strict and steps[0].message == "/cancel"
        order.append(steps[0].name)
        write_transcript_artifact([TelegramExchange("cancel", "send:/cancel", "Cancelled")])
    monkeypatch.setattr(live, "explore_whole_bot", graph)
    monkeypatch.setattr("tests.test_e2e.test_e2e_cbd_ready_draft_to_cancel_journey", clinical)
    monkeypatch.setattr("tests.telegram_live_harness.run_telegram_workflow", workflow)
    client = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(id=99999)))
    if graph_fails:
        with pytest.raises(AssertionError, match="synthetic graph"):
            await live.test_live_whole_bot(client)
    else:
        await live.test_live_whole_bot(client)
    assert order == ["graph", "cleanup"]
    clinical.assert_not_awaited()
    assert json.loads((tmp_path / "cleanup.json").read_text())["status"] == "passed"
    assert aggregate(tmp_path, "run")["status"] == ("failed" if graph_fails else "pending")


@pytest.mark.asyncio
async def test_active_amend_update_refines_without_filing(scenario):
    app, collector, draft, filing, errors = scenario
    slot = next(s for s in inventory(app) if s.state == bot.AWAIT_APPROVAL and s.callback == "handle_amend_draft")
    slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] = slot.state
    app.user_data[TEST_USER.id].update(draft_data={"_type": "FORM", **draft.model_dump()},
        case_text="Synthetic chest pain assessment", chosen_form="MINI_CEX", amend_mode=True,
        amend_pending_feedback="I will escalate earlier next time.")
    update = make_callback_update("AMEND|update_current")
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors and any("draft" in text.lower() for text in collector.texts)
    bot.extract_form_data.assert_awaited_once()
    assert bot.extract_form_data.await_args.kwargs["edit_feedback"] == "I will escalate earlier next time."
    filing.assert_not_awaited()


@pytest.mark.asyncio
async def test_offline_message_edits_capture_text_keyword():
    from tests.test_e2e_offline import ResponseCollector
    collector = ResponseCollector()
    message = await collector.fake_send_message(chat_id=99999, text="Initial")
    await message.edit_text(text="Updated")
    assert collector.texts == ["Initial", "Updated"]


@pytest.mark.parametrize("mismatch", [False, True])
def test_aggregate_preserves_target_and_limits_live_environment(tmp_path, monkeypatch, mismatch):
    from types import SimpleNamespace
    from tests import whole_bot_aggregate as driver
    monkeypatch.setenv("RELEASE_LIVE_TARGET", "portfolio_guru_bot")
    monkeypatch.setenv("RELEASE_LIVE_ALLOWLIST", "portfolio_guru_bot")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "wrong_bot" if mismatch else "portfolio_guru_bot")
    monkeypatch.setenv("TELETHON_SESSION", "synthetic-session")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-be-inherited")
    monkeypatch.setenv("GOOGLE_API_KEY", "provider-canary-not-for-live-client")
    monkeypatch.delenv("RUN_LIVE_TELEGRAM", raising=False)
    calls = []
    def child(args, **kwargs):
        calls.append(args)
        run_id = kwargs["env"]["WHOLE_BOT_RUN_ID"]
        evidence(tmp_path, run_id)
        if len(calls) == 1:
            for name in ("live_graph", "clinical", "cleanup"):
                driver.write_layer(tmp_path, name, run_id, "pending")
        else:
            assert "UNRELATED_SECRET" not in kwargs["env"]
            assert kwargs["env"]["GOOGLE_API_KEY"] == "fake"
            assert kwargs["env"]["TELEGRAM_LIVE_ALLOWED_BOTS"] == "portfolio_guru_bot"
            assert kwargs["env"]["TELETHON_SESSION"] == "synthetic-session"
        return {"exit_code": 0, "status": "passed", "reason": "exit"}
    monkeypatch.setattr(driver, "bounded_process", child)
    monkeypatch.setenv("PORTFOLIO_GURU_EXPECTED_SHA", "a" * 40)
    def verified(repo, root, run_id, target, sha):
        return json.loads((root / "runtime.json").read_text())
    monkeypatch.setattr(driver, "verify_candidate", verified)
    ready = MagicMock(return_value=True)
    monkeypatch.setattr("tests.telegram_live_harness.has_telethon_env", ready)
    assert driver.run(tmp_path) == (20 if mismatch else 0)
    assert len(calls) == (1 if mismatch else 2)
    assert ready.call_count == (0 if mismatch else 1)


@pytest.mark.asyncio
async def test_back_to_missing_dispatches_template_review(scenario):
    app, collector, draft, filing, errors = scenario
    slot = next(s for s in inventory(app) if s.state == bot.AWAIT_APPROVAL and s.callback == "handle_amend_draft")
    slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] = slot.state
    app.user_data[TEST_USER.id].update(pending_draft_data={"_type": "FORM", **draft.model_dump()}, chosen_form="MINI_CEX")
    update = make_callback_update("ACTION|back_to_missing")
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors
    assert bot._format_template_review("MINI_CEX", draft) in collector.texts
    assert slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] == bot.AWAIT_TEMPLATE_REVIEW
    bot.extract_form_data.assert_not_awaited()
    filing.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_pending", [False, True])
async def test_retry_setup_login_dispatches_password_state(scenario, monkeypatch, has_pending):
    app, collector, draft, filing, errors = scenario
    slot = next(s for s in inventory(app) if s.state == bot.AWAIT_PASSWORD and s.callback == "setup_retry_login")
    slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] = slot.state
    login = AsyncMock(return_value=False)
    monkeypatch.setattr(bot, "_test_kaizen_login", login)
    if has_pending:
        monkeypatch.setattr(bot, "_load_setup_retry_credentials", lambda context: ("synthetic@example.invalid", "synthetic-password"))
    update = make_callback_update("ACTION|retry_setup_login")
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors
    if has_pending:
        assert any("Login failed" in text for text in collector.texts)
        login.assert_awaited_once_with("synthetic@example.invalid", "synthetic-password")
    else:
        assert bot._KAIZEN_USERNAME_PROMPT in collector.texts
        login.assert_not_awaited()
    assert slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] == bot.AWAIT_USERNAME
    filing.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("context_data", [{"draft_data": {"_type": "FORM", "form_type": "MINI_CEX", "fields": {"reflection": "Preserved reflection"}}, "chosen_form": "MINI_CEX", "amend_pending_feedback": "Preserve feedback"}, {}, {"case_text": "Synthetic preserved case", "amend_pending_feedback": "Preserve my feedback", "chosen_form": "MINI_CEX"}])
async def test_stale_amend_recovers_without_discarding_context(scenario, context_data):
    app, collector, draft, filing, errors = scenario
    slot = next(s for s in inventory(app) if s.state == bot.AWAIT_APPROVAL and s.callback == "handle_amend_draft")
    slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] = slot.state
    app.user_data[TEST_USER.id].update(context_data)
    update = make_callback_update("AMEND|update_current")
    _prepare_update(update, app.bot)
    await app.process_update(update)
    assert not errors
    assert any("no longer available" in text and "/cancel" in text for text in collector.texts)
    assert slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] == bot.AWAIT_CASE_INPUT
    assert all(app.user_data[TEST_USER.id][key] == value for key, value in context_data.items())
    bot.extract_form_data.assert_not_awaited()
    filing.assert_not_awaited()


def test_offline_font_discovery_is_isolated_without_disabling_subprocesses():
    import subprocess
    import sys
    for command in (["fc-list", "--help"], ["system_profiler", "-xml", "SPFontsDataType"]):
        with pytest.raises(FileNotFoundError, match="Host font discovery"):
            subprocess.check_output(command)
    assert subprocess.check_output([sys.executable, "-c", "print('synthetic')"]) == b"synthetic\n"


@pytest.fixture(autouse=True)
def bind_current_registration_module(monkeypatch):
    # Startup smoke tests deliberately re-import bot. Patch the same module that
    # build_offline_application registers, rather than a collection-time alias.
    import importlib
    monkeypatch.setitem(globals(), "bot", importlib.import_module("bot"))


@pytest.mark.parametrize("installed", [False, True])
def test_aggregate_includes_only_existing_hermes_source(tmp_path, monkeypatch, installed):
    from tests import whole_bot_aggregate as driver
    from types import SimpleNamespace
    home = tmp_path / "home"
    source = home / ".hermes" / "hermes-agent"
    if installed:
        (source / "hermes_cli").mkdir(parents=True)
        (source / "hermes_cli" / "__init__.py").touch()
    monkeypatch.setattr(driver.Path, "home", lambda: home)
    monkeypatch.setenv("PYTHONPATH", "unreviewed-inherited-path")
    monkeypatch.setenv("RUN_LIVE_TELEGRAM", "0")
    def offline(args, **kwargs):
        paths = kwargs["env"]["PYTHONPATH"].split(__import__("os").pathsep)
        assert paths == [str(Path(__file__).resolve().parents[1])] + ([str(source)] if installed else [])
        assert kwargs["env"]["PYTHON_DOTENV_DISABLED"] == "1"
        return {"exit_code": 1, "status": "failed", "reason": "exit"}
    monkeypatch.setattr(driver, "bounded_process", offline)
    assert driver.run(tmp_path / "receipt") == 1


@pytest.mark.parametrize("evidence_kind", ["direct", "wrong-slot", "wrong-state", "wrong-result"])
def test_catalogue_rejects_unproven_state_dispatch(evidence_kind):
    from tests.whole_bot_catalogue import catalogue_receipt, reviewed_units
    slots = inventory(build_offline_application())
    unit = next(u for u in reviewed_units(slots).values()
                if u["kind"] == "state-input-transition" and u["id"].endswith("/transition:retry_template"))
    slot = next(s for s in slots if s.key in unit["registration_candidates"])
    event = {"callback": slot.callback, "kind": "callback", "payload": "ACTION|retry_template",
             "command": None, "result": bot.AWAIT_APPROVAL, "boundaries": [],
             "dispatched": True, "slot": slot.key, "source_state": slot.state,
             "resulting_state": bot.AWAIT_APPROVAL, "result_asserted": True, "asserted_result": bot.AWAIT_APPROVAL}
    if evidence_kind == "direct": event["dispatched"] = False
    if evidence_kind == "wrong-slot": event["slot"] = "group:0/global"
    if evidence_kind == "wrong-state": event["source_state"] = bot.AWAIT_APPROVAL
    if evidence_kind == "wrong-result": event["resulting_state"] = bot.AWAIT_FORM_CHOICE
    receipt = catalogue_receipt({"tests": {"asserted": [event]}, "emitted": [], "errors": []})
    assert unit["id"] in receipt["uncovered"]


def test_catalogue_link_prompt_cannot_cover_token_mutation():
    from tests.whole_bot_catalogue import catalogue_receipt
    event = {"callback": "link_command", "kind": "command", "command": "link",
             "payload": None, "argument_branch": "no-args", "result": -1, "boundaries": []}
    receipt = catalogue_receipt({"tests": {"prompt asserted": [event]}, "emitted": [], "errors": []})
    assert "command:link" in receipt["uncovered"]
    assert "command:link/args:token" in receipt["uncovered"]


@pytest.mark.parametrize("command,owner", [("beta", "beta_command"), ("reset", "reset_data"), ("delete", "reset_data")])
def test_catalogue_protected_command_requires_asserted_boundary(command, owner):
    from tests.whole_bot_catalogue import catalogue_receipt
    event = {"callback": owner, "kind": "command", "command": command, "payload": None,
             "result": -1, "boundaries": []}
    receipt = catalogue_receipt({"tests": {"output only": [event]}, "emitted": [], "errors": []})
    assert "command:" + command in receipt["uncovered"]


@pytest.mark.asyncio
async def test_link_token_dispatch_reaches_only_stubbed_account_mutation(offline_app, monkeypatch):
    from tests.whole_bot_audit import record_command_boundary
    app, collector = offline_app
    consume = MagicMock(return_value=(True, "Synthetic account linked"))
    monkeypatch.setattr("supabase_sync.consume_link_token", consume)
    coverage = Coverage(inventory(app))
    coverage.observe()
    try:
        update = make_command_update("link", args=["synthetic-one-use-code"])
        _prepare_update(update, app.bot)
        await app.process_update(update)
        consume.assert_called_once_with("synthetic-one-use-code", TEST_USER.id)
        assert any("Synthetic account linked" in t for t in collector.texts)
        record_command_boundary("link", argument_branch="token")
        slot = next(s for s in coverage.slots if "link" in s.commands)
        coverage.record_unit("command:link/args:token", "token consumed at stub", slots=[slot], boundary=True)
        assert not coverage.unit_evidence["command:link"]["offline"]
    finally:
        coverage.restore()


def state_obligations():
    from tests.whole_bot_catalogue import reviewed_units
    return [u["id"] for u in reviewed_units(inventory(build_offline_application())).values()
            if u["kind"] == "state-input"]


@pytest.mark.asyncio
@pytest.mark.parametrize("unit_id", state_obligations())
async def test_each_state_slot_dispatches_its_guard_or_effect(scenario, monkeypatch, unit_id):
    """One isolated real application dispatch per registered slot and input kind.

    These are deliberately recovery/guard probes; existing clinical and effect
    scenarios remain required by the separate semantic branch obligations.
    """
    from tests.whole_bot_audit import _PENDING, assert_dispatch_result, record_guard
    app, collector, draft, filing, errors = scenario
    coverage = Coverage(inventory(app))
    unit = coverage.units[unit_id]
    slot = next(s for s in coverage.slots if s.key in unit["registration_candidates"])
    owner, kind = slot.callback, unit["expectation"]["input"]
    data = app.user_data[TEST_USER.id]
    monkeypatch.setattr(bot, "get_credentials", lambda uid: None)
    login = AsyncMock(return_value=False)
    store_level, store_curriculum = MagicMock(), MagicMock()
    monkeypatch.setattr(bot, "_test_kaizen_login", login)
    monkeypatch.setattr(bot, "store_training_level", store_level)
    monkeypatch.setattr(bot, "store_curriculum", store_curriculum)
    # No kept Kaizen session and no sign-in service in the offline suite.
    monkeypatch.setattr(bot.kaizen_connection, "has_kept_session", lambda uid: False)
    expected = ConversationHandler.END
    payload = None
    if kind == "callback":
        routes = {
            "setup_retry_login": ("ACTION|retry_setup_login", bot.AWAIT_USERNAME),
            "setup_training_level": ("SETLEVEL|ST5", -1),
            "setup_curriculum": ("SETUP_CURRICULUM|2025", -1),
            "gather_done_callback": ("GATHER|done", -1),
            "handle_document_intent": ("DOCUSE|info", bot.AWAIT_CASE_INPUT),
            "handle_form_choice": ("FORM|show_all", bot.AWAIT_FORM_CHOICE),
            "handle_pathway_choice": ("PATHWAY|training", -1),
            "handle_amend_draft": ("AMEND|cancel", -1),
            "handle_approval_approve": ("APPROVE|draft", -1),
            "handle_approval_submit": ("APPROVE|submit", -1),
            "handle_attachment_confirm": ("ATTACH|yes", -1),
            "handle_approval_edit": ("EDIT|reflection", -1),
            "handle_edit_field": ("FIELD|reflection", bot.AWAIT_EDIT_VALUE),
            "handle_quick_improve": ("IMPROVE|reflection", -1),
            "handle_review_draft": ("REVIEW|draft", -1),
            "voice_collect_example": ("VOICE|more", bot.AWAIT_VOICE_EXAMPLES),
            # Passwordless is switched off offline, so starting or re-linking
            # falls back to the username prompt; "I've signed in" with no kept
            # session keeps waiting for the sign-in.
            "passwordless_setup_start": ("ACTION|connect_passwordless", bot.AWAIT_USERNAME),
            "passwordless_setup_new_link": ("ACTION|passwordless_link", bot.AWAIT_USERNAME),
            "passwordless_setup_done": ("ACTION|passwordless_done", bot.AWAIT_PASSWORDLESS),
        }
        if owner == "handle_callback":
            routes_by_input = [("ACTION|cancel", -1), ("CANCEL|draft", -1),
                ("ACTION|continue_thin", -1), ("ACTION|retry_recommend", bot.AWAIT_CASE_INPUT),
                ("ACTION|back_to_missing", -1), ("CASE|new", bot.AWAIT_CASE_INPUT),
                ("FILING_CURRICULUM|select|2025", bot.AWAIT_APPROVAL)]
            payload, expected = next((p, e) for p, e in routes_by_input if slot.handler.pattern.match(p))
        else:
            payload, expected = routes[owner]
        if owner == "handle_attachment_confirm":
            # Exercise the existing missing-credentials boundary after a
            # genuine pending confirmation. Stale answers are tested separately.
            data["awaiting_attachment_confirmation"] = True
        update = make_callback_update(payload)
    else:
        update = input_samples(app.bot)[kind]
        if owner == "_setup_wrong_input":
            data["_setup_state_hint"] = "password" if slot.state == bot.AWAIT_PASSWORD else "username"
            expected = slot.state
        elif owner in {"setup_username", "setup_password", "handle_case_input", "handle_gathering_input", "handle_template_review_text"}:
            expected = bot.AWAIT_USERNAME
        elif owner == "handle_form_search_text":
            expected = bot.AWAIT_FORM_CHOICE
        elif owner == "passwordless_awaiting_text":
            expected = bot.AWAIT_PASSWORDLESS
        elif owner == "handle_pending_media_context":
            expected = bot.AWAIT_DOC_INTENT
        elif owner == "voice_collect_example":
            expected = bot.AWAIT_VOICE_EXAMPLES
        elif owner == "handle_template_review_media":
            expected = bot.AWAIT_TEMPLATE_REVIEW
        elif owner == "handle_approval_media_feedback":
            expected = bot.AWAIT_APPROVAL
        elif owner == "handle_mid_conversation_text":
            update = make_text_update("Do you submit to my supervisor?")
            expected = bot.AWAIT_CASE_INPUT
        elif owner == "handle_edit_value_with_intent":
            update = make_text_update("Please use this longer synthetic edit text")
        else:
            assert owner == "handle_edit_value", owner
    slot.conversation._conversations[(TEST_USER.id, TEST_USER.id)] = slot.state
    coverage.observe()
    try:
        _prepare_update(update, app.bot)
        await app.process_update(update)
        assert not errors
        assert slot.conversation._conversations.get((TEST_USER.id, TEST_USER.id), -1) == expected
        event = assert_dispatch_result(coverage.events, slot, expected)
        assert event["kind"] == kind
        filing.assert_not_awaited()
        bot.extract_form_data.assert_not_awaited()
        if owner == "setup_password":
            login.assert_awaited_once_with("", "synthetic")
        else:
            login.assert_not_awaited()
        if owner == "setup_training_level":
            store_level.assert_called_once_with(TEST_USER.id, "ST5")
        if owner == "setup_curriculum":
            store_curriculum.assert_called_once_with(TEST_USER.id, "2025")
        # Explicit expected state + no-filing assertions bind only this dispatch.
        if any(e.get("slot") == slot.key for e in _PENDING):
            assert_dispatch_result(_PENDING, slot, expected)
            if payload:
                record_guard(payload)
        coverage.record_unit(unit_id, "registered guard/effect asserted", slots=[slot],
            boundary=unit["classification"] == "protected-boundary",
            transition={"from": slot.state, "to": expected, "input": kind})
        assert coverage.unit_evidence[unit_id]["offline"]
    finally:
        coverage.restore()


@pytest.mark.asyncio
async def test_shadowed_route_needs_its_own_real_state_dispatch(scenario, monkeypatch):
    import re
    from telegram.ext import CallbackContext
    from tests.whole_bot_coverage import DispatchRecorder
    from tests.whole_bot_catalogue import evidence_matches, reviewed_units
    from tests.whole_bot_audit import assert_dispatch_result
    app, collector, draft, filing, errors = scenario
    slots = inventory(app)
    slot = next(s for s in slots if s.state == bot.AWAIT_FORM_CHOICE and "retry_recommend" in s.selector)
    unit = next(u for u in reviewed_units(slots).values() if u["kind"] == "state-input"
                and slot.key in u["registration_candidates"])
    update = make_callback_update("ACTION|retry_recommend")
    _prepare_update(update, app.bot)
    events = []
    recorder = DispatchRecorder(events.append).start()
    key = (TEST_USER.id, TEST_USER.id)
    try:
        context = CallbackContext.from_update(update, app)
        assert await slot.handler.callback(update, context) == bot.AWAIT_CASE_INPUT
        assert not events  # Direct invocation is not dispatch evidence.

        slot.conversation._conversations[key] = bot.AWAIT_EDIT_FIELD
        await app.process_update(update)
        wrong = events[-1]
        assert wrong["callback"] == slot.callback and wrong["slot"] != slot.key
        assert wrong["source_state"] == bot.AWAIT_EDIT_FIELD
        wrong.update(result_asserted=True, asserted_result=bot.AWAIT_CASE_INPUT)
        assert not evidence_matches(unit, wrong, [slot])

        global_slot = next(s for s in slots if s.callback == "handle_action_button")
        with monkeypatch.context() as shadow:
            shadow.setattr(global_slot.handler, "callback", slot.handler.callback)
            shadow.setattr(global_slot.handler, "pattern", re.compile(r"^ACTION\|retry_recommend$"))
            slot.conversation._conversations[key] = slot.state
            await app.process_update(update)
            global_event = events[-1]
            assert global_event["callback"] == slot.callback
            assert global_event["slot"] == global_slot.key
            assert global_event["source_state"] is None
            assert "resulting_state" not in global_event
            assert slot.conversation._conversations[key] == slot.state
            assert not evidence_matches(unit, global_event, [slot])

        await app.process_update(update)
        correct = assert_dispatch_result(events, slot, bot.AWAIT_CASE_INPUT)
        assert evidence_matches(unit, correct, [slot])
        assert not errors and any("No case text found" in t for t in collector.texts)
        filing.assert_not_awaited()
        bot.extract_form_data.assert_not_awaited()
    finally:
        recorder.stop()
