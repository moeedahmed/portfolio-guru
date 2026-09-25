"""Registration inventory and assertion-backed offline coverage receipt.

No source parsing, alternate registrations, network access or implicit coverage.
"""
from dataclasses import dataclass, field
import hashlib
import json
import re
from pathlib import Path

from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler, MessageHandler


@dataclass
class Slot:
    key: str
    callback: str
    selector: str
    commands: tuple
    handler: object = field(repr=False)
    conversation: object = field(repr=False)
    state: object = None

    def identity(self):
        return {"key": self.key, "callback": self.callback, "selector": self.selector,
                "commands": self.commands}


def inventory(app):
    slots = []

    def visit(handlers, path, conversation=None, state=None):
        for i, handler in enumerate(handlers):
            key = f"{path}/{i}"
            if isinstance(handler, ConversationHandler):
                visit(handler.entry_points, key + "/entry", handler)
                for value, children in sorted(handler.states.items()):
                    visit(children, key + f"/state:{value}", handler, value)
                visit(handler.fallbacks, key + "/fallback", handler)
                continue
            commands = tuple(sorted(handler.commands)) if isinstance(handler, CommandHandler) else ()
            if commands:
                selector = "/" + ",".join(commands)
            elif isinstance(handler, CallbackQueryHandler):
                selector = getattr(handler.pattern, "pattern", None)
                assert isinstance(selector, str), f"Unclassified callback selector: {key}"
            else:
                assert isinstance(handler, MessageHandler), f"Unknown handler type: {key}"
                selector = str(handler.filters)
            slots.append(Slot(key, handler.callback.__name__, selector, commands,
                              handler, conversation, state))
    for group, handlers in sorted(app.handlers.items()):
        visit(handlers, f"group:{group}")
    return slots


def registration_digest(slots):
    return hashlib.sha256(json.dumps([s.identity() for s in slots], sort_keys=True).encode()).hexdigest()


# Reviewed registration shape: adding/reordering a slot requires a new exercised
# scenario, not an automatically regenerated approval manifest.
REGISTRATION_DIGEST = "58e786f096b0c8103bbff04531fc423d3a2180a84519997fda522595ba3fe635"
CATEGORIES = {
    name: category for category, names in {
        "admin": "assignbeta_command beta_command filingreport_command funnelreport_command listusers_command setbeta_command settier_command",
        "disabled": "bulk_command chase_command",
        "protected-boundary": "handle_approval_approve handle_approval_submit handle_reset_confirm handle_upgrade_button setup_password setup_retry_login reset_data passwordless_setup_done",
        "internal": "handle_assessor_intent_capture",
        "safe": """_setup_wrong_input arcp_command cancel_command curriculum_command gather_command
            gather_done_callback handle_action_button handle_amend_draft handle_approval_edit
            handle_approval_media_feedback handle_attachment_confirm handle_callback handle_case_input
            handle_chase_log handle_consent_callback handle_document_intent handle_edit_field
            handle_edit_value handle_edit_value_with_intent handle_feedback handle_filing_feedback
            handle_form_choice handle_form_search_text handle_gathering_input handle_info_button
            handle_mid_conversation_text handle_pathway_choice handle_pending_media_context
            handle_pushback handle_quick_improve handle_review_draft handle_same_case_another
            handle_set_curriculum handle_set_level handle_supervisor_callback handle_template_review_media
            handle_template_review_text handle_unsigned_range_pick health_command help_command link_command
            pathway_command privacy_command settings_command setup_cancel setup_curriculum
            setup_start setup_training_level setup_username start unsigned_command upgrade_command
            voice_collect_example voice_start passwordless_setup_start passwordless_setup_new_link
            passwordless_awaiting_text""",
    }.items() for name in names.split()
}

# Mixed dispatchers can reach writes; their harmless entry paths must never
# qualify the entire family as safe for a future live explorer.
for _name in """beta_command link_command handle_action_button handle_callback
    handle_attachment_confirm handle_chase_log handle_consent_callback handle_feedback
    handle_filing_feedback handle_pathway_choice handle_pushback handle_set_curriculum
    handle_set_level handle_supervisor_callback setup_curriculum setup_training_level
    voice_collect_example""".split():
    CATEGORIES[_name] = "protected-boundary"


def semantic_units(slots):
    from tests.whole_bot_catalogue import reviewed_units
    return reviewed_units(slots)


def update_identity(update):
    """Retain input shape, never command arguments or clinical text."""
    query = update.callback_query
    payload = query.data if query else None
    message = update.message
    kind, command, argument_branch = "other", None, None
    if isinstance(payload, str):
        kind = "callback"
    elif message:
        if message.text:
            kind = "text"
            if message.text.startswith("/"):
                parts = message.text.split()
                kind, command = "command", parts[0][1:].split("@")[0]
                argument_branch = "token" if len(parts) > 1 else "no-args"
        else:
            for field, label in (("voice", "voice"), ("audio", "audio"), ("photo", "image"),
                                 ("video", "video"), ("document", "document")):
                if getattr(message, field, None):
                    kind = label
                    break
    return {"kind": kind, "payload": payload, "command": command, "argument_branch": argument_branch}


