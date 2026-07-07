"""Hermes profile / bridge contract integration tests.

Validates that:

* The three Hermes docs (PROFILE_PROMPT, INTEGRATION_GUIDE, RICH_MESSAGE_GUIDE)
  exist, are non-empty, and contain the required honesty statements.
* The docs forbid token sharing between the test bot and the live beta bot.
* The docs declare the deterministic-engine boundary.
* The bridge contract (hermes_bridge_contract.py) is import-clean: no
  python-telegram-bot, no BWS/secrets, no network.
* The bridge converts valid Hermes-shaped payloads to InboundDecision
  correctly, covering all three dispositions (HANDLE, REFUSE_GROUP,
  REFUSE_EMPTY).
* The bridge serialises ChannelReply objects to plain dicts.
* The bridge round-trips a ChannelReply through serialise → deserialise.

No network, Telegram, Kaizen, BWS, or bot-token access in any test here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HERMES_DOCS_DIR = REPO_ROOT / "docs" / "hermes"

PROFILE_PROMPT = HERMES_DOCS_DIR / "PROFILE_PROMPT.md"
INTEGRATION_GUIDE = HERMES_DOCS_DIR / "INTEGRATION_GUIDE.md"
RICH_MESSAGE_GUIDE = HERMES_DOCS_DIR / "RICH_MESSAGE_GUIDE.md"

ALL_HERMES_DOCS = (PROFILE_PROMPT, INTEGRATION_GUIDE, RICH_MESSAGE_GUIDE)


# ── existence ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("doc", ALL_HERMES_DOCS, ids=lambda p: p.name)
def test_hermes_doc_exists_and_non_empty(doc: Path) -> None:
    assert doc.is_file(), f"missing Hermes doc: {doc}"
    body = doc.read_text(encoding="utf-8")
    assert body.strip(), f"Hermes doc is empty: {doc}"


# ── token isolation ────────────────────────────────────────────────────────


@pytest.mark.parametrize("doc", ALL_HERMES_DOCS, ids=lambda p: p.name)
def test_hermes_doc_declares_no_token_sharing(doc: Path) -> None:
    """Every Hermes doc must state that the test and live tokens are separate.

    We accept any of several equivalent phrasings.  The important
    invariant is that a reader cannot mistake the test bot token for the
    live beta token or assume they can be shared.
    """
    lower = doc.read_text(encoding="utf-8").lower()
    token_separation_signals = (
        "vnext_telegram_bot_token",
        "portfolio_guru_vnext",
        "separate token",
        "separate bot",
        "test bot token",
        "never co-polled",
        "never shared",
        "never be shared",
        "no shared telegram token",
        "two tokens",
    )
    assert any(sig in lower for sig in token_separation_signals), (
        f"{doc.name} does not contain any token-separation language. "
        f"Every Hermes doc must make clear that the test and live beta "
        f"tokens are separate and must never be co-polled or shared."
    )


# ── deterministic engine boundary ─────────────────────────────────────────


@pytest.mark.parametrize("doc", ALL_HERMES_DOCS, ids=lambda p: p.name)
def test_hermes_doc_preserves_deterministic_engine_boundary(doc: Path) -> None:
    """Every Hermes doc must acknowledge the deterministic engine boundary.

    The Hermes profile is a conversational layer; the engine must remain
    deterministic.  We check for language that distinguishes the
    conversational front (Hermes / profile) from the deterministic back
    (engine / filing system / Playwright).
    """
    lower = doc.read_text(encoding="utf-8").lower()
    boundary_signals = (
        "deterministic",
        "deterministic engine",
        "portfolio guru engine",
        "portfolio guru deterministic",
        "filing system",
        "channel_contract",
        "accept_inbound",
        "inboundmessage",
        "engine contract",
    )
    assert any(sig in lower for sig in boundary_signals), (
        f"{doc.name} does not mention the deterministic engine boundary. "
        f"Every Hermes doc must make clear that the engine, not the "
        f"conversational layer, owns filing decisions."
    )


# ── no Kaizen write claims ─────────────────────────────────────────────────


@pytest.mark.parametrize("doc", ALL_HERMES_DOCS, ids=lambda p: p.name)
def test_hermes_doc_does_not_claim_direct_kaizen_write(doc: Path) -> None:
    """Docs must never claim the Hermes profile writes to Kaizen directly.

    The profile may call the engine; the engine's Playwright filer
    writes to Kaizen after user approval.  The profile itself never writes.
    """
    lower = doc.read_text(encoding="utf-8").lower()
    # Any line that claims the profile/Hermes directly writes to Kaizen.
    # We allow "does not write", "never writes", etc.
    direct_write_phrases = (
        "hermes writes to kaizen",
        "profile writes to kaizen",
        "hermes saves to kaizen",
        "profile saves to kaizen",
    )
    for phrase in direct_write_phrases:
        assert phrase not in lower, (
            f"{doc.name} contains {phrase!r} which incorrectly claims "
            f"the Hermes profile writes directly to Kaizen."
        )


# ── concise-answer / progressive-disclosure contract ──────────────────────


def test_profile_prompt_caps_scope_and_capability_answer_length() -> None:
    """The profile must cap scope/capability answers and disclose progressively.

    A trainee asking a scope question ("What kind of cases can I share?")
    must get a short, mobile-first answer followed by an invitation to go
    deeper — never the whole product manual dumped in one message. The
    profile prompt is where the Hermes LLM is told to do this, so the
    contract is asserted against PROFILE_PROMPT.md directly.
    """
    lower = PROFILE_PROMPT.read_text(encoding="utf-8").lower()

    # An explicit short-length budget (e.g. "5-7 lines" / "5 to 7 lines").
    has_length_budget = bool(
        re.search(r"\b5\s*(?:-|–|to)\s*7\s*(?:short\s*)?lines?\b", lower)
    )
    assert has_length_budget, (
        "PROFILE_PROMPT.md must state an explicit short answer-length "
        "budget (e.g. '5-7 lines') for scope/capability questions so the "
        "Hermes test bot stops dumping the full manual."
    )

    # An explicit progressive-disclosure cue: answer short, then invite more.
    progressive_signals = (
        "progressive disclosure",
        "then invite",
        "invite a follow-up",
        "offer an example",
        "offer examples",
        "let the user",
    )
    assert any(sig in lower for sig in progressive_signals), (
        "PROFILE_PROMPT.md must tell the profile to answer briefly and "
        "then invite a follow-up or offer examples (progressive "
        "disclosure)."
    )

    # An explicit guard against dumping the manual / full catalogue.
    no_dump_signals = (
        "do not dump",
        "don't dump",
        "never paste the full",
        "never dump",
        "not the whole",
        "whole product manual",
        "entire form catalogue",
        "full form catalogue",
    )
    assert any(sig in lower for sig in no_dump_signals), (
        "PROFILE_PROMPT.md must explicitly forbid pasting the whole "
        "product manual / full form catalogue in a single answer."
    )


def test_profile_prompt_has_concise_shareable_cases_answer() -> None:
    """The profile must encode a worked, concise 'what can I share?' answer.

    This is the exact question that produced the overlong reply. The
    profile carries a short reference answer so the model has a concrete
    target: anonymised material, identifiers kept out, the approval gate,
    and an invitation to continue — not a manual.
    """
    text = PROFILE_PROMPT.read_text(encoding="utf-8")
    lower = text.lower()

    assert "what kind of cases can i share" in lower, (
        "PROFILE_PROMPT.md must include the worked scope question "
        "'What kind of cases can I share?' with a short reference answer."
    )

    # The reference answer must convey the shareable scope...
    assert "anonymised" in lower, (
        "The shareable-cases reference answer must say the material is "
        "anonymised."
    )
    # ...keep patient identifiers out (privacy reminder)...
    assert "identifier" in lower, (
        "The shareable-cases reference answer must remind the user to keep "
        "patient identifiers out."
    )
    # ...preserve the approval gate (nothing saved until approved)...
    assert "approve" in lower, (
        "The shareable-cases reference answer must preserve the approval "
        "gate (nothing is saved until the user approves)."
    )
    # ...and end by inviting a follow-up / offering an example.
    invite_signals = ("example", "send your first case", "want")
    assert any(sig in lower for sig in invite_signals), (
        "The shareable-cases reference answer must invite a follow-up or "
        "offer an example rather than stopping at a wall of text."
    )


CAPABILITY_MAP = REPO_ROOT / "docs" / "demo" / "HERMES_CAPABILITY_MAP.md"
WHATSAPP_ROLLOUT_PLAN = REPO_ROOT / "docs" / "hermes" / "WHATSAPP_ROLLOUT_PLAN.md"


# ── Hermes is optional thin transport, not the product brain ───────────────


def test_rollout_plan_marks_hermes_profile_optional() -> None:
    """The WhatsApp rollout plan must state the Hermes profile is optional.

    The lean direction is that WhatsApp is only a channel connector for a
    dedicated Portfolio Guru number/account. A Hermes profile is one optional
    thin transport, never a required layer — so the plan must say so explicitly
    and must offer a non-Hermes (direct) connector path.
    """
    lower = WHATSAPP_ROLLOUT_PLAN.read_text(encoding="utf-8").lower()

    assert "optional" in lower and "hermes profile" in lower, (
        "WHATSAPP_ROLLOUT_PLAN.md must state the Hermes profile is optional."
    )
    assert "thin transport" in lower or "thin channel connector" in lower, (
        "WHATSAPP_ROLLOUT_PLAN.md must frame Hermes as thin transport only."
    )
    # A direct (non-Hermes) connector must be a documented path.
    assert "direct" in lower and "connector" in lower, (
        "WHATSAPP_ROLLOUT_PLAN.md must document a direct channel connector "
        "path that needs no Hermes profile."
    )


def test_rollout_plan_keeps_engine_as_product_brain_not_emgurus_fanout() -> None:
    """Portfolio Guru's deterministic engine — not Hermes/EMGurus — is the brain."""
    lower = WHATSAPP_ROLLOUT_PLAN.read_text(encoding="utf-8").lower()

    assert "deterministic portfolio guru engine" in lower or (
        "deterministic" in lower and "product brain" in lower
    ), "WHATSAPP_ROLLOUT_PLAN.md must name the deterministic engine as the brain."
    # Tester rollout must not route through the general EMGurus account / fan-out.
    assert "must not use the general emgurus whatsapp account" in lower, (
        "WHATSAPP_ROLLOUT_PLAN.md must forbid routing testers through the "
        "general EMGurus WhatsApp account."
    )
    assert "fan-out" in lower or "fanout" in lower or "fan out" in lower, (
        "WHATSAPP_ROLLOUT_PLAN.md must state Portfolio Guru is not an EMGurus "
        "fan-out agent/gateway."
    )


