"""Reviewed semantic requirements; registration objects remain routing authority."""
import ast
import hashlib
import json
import re
from pathlib import Path

from telegram import Update
from telegram.ext import CallbackQueryHandler, MessageHandler
from tests.helpers import TEST_USER, build_offline_application
from tests.whole_bot_coverage import inventory, registration_digest, REGISTRATION_DIGEST


def payload_branch(payload):
    parts = payload.split("|")
    family = parts[0]
    if family == "FORM" and len(parts) == 2:
        return payload if parts[1] in {"best", "show_all", "back", "disabled"} else "FORM|cat_*" if parts[1].startswith("cat_") else "FORM|*"
    if family in {"FIELD", "SETLEVEL", "SET_CURRICULUM", "SETUP_CURRICULUM", "PATHWAY", "PATHWAY_SETTINGS", "EDIT", "REMIND"}:
        return family + "|*"
    if family in {"SUP", "CONSENT", "FILING_CURRICULUM", "FILING", "FEEDBACK"} and len(parts) >= 3:
        return "|".join(parts[:2]) + "|*"
    if family in {"APPROVE", "CANCEL"} and len(parts) == 3 and parts[1] == "draft":
        return family + "|draft"  # case token stamp, not a new branch
    if family == "UNSIGNED":
        return "UNSIGNED|*"
    if family == "PUSHBACK" and len(parts) >= 3:
        return "PUSHBACK|*|" + parts[2]
    if family == "ACTION" and len(parts) > 2:
        if parts[1] == "health_queue":
            return "|".join(parts[:3]) + "|*"
        if parts[1] in {"health_page", "health_review_select", "health_review_confirm", "health_route_set", "post_file_more"}:
            return "|".join(parts[:2]) + "|*"
    return payload


def producer_digest():
    """Conservative producer-module AST drift guard; never execution credit.

    Hash whole modules so a changed lookup table or indirect payload builder
    cannot escape review merely because callback_data still names a variable.
    """
    modules = []
    for name in ("bot", "supervisor_bot", "channel_actions", "channel_reply_policy", "conversation_supervisor"):
        tree = ast.parse((Path(__file__).parents[1] / (name + ".py")).read_text())
        modules.append((name, ast.dump(tree, include_attributes=False)))
    return hashlib.sha256(json.dumps(modules).encode()).hexdigest()


