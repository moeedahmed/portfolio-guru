"""Channel-neutral inbound contract for the EMGurus WhatsApp Gateway boundary.

Portfolio Guru sits *behind* the single EMGurus WhatsApp Gateway as a 1:1
portfolio service. The gateway owns the WhatsApp number and DM-vs-group
routing; Portfolio Guru owns only the direct (1:1) portfolio conversation and
must refuse group/community scope. These tests pin that boundary:

* an inbound message is a channel-neutral envelope (channel + session context +
  content), never a Telegram object;
* portfolio content is private by default;
* :func:`accept_inbound` handles DIRECT scope and refuses GROUP scope, with a
  refusal that renders losslessly to any channel (no Telegram dependency);
* the whole path is import-clean of ``python-telegram-bot`` so it can run inside
  a gateway process that has never heard of Telegram.

They use no network, no credentials, and no live services.
"""

from __future__ import annotations

import sys

import pytest

from channel_actions import ChannelReply, render_numbered
from channel_contract import (
    Channel,
    ConversationScope,
    InboundDecision,
    InboundDisposition,
    InboundMessage,
    MediaRef,
    SessionRef,
    accept_inbound,
)


def _session(channel: Channel = Channel.WHATSAPP) -> SessionRef:
    return SessionRef(
        channel=channel,
        conversation_id="wa:+440000000000",
        gateway_user_id="emgurus-user-123",
    )


def test_session_ref_requires_channel_and_conversation_id():
    with pytest.raises(ValueError):
        SessionRef(channel=Channel.WHATSAPP, conversation_id="")


def test_contentless_message_is_refused_not_crashed():
    """The boundary receives untrusted input: a no-content turn is refused
    at routing time, not rejected at construction."""
    msg = InboundMessage(session=_session(), scope=ConversationScope.DIRECT)
    assert accept_inbound(msg).disposition is InboundDisposition.REFUSE_EMPTY


def test_portfolio_content_is_private_by_default():
    msg = InboundMessage(
        session=_session(), scope=ConversationScope.DIRECT, text="58M chest pain"
    )
    assert msg.private is True


def test_direct_text_message_is_handled():
    msg = InboundMessage(
        session=_session(), scope=ConversationScope.DIRECT, text="58M chest pain, CBD"
    )
    decision = accept_inbound(msg)
    assert isinstance(decision, InboundDecision)
    assert decision.disposition is InboundDisposition.HANDLE
    assert decision.message is msg
    assert decision.refusal is None


def test_media_only_message_is_handled():
    msg = InboundMessage(
        session=_session(),
        scope=ConversationScope.DIRECT,
        media=(MediaRef(kind="voice", uri="gw://blob/abc"),),
    )
    decision = accept_inbound(msg)
    assert decision.disposition is InboundDisposition.HANDLE


def test_group_scope_is_refused_as_gateway_responsibility():
    msg = InboundMessage(
        session=_session(), scope=ConversationScope.GROUP, text="hi everyone"
    )
    decision = accept_inbound(msg)
    assert decision.disposition is InboundDisposition.REFUSE_GROUP
    assert decision.message is None
    # The refusal is channel-neutral and never leaks portfolio content.
    assert isinstance(decision.refusal, ChannelReply)
    assert "hi everyone" not in decision.refusal.full_text()


def test_group_refusal_renders_without_telegram():
    """A gateway process need not have python-telegram-bot installed."""
    msg = InboundMessage(
        session=_session(), scope=ConversationScope.GROUP, text="hello"
    )
    rendered = render_numbered(accept_inbound(msg).refusal)
    assert rendered  # renders as plain/numbered text, no Telegram import required


def test_empty_message_is_refused():
    msg = InboundMessage(
        session=_session(), scope=ConversationScope.DIRECT, text="   "
    )
    decision = accept_inbound(msg)
    assert decision.disposition is InboundDisposition.REFUSE_EMPTY


def test_contract_is_channel_agnostic_across_channels():
    """Same envelope, different channel — identical disposition."""
    for channel in (Channel.WHATSAPP, Channel.TELEGRAM, Channel.WEB):
        msg = InboundMessage(
            session=_session(channel), scope=ConversationScope.DIRECT, text="CBD please"
        )
        assert accept_inbound(msg).disposition is InboundDisposition.HANDLE


def test_handle_decision_has_fresh_start_true():
    """fresh_start is always True until Portfolio Guru tracks server-side sessions.

    The gateway (OpenClaw WhatsApp bridge) is authoritative for session
    continuity in the interim: it uses an in-memory TTL to suppress the
    "Starting…" ACK on continuation turns.
    """
    msg = InboundMessage(
        session=_session(), scope=ConversationScope.DIRECT, text="CBD review"
    )
    decision = accept_inbound(msg)
    assert decision.disposition is InboundDisposition.HANDLE
    assert decision.fresh_start is True


def test_module_imports_without_telegram():
    """The inbound contract must not pull in python-telegram-bot.

    A future gateway adapter imports this module to decide routing before any
    Telegram-specific code is loaded, so the import graph must stay clean. The
    check runs in a fresh subprocess: reloading channel_contract in-process
    rebinds its classes and leaves importers (e.g. webhook_server) holding stale
    references, which poisoned every test that ran afterwards.
    """
    import os
    import subprocess

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, channel_contract; "
            "sys.exit(1 if 'telegram' in sys.modules else 0)",
        ],
        cwd=backend_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"channel_contract import pulled in telegram:\n{result.stderr}"
    )
