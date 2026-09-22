"""Opt-in pytest observation of real registered callbacks in passing scenarios.

This records evidence, not completion. Failed/skipped tests earn no credit.
"""
import dis
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

_RECORDS = {}
_PENDING = []
_CODES = {}
_EMITTED = set()
_ERRORS = []
_CURRENT = None
_PREVIOUS = None
_START = {}
_EFFECTS = {}
_BOUNDARIES = {
    "bot": ("route_filing", "store_credentials", "store_voice_profile", "clear_voice_profile", "store_training_level", "store_curriculum", "save_health_profile", "_test_kaizen_login"),
    "stripe_handler": ("create_checkout_session",), "supabase_sync": ("delete_user_data", "consume_link_token", "store_beta_request"),
    "assessor_writeback": ("execute_write_plan",), "filing_coverage": ("record_pushback",),
    "chase_guard": ("log_chase",), "consent": ("record_consent", "record_withdrawal"),
    "urllib.request": ("urlopen",),
}
_BOUNDARY_NAMES = {name for names in _BOUNDARIES.values() for name in names}


def _value(obj, name):
    if isinstance(obj, Mock) and name not in vars(obj) and name not in obj._mock_children:
        return None
    return getattr(obj, name, None)


def _profile(frame, event, result):
    if event == "call":
        if frame.f_code in _CODES:
            _START.setdefault(id(frame), dict(_EFFECTS))
        name = frame.f_code.co_name
        if name in {"_execute_mock_call", "__call__", "<lambda>"} or name.startswith("fake_") or name in _BOUNDARY_NAMES:
            for module, names in _BOUNDARIES.items():
                loaded = sys.modules.get(module)
                for attribute in names:
                    target = getattr(loaded, attribute, None)
                    if target is not None and (frame.f_locals.get("self") is target or frame.f_code is getattr(target, "__code__", None)):
                        key = module + "." + attribute
                        _EFFECTS[key] = _EFFECTS.get(key, 0) + 1
        return
    if event != "return" or frame.f_code not in _CODES:
        return
    opcode = dis.opname[frame.f_code.co_code[frame.f_lasti]] if frame.f_lasti >= 0 else ""
    if opcode not in {"RETURN_VALUE", "RETURN_CONST"}:
        if opcode not in {"YIELD_VALUE", "YIELD_FROM"}:
            _START.pop(id(frame), None)
        return  # An async suspension is not a successful handler return.
    before = _START.pop(id(frame), {})
    effects = sorted(k for k, v in _EFFECTS.items() if v > before.get(k, 0))
    update = frame.f_locals.get("update")
    if update is None:
        if _CODES[frame.f_code] in {"_store_draft", "_restore_retryable_draft"}:
            _PENDING.append({"callback": _CODES[frame.f_code], "kind": "internal", "payload": None, "command": None, "result": None, "boundaries": []})
        return
    query = _value(update, "callback_query")
    payload = _value(query, "data") if query is not None else None
    message = _value(update, "message")
    kind, command = "other", None
    if isinstance(payload, str):
        kind = "callback"
    elif message is not None:
        text = _value(message, "text")
        if isinstance(text, str):
            kind = "text"
            if text.startswith("/"):
                kind, command = "command", text.split()[0][1:].split("@")[0]
        else:
            for field, label in (("voice", "voice"), ("audio", "audio"), ("photo", "image"),
                                 ("video", "video"), ("document", "document")):
                if _value(message, field):
                    kind = label
                    break
    _PENDING.append({"callback": _CODES[frame.f_code], "kind": kind,
        "payload": payload if isinstance(payload, str) else None, "command": command,
        "result": result if type(result) in (int, str, type(None)) else type(result).__name__, "boundaries": effects})


@pytest.fixture(autouse=True)
def audit_scenario(request, monkeypatch, tmp_path):
    global _CURRENT, _PREVIOUS
    from tests.helpers import build_offline_application, isolate_bot_storage
    from tests.whole_bot_coverage import inventory
    from telegram import InlineKeyboardButton
    from telegram.ext import Application
    isolate_bot_storage(monkeypatch, tmp_path)
    if not _CODES:
        for slot in inventory(build_offline_application()):
            callback = slot.handler.callback
            while callback:
                _CODES[callback.__code__] = slot.callback
                callback = getattr(callback, "__wrapped__", None)
    _CODES[Application.process_update.__code__] = "process_update"
    import bot
    for helper in (bot._store_draft, bot._restore_retryable_draft, bot.error_handler):
        _CODES[helper.__code__] = helper.__name__
    original = InlineKeyboardButton.__init__
    def button(self, *args, **kwargs):
        original(self, *args, **kwargs)
        caller = Path(sys._getframe(1).f_code.co_filename).name
        if caller in {"bot.py", "supervisor_bot.py", "channel_actions.py"} and isinstance(self.callback_data, str):
            _EMITTED.add(self.callback_data)
    monkeypatch.setattr(InlineKeyboardButton, "__init__", button)
    _CURRENT = request.node.nodeid
    _PENDING.clear()
    _START.clear()
    _EFFECTS.clear()
    from tests.whole_bot_coverage import DispatchRecorder
    recorder = DispatchRecorder(_PENDING.append, lambda: _EFFECTS).start()
    _PREVIOUS = sys.getprofile()
    sys.setprofile(_profile)
    yield
    sys.setprofile(_PREVIOUS)
    recorder.stop()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.passed:
        unique = {json.dumps({**r, "guard_asserted": bool(r.get("guard_asserted"))}, sort_keys=True) for r in _PENDING}
        _RECORDS[item.nodeid] = [json.loads(r) for r in sorted(unique)]
    elif report.failed or report.skipped:
        _ERRORS.append({"test": item.nodeid, "status": report.outcome, "phase": report.when})
        _RECORDS.pop(item.nodeid, None)


def pytest_sessionfinish(session, exitstatus):
    target = os.environ.get("WHOLE_BOT_OBSERVATIONS")
    if target:
        Path(target).write_text(json.dumps({"tests": _RECORDS, "emitted": sorted(_EMITTED),
            "errors": _ERRORS, "exitstatus": int(exitstatus), "run_id": os.environ.get("WHOLE_BOT_RUN_ID", "offline-local")}, sort_keys=True, indent=2) + "\n")

    catalogue = os.environ.get("WHOLE_BOT_CATALOGUE")
    if catalogue:
        from tests.whole_bot_catalogue import catalogue_receipt
        observations = {"tests": _RECORDS, "emitted": sorted(_EMITTED), "errors": _ERRORS}
        receipt = catalogue_receipt(observations)
        receipt["run_id"] = os.environ.get("WHOLE_BOT_RUN_ID", "offline-local")
        Path(catalogue).write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
        if receipt["status"] != "passed":
            session.exitstatus = 1


def record_guard(payload):
    """Scenario authors call this only after asserting the final no-effect guard."""
    for event in reversed(_PENDING):
        if event.get("payload") == payload:
            event["guard_asserted"] = True
            return


def assert_dispatch_result(events, slot, expected):
    """Assert the actual committed result, then bind that assertion to one event."""
    event = next(e for e in reversed(events) if e.get("slot") == slot.key and e.get("dispatched"))
    assert event["source_state"] == slot.state
    assert event["resulting_state"] == expected
    event.update(result_asserted=True, asserted_result=expected)
    return event


def record_command_boundary(command, *, argument_branch="no-args"):
    """After effect/guard assertions, bind credit to the latest actual command."""
    for event in reversed(_PENDING):
        if (event.get("dispatched") and event.get("command") == command and
                event.get("argument_branch") == argument_branch):
            event["boundary_asserted"] = True
            return