class DispatchRecorder:
    """Observe PTB's selected leaf and committed state without changing routing.

    Only Application.process_update can open an observation scope. Conversation
    check_result identifies the selected leaf, including entry/fallback routes.
    The resulting state is read AFTER ConversationHandler commits it.
    """
    def __init__(self, sink, effects=lambda: {}):
        from contextvars import ContextVar
        self.scope = ContextVar("whole_bot_dispatch", default=None)
        self.route = ContextVar("whole_bot_route", default=None)
        self.sink, self.effects = sink, effects

    def start(self):
        from contextlib import ExitStack
        from functools import wraps
        from inspect import unwrap
        import sys
        from unittest.mock import patch
        from telegram.ext import Application, BaseHandler
        self.patches = ExitStack()
        process, handle, conversation = Application.process_update, BaseHandler.handle_update, ConversationHandler.handle_update
        process_code = unwrap(process).__code__

        @wraps(process)
        async def dispatch(app, update):
            events = []
            token = self.scope.set((app, inventory(app), events))
            try:
                result = await process(app, update)
                for event in events:
                    self.sink(event)
                return result
            finally:
                self.scope.reset(token)

        @wraps(conversation)
        async def in_conversation(conv, update, app, check_result, context):
            source, key, selected, _ = check_result
            events = []
            token = self.route.set((conv, source, key, selected, events))
            try:
                result = await conversation(conv, update, app, check_result, context)
                for event in events:
                    event["resulting_state"] = conv._conversations.get(key, ConversationHandler.END)
                return result
            finally:
                self.route.reset(token)

        @wraps(handle)
        async def leaf(handler, update, app, check_result, context):
            scope, route = self.scope.get(), self.route.get()
            before = dict(self.effects())
            result = await handle(handler, update, app, check_result, context)
            if scope is None or scope[0] is not app:
                return result
            if route:
                if handler is not route[3]:
                    return result  # A nested direct call is not PTB's selected leaf.
            else:
                frame = sys._getframe(1)
                try:
                    while frame and frame.f_code is not process_code:
                        frame = frame.f_back
                    if frame is None or frame.f_locals.get("handler") is not handler:
                        return result
                finally:
                    del frame
            candidates = [s for s in scope[1] if s.handler is handler and
                          (s.conversation is (route[0] if route else None))]
            if route:
                # State handlers take precedence over fallbacks; entry selection
                # is identified by the actual handler object returned by PTB.
                candidates = [s for s in candidates if s.state is None or s.state == route[1]]
            assert len(candidates) == 1, "Ambiguous actual registration slot"
            slot = candidates[0]
            event = {**update_identity(update), "callback": slot.callback, "slot": slot.key,
                     "dispatched": True, "source_state": route[1] if route else None,
                     "result": result, "boundaries": sorted(k for k, v in self.effects().items() if v > before.get(k, 0))}
            if route:
                route[4].append(event)
            scope[2].append(event)
            return result

        self.patches.enter_context(patch.object(Application, "process_update", dispatch))
        self.patches.enter_context(patch.object(ConversationHandler, "handle_update", in_conversation))
        self.patches.enter_context(patch.object(BaseHandler, "handle_update", leaf))
        return self

    def stop(self):
        self.patches.close()


