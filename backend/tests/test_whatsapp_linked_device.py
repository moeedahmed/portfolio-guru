"""Tests for the direct WhatsApp linked-device transport normaliser.

The linked-device connector is transport only: it maps a raw Baileys /
WhatsApp-Web multi-device message envelope onto the same channel-neutral
:class:`~channel_contract.InboundMessage` every channel shares, and carries no
Portfolio Guru product logic. These tests pin that boundary:

* a 1:1 text envelope normalises to a DIRECT, private, handled turn;
* group / broadcast / newsletter / status JIDs become GROUP scope and are
  refused as a gateway responsibility, and the refusal never echoes content;
* WhatsApp media containers map onto the engine's neutral media kinds, with a
  push-to-talk clip treated as a voice note and no media key ever forwarded;
* the forward payload matches the repo-owned inbound bridge contract;
* the dry-run harness returns routing metadata only, never clinical content;
* the module is import-clean of python-telegram-bot and the product engine, and
  carries no shared EMGurus routing wording.

No network, no credentials, no live WhatsApp/Telegram service.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from channel_contract import ConversationScope, InboundDisposition
import whatsapp_linked_device as wld


def _text_envelope(
    text: str = "58M chest pain, CBD reflection",
    jid: str = "447700900000@s.whatsapp.net",
) -> dict:
    return {
        "key": {"remoteJid": jid, "fromMe": False, "id": "MSGID1"},
        "message": {"conversation": text},
        "pushName": "Dr Smith",
    }


def test_direct_text_envelope_normalises_to_handled_private_turn():
    msg = wld.normalize_message(_text_envelope())
    assert msg.scope is ConversationScope.DIRECT
    assert msg.private is True
    assert msg.text == "58M chest pain, CBD reflection"
    assert msg.session.conversation_id == "wa:447700900000@s.whatsapp.net"
    assert wld.normalize_and_route(_text_envelope()).decision.disposition is (
        InboundDisposition.HANDLE
    )


def test_lid_direct_turn_keeps_lid_session_but_prefers_phone_gateway_user_id():
    env = {
        "key": {
            "remoteJid": "84125843243120@lid",
            "senderPn": "447700900000@s.whatsapp.net",
            "senderLid": "84125843243120@lid",
            "id": "LID1",
        },
        "message": {"conversation": "hello"},
    }
    msg = wld.normalize_message(env)

    assert msg.session.conversation_id == "wa:84125843243120@lid"
    assert msg.session.gateway_user_id == "447700900000@s.whatsapp.net"
    assert wld.to_inbound_payload(env)["gateway_user_id"] == "447700900000@s.whatsapp.net"


def test_extended_text_message_is_extracted():
    env = {
        "key": {"remoteJid": "447700900000@s.whatsapp.net", "id": "M2"},
        "message": {"extendedTextMessage": {"text": "please draft my CBD"}},
    }
    assert wld.normalize_message(env).text == "please draft my CBD"


@pytest.mark.parametrize(
    "jid",
    [
        "120363000000000000@g.us",
        "447700900000@broadcast",
        "12345@newsletter",
        "status@broadcast",
    ],
)
def test_non_direct_jids_are_refused_as_group(jid: str):
    secret = "patient John Doe MRN 12345 chest pain"
    env = _text_envelope(text=secret, jid=jid)
    normalized = wld.normalize_and_route(env)
    assert normalized.message.scope is ConversationScope.GROUP
    assert normalized.decision.disposition is InboundDisposition.REFUSE_GROUP
    # The refusal must never replay private content into a shared thread.
    assert normalized.decision.refusal is not None
    assert secret not in normalized.decision.refusal.full_text()


def test_group_sender_identity_comes_from_participant():
    env = {
        "key": {
            "remoteJid": "120363000000000000@g.us",
            "participant": "447700900000@s.whatsapp.net",
            "id": "M3",
        },
        "message": {"conversation": "hi"},
    }
    msg = wld.normalize_message(env)
    assert msg.session.gateway_user_id == "447700900000@s.whatsapp.net"


def test_empty_envelope_is_refused_empty_not_crashed():
    env = {"key": {"remoteJid": "447700900000@s.whatsapp.net", "id": "M4"}}
    normalized = wld.normalize_and_route(env)
    assert not normalized.message.has_content()
    assert normalized.decision.disposition is InboundDisposition.REFUSE_EMPTY


def test_missing_remote_jid_raises():
    with pytest.raises(ValueError):
        wld.normalize_message({"key": {"id": "M5"}, "message": {"conversation": "hi"}})


@pytest.mark.parametrize(
    "frame",
    [
        {"key": {"id": "M5"}, "message": {"conversation": "hi"}},
        {"key": {"remoteJid": "  "}, "message": {"conversation": "hi"}},
        {"key": {"remoteJid": None}, "message": {"conversation": "hi"}},
        {"message": {"conversation": "hi"}},
        {"key": "not-a-mapping"},
        {},
        "not-a-mapping",
    ],
)
def test_non_user_frame_is_refused_invalid_not_crashed(frame):
    """An internal/protocol Baileys frame with no routable remoteJid is dropped."""
    normalized = wld.normalize_and_route(frame)
    assert normalized.message is None
    assert normalized.decision.disposition is InboundDisposition.REFUSE_INVALID
    # A drop is transport plumbing, not a product refusal — no refusal copy.
    assert normalized.decision.refusal is None


def test_dry_run_on_invalid_frame_returns_metadata_without_crashing():
    result = wld.dry_run({"key": {"id": "M5"}, "message": {"conversation": "hi"}})
    assert result["disposition"] == "refuse_invalid"
    assert result["scope"] is None
    assert result["conversation_id"] is None
    assert result["has_content"] is False
    assert result["media_kinds"] == []


def test_ptt_audio_maps_to_voice_and_plain_audio_maps_to_audio():
    voice = {
        "key": {"remoteJid": "447700900000@s.whatsapp.net", "id": "V1"},
        "message": {"audioMessage": {"mimetype": "audio/ogg; codecs=opus", "ptt": True}},
    }
    audio = {
        "key": {"remoteJid": "447700900000@s.whatsapp.net", "id": "A1"},
        "message": {"audioMessage": {"mimetype": "audio/mp4", "ptt": False}},
    }
    assert wld.normalize_message(voice).media[0].kind == "voice"
    assert wld.normalize_message(audio).media[0].kind == "audio"


def test_image_document_map_to_photo_and_document_with_safe_uri():
    env = {
        "key": {"remoteJid": "447700900000@s.whatsapp.net", "id": "IMG1"},
        "message": {
            "imageMessage": {"mimetype": "image/jpeg", "caption": "ECG"},
        },
    }
    msg = wld.normalize_message(env)
    assert msg.media[0].kind == "photo"
    assert msg.media[0].caption == "ECG"
    assert msg.media[0].mime_type == "image/jpeg"
    # Safe, opaque pointer only — no media key, no bytes.
    assert msg.media[0].uri == "wa-linked-device://IMG1#0"
    assert wld.normalize_and_route(env).decision.disposition is (
        InboundDisposition.HANDLE
    )


def test_media_ref_never_carries_a_media_key():
    env = {
        "key": {"remoteJid": "447700900000@s.whatsapp.net", "id": "DOC1"},
        "message": {
            "documentMessage": {
                "mimetype": "application/pdf",
                "mediaKey": "SUPER-SECRET-KEY",
                "url": "https://mmg.whatsapp.net/secret",
                "fileName": "case.pdf",
            }
        },
    }
    ref = wld.normalize_message(env).media[0]
    assert ref.kind == "document"
    assert "SUPER-SECRET-KEY" not in (ref.uri or "")
    assert "mmg.whatsapp.net" not in (ref.uri or "")


def test_to_inbound_payload_matches_bridge_contract():
    payload = wld.to_inbound_payload(_text_envelope())
    assert payload["channel"] == "whatsapp"
    assert payload["scope"] == "direct"
    assert payload["conversation_id"] == "wa:447700900000@s.whatsapp.net"
    assert payload["text"] == "58M chest pain, CBD reflection"
    assert payload["private"] is True
    assert payload["media"] == []


def test_dry_run_excludes_clinical_text_and_captions():
    secret = "patient identifiable narrative that must not be logged"
    env = {
        "key": {"remoteJid": "447700900000@s.whatsapp.net", "id": "DR1"},
        "message": {
            "conversation": secret,
            "imageMessage": {"mimetype": "image/jpeg", "caption": "secret caption"},
        },
    }
    result = wld.dry_run(env)
    serialized = json.dumps(result)
    assert secret not in serialized
    assert "secret caption" not in serialized
    assert result["disposition"] == "handle"
    assert result["scope"] == "direct"
    assert result["has_content"] is True
    assert result["media_kinds"] == ["photo"]


def test_connector_family_constants_align_with_readiness_guard():
    assert wld.LINKED_DEVICE_CONNECTORS == frozenset(
        {"direct", "linked-device", "baileys"}
    )


def test_no_shared_emgurus_routing_wording_in_module():
    """The connector must never route testers through the general EMGurus account."""
    here = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(here, "..", "whatsapp_linked_device.py")
    with open(module_path, "r", encoding="utf-8") as handle:
        source = handle.read().lower()
    assert "emgurus gateway" not in source
    assert "emgurus fan-out" not in source
    assert "route through emgurus" not in source


def test_module_imports_without_telegram_or_product_engine():
    """A thin connector process must not pull in Telegram or the product brain."""
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, whatsapp_linked_device; "
            "bad = [m for m in ('telegram', 'extractor', 'bot') if m in sys.modules]; "
            "sys.exit(1 if bad else 0)",
        ],
        cwd=backend_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"linked-device connector pulled in a forbidden module:\n{result.stderr}"
    )


def test_cli_dry_run_prints_json_verdict_and_exit_code(tmp_path):
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    payload_file = tmp_path / "envelope.json"
    payload_file.write_text(json.dumps(_text_envelope()), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "whatsapp_linked_device.py", "--payload", str(payload_file)],
        cwd=backend_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["disposition"] == "handle"
    # The clinical narrative must not appear in the harness output.
    assert "chest pain" not in result.stdout


def test_cli_dry_run_group_turn_exits_refused(tmp_path):
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    payload_file = tmp_path / "group.json"
    payload_file.write_text(
        json.dumps(_text_envelope(jid="120363000000000000@g.us")), encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, "whatsapp_linked_device.py", "--payload", str(payload_file)],
        cwd=backend_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["disposition"] == "refuse_group"