# ── BWS secret name vs runtime alias ──────────────────────────────────────


def test_integration_guide_names_actual_bws_secret() -> None:
    """INTEGRATION_GUIDE must name TELEGRAM_BOT_TOKEN_PORTFOLIO_TEST as the BWS secret.

    That is the key visible in Bitwarden; PORTFOLIO_GURU_VNEXT_TELEGRAM_BOT_TOKEN
    is only the local/OpenClaw runtime alias and must not be presented as the BWS name.
    """
    text = INTEGRATION_GUIDE.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN_PORTFOLIO_TEST" in text, (
        "INTEGRATION_GUIDE.md must contain 'TELEGRAM_BOT_TOKEN_PORTFOLIO_TEST' "
        "(the actual BWS secret name). "
        "PORTFOLIO_GURU_VNEXT_TELEGRAM_BOT_TOKEN is only the OpenClaw runtime alias."
    )


def test_docs_do_not_present_alias_as_bws_name() -> None:
    """No Hermes doc or capability map may present the runtime alias as the BWS secret name.

    Forbidden patterns that incorrectly imply PORTFOLIO_GURU_VNEXT_TELEGRAM_BOT_TOKEN
    is a Bitwarden secret key:
      - 'bws: `portfolio_guru_vnext_telegram_bot_token`'
      - 'confirm `portfolio_guru_vnext_telegram_bot_token` is in bws'
    """
    forbidden = (
        "bws: `portfolio_guru_vnext_telegram_bot_token`",
        "confirm `portfolio_guru_vnext_telegram_bot_token` is in bws",
    )
    docs_to_check = list(ALL_HERMES_DOCS) + [CAPABILITY_MAP]
    for doc in docs_to_check:
        if not doc.is_file():
            continue
        lower = doc.read_text(encoding="utf-8").lower()
        for pattern in forbidden:
            assert pattern not in lower, (
                f"{doc.name} contains {pattern!r}, which presents the OpenClaw "
                "runtime alias as the Bitwarden secret name. Use "
                "'BWS secret name: TELEGRAM_BOT_TOKEN_PORTFOLIO_TEST; "
                "OpenClaw/runtime alias: PORTFOLIO_GURU_VNEXT_TELEGRAM_BOT_TOKEN' instead."
            )