class Coverage:
    def __init__(self, slots):
        assert registration_digest(slots) == REGISTRATION_DIGEST, "Unclassified registration change"
        from tests.whole_bot_catalogue import producer_digest, PRODUCER_DIGEST
        assert producer_digest() == PRODUCER_DIGEST, "Unreviewed keyboard producer change"
        self.slots = slots
        self.evidence = {}
        self.units = semantic_units(slots)
        self.unit_evidence = {key: {"offline": [], "live": []} for key in self.units}
        self.observed = []
        self.events = []
        self.failures = []
        assert {s.callback for s in slots} <= CATEGORIES.keys(), "Unclassified handler"

    def observe(self):
        def collect(event):
            self.events.append(event)
            self.observed.append((event["slot"], event["result"]))
        self.recorder = DispatchRecorder(collect).start()

    def restore(self):
        self.recorder.stop()

    def validate(self, slot, scenario, *, boundary=False):
        assert any(e.get("slot") == slot.key and e.get("dispatched") for e in self.events), f"Slot not dispatched: {slot.key}"
        category = CATEGORIES[slot.callback]
        if category == "protected-boundary":
            assert boundary, "Protected slot needs a verified final guard/mutation boundary"
        self.evidence[slot.key] = {"scenario": scenario, "status": (
            "protected-boundary-covered" if boundary else "covered")}

    def record_unit(self, unit_id, scenario, *, slots=(), layer="offline",
                    boundary=False, transition=None, artifact=None):
        """Call only after scenario assertions pass; record only proven slots.

        Candidates express possible shared implementations, not proof. A scenario
        claiming multiple registrations must actually dispatch every claimed slot.
        Live evidence uses a transcript artifact, never mocked dispatch evidence.
        """
        unit = self.units[unit_id]
        assert unit["kind"] != "callback-family", "Family requires a complete branch catalogue; completion is derived"
        assert layer in {"offline", "live"} and scenario
        keys = sorted(s.key for s in slots)
        assert set(keys) <= set(unit["registration_candidates"]) or unit["kind"] == "behaviour"
        if unit["classification"] == "protected-boundary":
            assert boundary, "Protected semantic unit requires boundary evidence"
        if unit["kind"] == "state-input-transition":
            assert transition == unit["expectation"], "Unexpected semantic transition"
        if layer == "offline":
            from tests.whole_bot_catalogue import evidence_matches
            from tests.whole_bot_audit import assert_dispatch_result
            assert keys and all(any(e.get("slot") == key for e in self.events) for key in keys), "Unobserved registration"
            if unit["kind"].startswith("state-input"):
                assert transition and transition["from"] == unit["expectation"]["from"], "Unexpected semantic transition"
                for slot in slots:
                    assert_dispatch_result(self.events, slot, transition["to"])
            for key in keys:
                matches = [{**e, "boundary_asserted": boundary, "guard_asserted": boundary}
                           for e in self.events if e.get("slot") == key]
                assert any(evidence_matches(unit, e, slots, scenario) for e in matches), "Evidence does not match semantic obligation"
        else:
            assert artifact and Path(artifact).is_file() and Path(artifact).stat().st_size, "Live evidence requires a transcript artifact"
        evidence = {"scenario": scenario, "slots": keys, "transition": transition,
                    "artifact": artifact, "status": ("protected-boundary-reached" if layer == "live" else "protected-boundary-covered") if boundary else "covered"}
        if evidence not in self.unit_evidence[unit_id][layer]:
            self.unit_evidence[unit_id][layer].append(evidence)
        if layer == "offline":
            for key in keys:
                self.evidence[key] = {"scenario": scenario, "status": "observed"}

    def absorb(self, other):
        """Combine assertion-backed scenarios from the same reviewed inventory."""
        assert self.units == other.units, "Semantic catalogue mismatch"
        assert registration_digest(self.slots) == registration_digest(other.slots), "Registration mismatch"
        self.evidence.update(other.evidence)
        self.failures.extend(other.failures)
        for key, layers in other.unit_evidence.items():
            for layer, entries in layers.items():
                for entry in entries:
                    if entry not in self.unit_evidence[key][layer]:
                        self.unit_evidence[key][layer].append(entry)

    def receipt(self):
        for key, unit in self.units.items():
            if unit["kind"] == "callback-family" and unit["members"]:
                for layer in ("offline", "live"):
                    if all(self.unit_evidence[m][layer] for m in unit["members"]):
                        self.unit_evidence[key][layer] = [e for m in unit["members"] for e in self.unit_evidence[m][layer]]
        uncovered = sorted(k for k in self.units if not self.unit_evidence[k]["offline"])
        return {"schema": 2, "layer": "offline", "status": "failed" if self.failures else "pending" if uncovered else "passed", "covered": len(self.units) - len(uncovered),
                "uncovered": uncovered, "failures": self.failures,
                "completion": {"catalogue_complete": True, "required_layers": ["offline", "live"]},
                "unclassified": sorted(k for k, u in self.units.items() if u["review_required"]),
                "units": [{**u, "evidence": {layer: sorted(items, key=lambda e: json.dumps(e, sort_keys=True))
                           for layer, items in self.unit_evidence[k].items()}} for k, u in self.units.items()],
                "registration": {"digest": REGISTRATION_DIGEST, "count": len(self.slots),
                    "exercised": len(self.evidence), "slots": [{**s.identity(),
                    **self.evidence.get(s.key, {"status": "unobserved"})} for s in self.slots]},
                "limits": "Per-scenario ledger only; the audited catalogue and aggregate gate decide completion. Dynamic values and inputs/providers are sampled."}

    def summary(self):
        r = self.receipt()
        return (f"{r['status'].upper()}: {r['covered']}/{len(self.units)} offline semantic units; "
                f"{len(self.slots)} registration slots retained; aggregate proof is separate")

    def require_complete(self):
        receipt = self.receipt()
        assert receipt["status"] == "passed", self.summary()

    def write(self, path):
        Path(path).write_text(json.dumps(self.receipt(), sort_keys=True, indent=2) + "\n")