# Reviewed consent callback ownership: case_conv stores the resumed state.
# Wording and payload branches are unchanged; offline dispatch tests cover
# photo -> consent -> document intent and non-resuming state preservation.
# Batch A (stop losing work): bot.py state/clean-up changes only; no keyboard
# or callback payload added, removed or reworded.
# Login onboarding: case retention, validation, stale acknowledgements and structural
# telemetry only. Existing copy/payloads are unchanged; FORM re-entry is dispatch-tested.
# 9 Oct 2026: the drafting progress message is deleted after the draft is
# sent (edit fallback only); the draft keyboard and payloads are unchanged.
# 9 Oct 2026: reviewed case/reflection priority before question/menu routing;
# account replies use fixed copy. Existing keyboard/payload branches unchanged.
# 9 Oct 2026: non-string input is never treated as case evidence; no branch change.
# 9 Oct 2026: reviewed schema-only reflection footer and draft-stamped possible-KC Add.
# Add updates KCs only after the tap; stale/save-in-progress taps are inert.
# 9 Oct 2026: reviewed regeneration guard retains unconfirmed possible KCs;
# only the existing Add tap selects them. No keyboard or callback changes.
# 9 Oct 2026: reviewed static curriculum-only possible-KC copy and metadata
# preservation through field edits and active/pending re-analysis. Add remains
# the sole selection action;
# existing keyboards, callback payloads and approval ownership are unchanged.
# 9 Oct 2026: reviewed catalogue validation at draft reconstruction and preview;
# invalid KCs are removed before filing. No keyboard or callback payload changes.
# 9 Oct 2026: reviewed account/billing topic routing; connection failures retain
# the existing Connect Kaizen/Settings actions; pricing appears only for billing.
# 9 Oct 2026: third KC selected by default with Remove instead of Add.
# 9 Oct 2026: Remove syncs the pending snapshot and is hidden/rejected for
# saved-draft amendments and reopens; existing callback payloads are unchanged.
# 9 Oct 2026: KC Remove button dropped; one label per button action.
# 9 Oct 2026: photo reflection hints use filled draft fields; source warning
# removed, text-as-case and all Save labels aligned, descriptive KC edits validated.
# 2026-10-09: restored photo reflection provenance at preview/storage/filing;
# required reflection fields checked individually. Existing controls unchanged.
# 9 Oct 2026: final main/wider-journey merge, own-word provenance and draft-message fix.
# 9 Oct 2026: reflection provenance checks retained authored words per field;
# captions/replies/reuse stay separate from OCR. Existing controls unchanged.
# 9 Oct 2026: word support and verbatim authored-reflection fallback shared
# by preview, storage and filing; existing controls unchanged.
# 10 Oct 2026: status questions retain saved-case authorship; reflection-prompt
# replies and labelled multiline reflection retain their complete verbatim fallback.
# Existing controls, callback payloads and approval ownership are unchanged.
# 10 Oct 2026: reflection fallback requires a prompted reply; status, reuse,
# form, cancel and edit controls retain their handlers and pending reflection.
# 10 Oct 2026: standard reflection gaps and voice/audio replies retain authored
# wording; edit/control exclusions and case-lifecycle cleanup are unchanged in scope.
# 10 Oct 2026: polite edit and cancel instructions keep their control routes.
# 10 Oct 2026: reflection sources exclude controls; voice/audio share typed routing;
# prompted replies fill blank reflections. Existing payloads and ownership unchanged.
# 10 Oct 2026: an edit verb counts as a control only when it names part of the draft.
PRODUCER_DIGEST = '96522dc993f31a54335dad903d1d025758e57dd4ada26c17eb665475851da95a'
CALLBACK_BRANCHES = set("""
ACTION|connect_passwordless ACTION|passwordless_done ACTION|passwordless_link ACTION|pwl_reconnect ACTION|pwl_reconnected ACTION|setup_password
ACTION|back_to_menu ACTION|back_to_missing ACTION|cancel ACTION|change_curriculum ACTION|change_level
ACTION|change_pathway ACTION|confirm_refresh_for_health ACTION|confirm_refresh_portfolio ACTION|continue_thin
ACTION|delete ACTION|file ACTION|health ACTION|health_limited ACTION|health_page|*
ACTION|health_queue|awaiting|* ACTION|health_queue|draft|* ACTION|health_review_confirm|*
ACTION|health_review_select|* ACTION|health_review_setup ACTION|health_route_set|* ACTION|health_view|about ACTION|health_view|curriculum ACTION|health_view|more
ACTION|health_view|priorities ACTION|portfolio_defaults ACTION|refresh_portfolio ACTION|reset
ACTION|retry_filing ACTION|retry_recommend ACTION|retry_setup_login ACTION|retry_template
ACTION|same_case_another ACTION|settings ACTION|setup ACTION|voice AMEND|cancel AMEND|cancel_choice
AMEND|start_new AMEND|update_current APPROVE|draft ATTACH|no ATTACH|yes CANCEL|doc_intent CANCEL|draft
CANCEL|edit CANCEL|form CASE|improve CASE|new CONFIRM|keep CONFIRM|reset CONSENT|accept|* CONSENT|decline|* DOCUSE|attach
DOCUSE|both DOCUSE|ignore DOCUSE|info FIELD|* FORM|* FORM|back FORM|best FORM|cat_* FORM|disabled
FORM|show_all GATHER|done INFO|privacy_details INFO|privacy_summary INFO|what PATHWAY_SETTINGS|* PATHWAY|* PUSHBACK|*|curriculum_links
PUSHBACK|*|date_of_encounter PUSHBACK|*|key_capabilities PUSHBACK|*|other PUSHBACK|*|reflection SETLEVEL|*
SET_CURRICULUM|* SUP|cancel-draft|* SUP|confirm-save-draft|* SUP|later|* SUP|open|* SUP|prepare-writeback|*
SUP|recapture|* SUP|request-save-draft|* SUP|review|* SUP|skip|* UNSIGNED|* UPGRADE|pro_plus VOICE|back_to_choice VOICE|back_to_settings
VOICE|done VOICE|kaizen_sample|last_12m VOICE|kaizen_sample|last_6m VOICE|kaizen_sample|recent_10 VOICE|more
VOICE|path_kaizen VOICE|path_manual VOICE|remove APPROVE|submit REVIEW|draft IMPROVE|reflection EDIT|*
FILING|feedback|* FEEDBACK|good|* FEEDBACK|bad|* FILING_CURRICULUM|retry|* FILING_CURRICULUM|select|*
SETUP_CURRICULUM|* REMIND|*
""".split())