# ── bridge contract: import clean ─────────────────────────────────────────


def test_bridge_contract_imports_without_telegram() -> None:
    """The bridge must not pull in python-telegram-bot.

    Hermes runs in a separate process that may not have the ptb library
    installed.  The subprocess check mirrors the pattern used in
    test_channel_contract.py for the same invariant.
    """
    import os
    import subprocess

    backend_dir = REPO_ROOT / "backend"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, hermes_bridge_contract; "
            "sys.exit(1 if 'telegram' in sys.modules else 0)",
        ],
        cwd=str(backend_dir),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"hermes_bridge_contract import pulled in telegram:\n{result.stderr}"
    )


def test_bridge_contract_imports_without_bws_or_secrets() -> None:
    """The bridge must not import any BWS/secrets library."""
    import os
    import subprocess

    backend_dir = REPO_ROOT / "backend"
    sensitive_modules = ("bws", "boto3", "keyring", "bitwarden")
    check = "; ".join(
        f"sys.exit(1) if {m!r} in sys.modules else None"
        for m in sensitive_modules
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys, hermes_bridge_contract; {check}",
        ],
        cwd=str(backend_dir),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"hermes_bridge_contract imported a secrets/BWS module:\n{result.stderr}"
    )


# ── bridge: inbound_from_payload ──────────────────────────────────────────


def _import_bridge():
    import importlib
    import sys
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    return importlib.import_module("hermes_bridge_contract")


def _channel_contract():
    import importlib
    import sys
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    return importlib.import_module("channel_contract")


def _channel_actions():
    import importlib
    import sys
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    return importlib.import_module("channel_actions")


def _valid_payload(**overrides) -> dict:
    base = {
        "channel": "telegram",
        "conversation_id": "tg:chat:9999",
        "gateway_user_id": "hermes-test-user",
        "scope": "direct",
        "text": "62M chest pain, possible STEMI, CBD please",
        "media": [],
        "private": True,
    }
    base.update(overrides)
    return base


def test_bridge_handle_direct_text_message() -> None:
    bridge = _import_bridge()
    cc = _channel_contract()

    decision = bridge.inbound_from_payload(_valid_payload())

    assert decision.disposition is cc.InboundDisposition.HANDLE
    assert decision.message is not None
    assert decision.refusal is None
    assert decision.fresh_start is True


def test_bridge_refuse_group_scope() -> None:
    bridge = _import_bridge()
    cc = _channel_contract()
    ca = _channel_actions()

    decision = bridge.inbound_from_payload(_valid_payload(scope="group"))

    assert decision.disposition is cc.InboundDisposition.REFUSE_GROUP
    assert decision.message is None
    assert isinstance(decision.refusal, ca.ChannelReply)


def test_bridge_refuse_empty_content() -> None:
    bridge = _import_bridge()
    cc = _channel_contract()

    decision = bridge.inbound_from_payload(_valid_payload(text=None, media=[]))

    assert decision.disposition is cc.InboundDisposition.REFUSE_EMPTY


def test_bridge_refuse_whitespace_only_text() -> None:
    bridge = _import_bridge()
    cc = _channel_contract()

    decision = bridge.inbound_from_payload(_valid_payload(text="   "))

    assert decision.disposition is cc.InboundDisposition.REFUSE_EMPTY


def test_bridge_handle_media_only_payload() -> None:
    bridge = _import_bridge()
    cc = _channel_contract()

    payload = _valid_payload(
        text=None,
        media=[{"kind": "voice", "uri": "gw://blob/x", "mime_type": "audio/ogg"}],
    )
    decision = bridge.inbound_from_payload(payload)

    assert decision.disposition is cc.InboundDisposition.HANDLE
    assert len(decision.message.media) == 1
    assert decision.message.media[0].kind == "voice"


def test_bridge_strips_malformed_media_items() -> None:
    bridge = _import_bridge()
    cc = _channel_contract()

    payload = _valid_payload(
        media=[
            {"kind": "photo"},            # valid
            {},                            # no kind — dropped
            {"uri": "gw://blob/y"},        # no kind — dropped
            {"kind": "", "uri": "x"},      # empty kind — dropped
        ]
    )
    decision = bridge.inbound_from_payload(payload)

    assert decision.disposition is cc.InboundDisposition.HANDLE
    assert len(decision.message.media) == 1
    assert decision.message.media[0].kind == "photo"