PROTECTED_BRANCHES = {"APPROVE|draft", "APPROVE|submit", "ATTACH|yes", "ATTACH|no", "CONFIRM|reset", "UPGRADE|pro_plus", "VOICE|done",
    "FEEDBACK|good|*", "FEEDBACK|bad|*", "SUP|confirm-save-draft|*"}
BEHAVIOURS = {
    "unknown-command": "test_unknown_and_retired_commands_reply_without_echo",
    "command-usage": "test_command_usage_independent_dispatch",
    "cancel": "test_cancel_resets_conversation",
    "error": "test_template_review_image_error_uses_send_text_recovery",
    "retry": "test_retry_dispatches_intended_branch",
    "unknown-callback": "test_unknown_and_malformed_callbacks_cannot_file[UNKNOWN|value]",
    "malformed-callback": "test_unknown_and_malformed_callbacks_cannot_file[FORM|]",
    "stale-callback": "test_stale_form_selection_without_case_gives_restart_path",
    "replayed-callback": "test_unknown_and_malformed_callbacks_cannot_file[FORM|]",
    "replayed-input": "test_replay_after_successful_preview_does_not_reextract",
    "state-isolation": "test_active_drafts_are_isolated_per_user_context",
}


def input_samples(bot):
    base = {"message_id": 1, "date": 0, "chat": {"id": TEST_USER.id, "type": "private"},
            "from": {"id": TEST_USER.id, "is_bot": False, "first_name": "Synthetic"}}
    file = {"file_id": "synthetic", "file_unique_id": "synthetic", "width": 1, "height": 1, "duration": 1}
    fields = {"text": {"text": "synthetic"}, "voice": {"voice": file}, "audio": {"audio": file},
              "image": {"photo": [file]}, "video": {"video": file}, "document": {"document": file}}
    return {kind: Update.de_json({"update_id": 1, "message": {**base, **value}}, bot) for kind, value in fields.items()}


def state_expectation(slot, kind):
    """Reviewed recovery probes and committed destinations, separate from execution.

    Branch coverage still requires the wider callback catalogue. These probes
    prove dispatch ownership, including disconnected/stale recovery behaviour.
    """
    callbacks = {
        "setup_retry_login": ("ACTION|retry_setup_login", 0),
        # Passwordless setup. Offline the option is switched off / the sign-in
        # service is absent, so starting or re-linking falls back to asking for
        # the username (AWAIT_USERNAME); "I've signed in" with no kept session
        # stays waiting (AWAIT_PASSWORDLESS).
        "passwordless_setup_start": ("ACTION|connect_passwordless", 0),
        "passwordless_setup_new_link": ("ACTION|passwordless_link", 0),
        "passwordless_setup_done": ("ACTION|passwordless_done", 15),
        "setup_password_start": ("ACTION|setup_password", 0),
        "setup_training_level": ("SETLEVEL|ST5", -1),
        "setup_curriculum": ("SETUP_CURRICULUM|2025", -1),
        "gather_done_callback": ("GATHER|done", -1),
        "handle_document_intent": ("DOCUSE|info", 6),
        "handle_form_choice": ("FORM|show_all", 2),
        "handle_pathway_choice": ("PATHWAY|training", -1),
        "handle_amend_draft": ("AMEND|cancel", -1),
        "handle_approval_approve": ("APPROVE|draft", -1),
        "handle_approval_submit": ("APPROVE|submit", -1),
        "handle_attachment_confirm": ("ATTACH|yes", -1),
        "handle_approval_edit": ("EDIT|reflection", -1),
        "handle_edit_field": ("FIELD|reflection", 5),
        "handle_quick_improve": ("IMPROVE|reflection", -1),
        "handle_review_draft": ("REVIEW|draft", -1),
        "voice_collect_example": ("VOICE|more", 8),
    }
    payload = None
    if kind == "callback":
        if slot.callback == "handle_callback":
            payload, target = next((p, t) for p, t in [
                ("ACTION|cancel", -1), ("CANCEL|draft", -1), ("ACTION|continue_thin", -1),
                ("ACTION|retry_recommend", 6), ("ACTION|back_to_missing", -1),
                ("CASE|new", 6), ("FILING_CURRICULUM|select|2025", 3)]
                if slot.handler.pattern.match(p))
        else:
            payload, target = callbacks[slot.callback]
    else:
        targets = {"setup_username": 0, "setup_password": 0, "handle_case_input": 0,
            "handle_gathering_input": 0, "handle_template_review_text": 0,
            "handle_form_search_text": 2, "handle_pending_media_context": 14,
            "voice_collect_example": 8, "handle_template_review_media": 9,
            "handle_approval_media_feedback": 3, "handle_mid_conversation_text": 6,
            "handle_edit_value_with_intent": -1, "handle_edit_value": -1,
            "_setup_wrong_input": slot.state, "passwordless_awaiting_text": 15}
        target = targets[slot.callback]
    return {"from": slot.state, "input": kind, "payload": payload, "to": target}