def test_bridge_raises_on_missing_conversation_id() -> None:
    bridge = _import_bridge()

    with pytest.raises(ValueError, match="conversation_id"):
        bridge.inbound_from_payload(_valid_payload(conversation_id=""))


def test_bridge_raises_on_unknown_channel() -> None:
    bridge = _import_bridge()

    with pytest.raises(ValueError, match="channel"):
        bridge.inbound_from_payload(_valid_payload(channel="pigeon"))


def test_bridge_raises_on_unknown_scope() -> None:
    bridge = _import_bridge()

    with pytest.raises(ValueError, match="scope"):
        bridge.inbound_from_payload(_valid_payload(scope="broadcast"))


def test_bridge_defaults_private_to_true() -> None:
    bridge = _import_bridge()

    payload = _valid_payload()
    payload.pop("private", None)
    decision = bridge.inbound_from_payload(payload)

    assert decision.message.private is True


def test_bridge_respects_all_channel_values() -> None:
    bridge = _import_bridge()
    cc = _channel_contract()

    for channel in ("telegram", "whatsapp", "web"):
        decision = bridge.inbound_from_payload(_valid_payload(channel=channel))
        assert decision.disposition is cc.InboundDisposition.HANDLE
        assert decision.message.session.channel.value == channel


# ── bridge: serialise_reply ────────────────────────────────────────────────


def test_serialise_reply_body_and_continuation() -> None:
    bridge = _import_bridge()
    ca = _channel_actions()

    reply = ca.ChannelReply(
        body="Draft ready.",
        continuation="Tap Approve to save.",
        actions=(),
    )
    result = bridge.serialise_reply(reply)

    assert result["body"] == "Draft ready."
    assert result["continuation"] == "Tap Approve to save."
    assert result["actions"] == []


def test_serialise_reply_actions_preserve_order() -> None:
    bridge = _import_bridge()
    ca = _channel_actions()

    reply = ca.ChannelReply(
        body="Which form?",
        continuation=None,
        actions=(
            ca.ChannelAction("cbd", "Case-Based Discussion"),
            ca.ChannelAction("dops", "DOPS"),
        ),
    )
    result = bridge.serialise_reply(reply)

    assert len(result["actions"]) == 2
    assert result["actions"][0] == {"action_id": "cbd", "label": "Case-Based Discussion"}
    assert result["actions"][1] == {"action_id": "dops", "label": "DOPS"}


def test_serialise_decision_handle_has_no_refusal() -> None:
    bridge = _import_bridge()
    cc = _channel_contract()

    decision = bridge.inbound_from_payload(_valid_payload())
    assert decision.disposition is cc.InboundDisposition.HANDLE

    d = bridge.serialise_decision(decision)
    assert d["disposition"] == "handle"
    assert "refusal" not in d
    assert d["fresh_start"] is True


def test_serialise_decision_refusal_has_body() -> None:
    bridge = _import_bridge()
    cc = _channel_contract()

    decision = bridge.inbound_from_payload(_valid_payload(scope="group"))
    assert decision.disposition is cc.InboundDisposition.REFUSE_GROUP

    d = bridge.serialise_decision(decision)
    assert d["disposition"] == "refuse_group"
    assert "refusal" in d
    assert d["refusal"]["body"]  # non-empty refusal body


# ── bridge: deserialise_reply round-trip ──────────────────────────────────


def test_deserialise_reply_round_trip() -> None:
    bridge = _import_bridge()
    ca = _channel_actions()

    original = ca.ChannelReply(
        body="Draft saved.",
        continuation="Send a new case when you're ready.",
        actions=(ca.ChannelAction("new_case", "📥 New case"),),
    )
    serialised = bridge.serialise_reply(original)
    restored = bridge.deserialise_reply(serialised)

    assert restored.body == original.body
    assert restored.continuation == original.continuation
    assert len(restored.actions) == 1
    assert restored.actions[0].action_id == "new_case"
    assert restored.actions[0].label == "📥 New case"


def test_deserialise_reply_drops_malformed_actions() -> None:
    bridge = _import_bridge()

    data = {
        "body": "Choose a form.",
        "continuation": None,
        "actions": [
            {"action_id": "cbd", "label": "CBD"},      # valid
            {"label": "DOPS"},                          # missing action_id — dropped
            {"action_id": "mcr"},                       # missing label — dropped
            {},                                         # empty — dropped
        ],
    }
    reply = bridge.deserialise_reply(data)

    assert len(reply.actions) == 1
    assert reply.actions[0].action_id == "cbd"