def reviewed_units(slots):
    """The single reviewed semantic catalogue used by manual and audited evidence."""
    from tests.whole_bot_coverage import CATEGORIES
    import bot
    units = {}
    def put(key, kind, category, candidates=(), expectation=None, members=()):
        entry = units.setdefault(key, {"id": key, "kind": kind, "classification": category,
            "registration_candidates": [], "review_required": False, "expectation": expectation,
            "members": list(members)})
        entry["registration_candidates"] = sorted(set(entry["registration_candidates"]) | {s.key for s in candidates})
    for slot in slots:
        for command in slot.commands:
            put("command:" + command, "command", CATEGORIES[slot.callback], [slot])
    for branch in sorted(CALLBACK_BRANCHES):
        sample = branch.replace("*", "2025" if branch.startswith("FILING_CURRICULUM|") else "aaaaaaaa")
        candidates = [s for s in slots if isinstance(s.handler, CallbackQueryHandler) and s.handler.pattern.match(sample)]
        put("callback:" + branch, "callback-branch", "protected-boundary" if branch in PROTECTED_BRANCHES else "internal", candidates)
    samples = input_samples(None)
    for slot in slots:
        if slot.state is None:
            continue
        kinds = ["callback"] if isinstance(slot.handler, CallbackQueryHandler) else [k for k, u in samples.items() if slot.handler.check_update(u)]
        for kind in kinds:
            key = f"state:{slot.state}:{slot.callback}:{slot.selector}:{kind}/slot:{slot.key}"
            put(key, "state-input", "protected-boundary" if slot.callback in {"setup_password", "setup_retry_login"} else "internal", [slot],
                state_expectation(slot, kind))
            for payload, target in (("retry_recommend", "AWAIT_FORM_CHOICE"), ("retry_template", "AWAIT_APPROVAL")):
                if kind == "callback" and slot.handler.pattern.match("ACTION|" + payload):
                    put(key + "/transition:" + payload, "state-input-transition", "internal", [slot],
                        {"from": slot.state, "to": getattr(bot, target), "input": "ACTION|" + payload})
    families = {family for s in slots if isinstance(s.handler, CallbackQueryHandler)
                for family in re.findall(r"([A-Z][A-Z_]+)\\\|", s.selector)}
    for family in sorted(families):
        put("callback-family:" + family, "callback-family", "internal", members=[k for k in units if k.startswith("callback:" + family + "|")])
    for behaviour in BEHAVIOURS:
        put("behaviour:" + behaviour, "behaviour", "internal")
    return dict(sorted(units.items()))


# Registration candidate paths shift when consent moves into case_conv.
# FORM branch candidates also include the tested post-setup case entry point.
# 9 Oct 2026: retired KC callbacks removed from active approval ownership.
# 9 Oct 2026: final main/wider-journey merge, own-word provenance and draft-message fix.
# 10 Oct 2026: rechecked after provenance fixes; semantic requirements unchanged.
CATALOGUE_DIGEST = 'a8ae9cc094612f3bd05722452b9b3a613dc3f23b3e2d3a63e3bbec79503cef8d'


def requirements_digest(units):
    keys = ("id", "kind", "classification", "registration_candidates", "review_required", "expectation", "members")
    declarations = [{k: u.get(k) for k in keys} for u in sorted(units, key=lambda u: u["id"])]
    return hashlib.sha256(json.dumps(declarations, sort_keys=True).encode()).hexdigest()


def evidence_matches(unit, event, candidates, scenario=""):
    """One offline credit authority for both the audit and manual Coverage API."""
    kind, key = unit["kind"], unit["id"]
    if kind == "behaviour":
        return scenario.split("::")[-1].startswith(BEHAVIOURS[key.removeprefix("behaviour:")])
    if event.get("callback") not in {s.callback for s in candidates}:
        return False
    if kind == "command":
        if event.get("command") != key.removeprefix("command:").split("/")[0]:
            return False
        expected = unit.get("expectation")
        if expected and event.get("argument_branch") != expected["arguments"]:
            return False
    elif kind == "callback-branch":
        if not event.get("payload") or payload_branch(event["payload"]) != key.removeprefix("callback:"):
            return False
    elif kind.startswith("state-input"):
        expected = unit["expectation"]
        if (not event.get("dispatched") or event.get("slot") not in unit["registration_candidates"]
                or event.get("source_state") != expected["from"]
                or "resulting_state" not in event or not event.get("result_asserted")
                or event.get("asserted_result") != event["resulting_state"]
                or event["resulting_state"] != expected["to"]):
            return False
        if kind == "state-input-transition":
            if event.get("payload") != expected["input"] or event["resulting_state"] != expected["to"]:
                return False
        elif event.get("kind") != expected["input"] or (event["kind"] == "callback" and
                event.get("payload") != expected["payload"]):
            return False
    if unit["classification"] == "protected-boundary":
        if kind == "command":
            return bool(event.get("boundary_asserted") and (event.get("dispatched") or event.get("boundaries")))
        return bool(event.get("boundaries") or event.get("guard_asserted"))
    return True


def catalogue_receipt(observations):
    app = build_offline_application()
    slots = inventory(app)
    records = [(node, event) for node, events in observations["tests"].items() for event in events]
    requirements = reviewed_units(slots)
    by_key = {s.key: s for s in slots}
    units = []
    for key, unit in requirements.items():
        kind = unit["kind"]
        if kind == "callback-family":
            continue
        candidates = [by_key[k] for k in unit["registration_candidates"]]
        relevant = [(node, e) for node, e in records if evidence_matches(unit, e, candidates, node)]
        protected = unit["classification"] == "protected-boundary"
        proof = sorted({node for node, _ in relevant})
        boundary = protected or any(e.get("boundaries") for _, e in relevant)
        units.append({**unit, "status": ("protected-boundary-covered" if boundary else "covered") if proof else "uncovered", "scenarios": proof})
    completed = {u["id"]: u for u in units}
    for key, unit in requirements.items():
        if unit["kind"] == "callback-family":
            branches = [completed[k] for k in unit["members"]]
            complete = bool(branches) and all(u["scenarios"] for u in branches)
            units.append({**unit, "status": "covered" if complete else "uncovered",
                          "scenarios": sorted({n for u in branches for n in u["scenarios"]}) if complete else []})
    emitted = {payload_branch(p) for p in observations["emitted"]}
    unknown = sorted(emitted - CALLBACK_BRANCHES)
    missing = [u["id"] for u in units if not u["scenarios"]]
    drift = registration_digest(slots) != REGISTRATION_DIGEST or producer_digest() != PRODUCER_DIGEST or requirements_digest(units) != CATALOGUE_DIGEST
    return {"schema": 1, "layer": "catalogue", "status": "failed" if drift or unknown or observations["errors"] else "pending" if missing else "passed",
            "registration_digest": registration_digest(slots), "producer_digest": producer_digest(), "requirements_digest": requirements_digest(units),
            "catalogue_complete": not drift and not unknown and not missing and not observations["errors"],
            "unclassified": unknown, "uncovered": missing, "units": units, "errors": observations["errors"],
            "grouping": "State credit requires the exact dispatched slot, source, input and asserted committed result. Protected commands require branch-specific asserted boundaries. Manual and audited evidence use evidence_matches."}


AUDIT_TESTS = """test_reset_command test_funnel_metrics test_flow_walker test_e2e_offline test_whole_bot_coverage test_health_bot
 test_gathering_mode test_attachment_handoff test_essential_first_gate test_supervisor_bot
 test_consent_gate test_missing_essentials_replay_guard test_concurrent_user_isolation
 test_modality_clause_coverage test_setup_manual_profile_fallback test_setup_connect_flow test_attachment_upload_consent
 test_curriculum_filing_recovery test_channel_contract test_forms test_kc_edit_retention
 test_filing_reliability test_filing_reliability_matrix test_kc_quality test_rcem_ai_policy test_button_routing""".split()

if __name__ == "__main__":
    print(" ".join("tests/" + name + ".py" for name in AUDIT_TESTS))
