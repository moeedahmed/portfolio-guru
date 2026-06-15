"""Offline Telegram QA transcript runner.

Drives the real PTB ``Application.process_update()`` stack with ``OfflineRequest``
(any outbound network call fails immediately), feeds synthetic Telegram updates
for a set of golden cases, and writes a structured JSON + Markdown transcript
covering bot messages, inline buttons, captured draft state, and per-step
pass/fail observations.

This lane is the offline-only complement to the live Telethon harness in
``telegram_live_harness.py``. It never imports Telethon, never opens a network
connection, and never needs ``TELEGRAM_LIVE_APPROVED`` — running it does not
contact Telegram at all.

Handler registration mirrors the offline PTB fixture in
``tests/test_e2e_offline.py`` (keep them aligned).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Document, Message, PhotoSize, Update, Voice
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from tests.helpers import (
    BOT_USER,
    OfflineRequest,
    make_callback_update,
    make_command_update,
    make_text_update,
)


FORBIDDEN_MARKERS = (
    "traceback",
    "exception",
    "internal server error",
)


@dataclass
class Step:
    """A single synthetic action sent to the bot.

    Exactly one of ``text``, ``command``, ``callback``, or ``media_type`` must
    be set. Media steps use synthetic Telegram attachments and patched
    extractors; they exercise the bot's media handler path without contacting
    Telegram.
    """
    label: str
    text: str | None = None
    command: str | None = None
    callback: str | None = None
    media_type: str | None = None  # "photo", "voice", or "document"
    extracted_text: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    caption: str | None = None
    expect_text_any: tuple[str, ...] = ()
    expect_button_any: tuple[str, ...] = ()
    forbid_text_any: tuple[str, ...] = FORBIDDEN_MARKERS


@dataclass
class CaseDefinition:
    """A golden case: profile + ordered steps."""
    case_id: str
    persona: str
    profile: dict[str, Any]
    recommended_forms: list[tuple[str, str]]  # (form_type, rationale)
    draft_form_type: str
    draft_fields: dict[str, Any]
    steps: list[Step]


@dataclass
class StepObservation:
    label: str
    action: dict[str, Any]
    bot_messages: list[dict[str, Any]] = field(default_factory=list)
    buttons: list[dict[str, str]] = field(default_factory=list)
    observed_form_recommendations: list[str] = field(default_factory=list)
    observed_draft: dict[str, Any] | None = None
    observed_chosen_form: str | None = None
    timed_out: bool = False
    error: str | None = None
    passed: bool = True
    failures: list[str] = field(default_factory=list)


@dataclass
class CaseTranscript:
    case_id: str
    persona: str
    profile: dict[str, Any]
    steps: list[StepObservation] = field(default_factory=list)
    passed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Response collector
# ---------------------------------------------------------------------------


class _ResponseCollector:
    """Captures all bot send/edit calls between checkpoints."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def _make_fake_message(self, chat_id, text):
        msg = MagicMock(spec=Message)
        msg.message_id = len(self.sent) + 5000
        msg.chat_id = chat_id
        msg.text = text
        return msg

    async def send_message(self, chat_id=None, text="", **kwargs):
        self.sent.append({"method": "send_message", "chat_id": chat_id, "text": text, **kwargs})
        return self._make_fake_message(chat_id, text)

    async def edit_message_text(self, text="", chat_id=None, message_id=None, **kwargs):
        self.sent.append({
            "method": "edit_message_text",
            "chat_id": chat_id,
            "text": text,
            "message_id": message_id,
            **kwargs,
        })
        return True

    def drain(self) -> list[dict[str, Any]]:
        out = self.sent[:]
        self.sent.clear()
        return out


class _OfflineTelegramFile:
    """Small stand-in for Telegram ``File`` objects used by media handlers."""

    async def download_to_drive(self, custom_path=None, *args, **kwargs):
        if custom_path:
            Path(custom_path).write_bytes(b"offline telegram qa fixture")
        return custom_path


# ---------------------------------------------------------------------------
# Offline app builder
# ---------------------------------------------------------------------------


def _flatten_buttons(markup) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not markup or not hasattr(markup, "inline_keyboard"):
        return out
    for row in markup.inline_keyboard:
        for btn in row:
            out.append({
                "text": btn.text or "",
                "callback_data": btn.callback_data or "",
            })
    return out


def _prepare_update(update, bot) -> None:
    """Set the bot ref on nested update objects so PTB routing works."""
    update.set_bot(bot)
    if update.message:
        update.message.set_bot(bot)
        if update.message.chat:
            update.message.chat.set_bot(bot)
        if update.message.from_user:
            update.message.from_user.set_bot(bot)
    if update.callback_query:
        update.callback_query.set_bot(bot)
        if update.callback_query.message:
            update.callback_query.message.set_bot(bot)
            if update.callback_query.message.chat:
                update.callback_query.message.chat.set_bot(bot)
        if update.callback_query.from_user:
            update.callback_query.from_user.set_bot(bot)


@contextlib.asynccontextmanager
async def offline_application():
    """Build an offline PTB ``Application`` with handlers registered.

    Yields ``(app, collector, patch_stack)``. Network calls are blocked by
    ``OfflineRequest`` and class-level patches replace the network-bound bot
    methods with collector callbacks.
    """
    import bot  # noqa: WPS433 — late import: needs sys.path set up by conftest.

    collector = _ResponseCollector()

    app = (
        Application.builder()
        .token("0:FAKE")
        .updater(None)
        .request(OfflineRequest())
        .get_updates_request(OfflineRequest())
        .build()
    )

    real_bot = app.bot
    real_bot._unfreeze()
    real_bot._bot_user = BOT_USER
    real_bot._bot_initialized = True
    real_bot._requests_initialized = True

    bot_cls = type(real_bot)
    patches = contextlib.ExitStack()

    async def _send(_self, chat_id=None, text="", **kwargs):
        return await collector.send_message(chat_id=chat_id, text=text, **kwargs)

    async def _edit(_self, text="", chat_id=None, message_id=None, **kwargs):
        return await collector.edit_message_text(
            text=text, chat_id=chat_id, message_id=message_id, **kwargs,
        )

    async def _answer_cq(_self, callback_query_id=None, **kwargs):
        return True

    async def _delete(_self, chat_id=None, message_id=None, **kwargs):
        return True

    async def _get_me(_self, **kwargs):
        return BOT_USER

    async def _send_action(_self, chat_id=None, action=None, **kwargs):
        return True

    async def _get_file(_self, **kwargs):
        return _OfflineTelegramFile()

    patches.enter_context(patch.object(bot_cls, "send_message", _send))
    patches.enter_context(patch.object(bot_cls, "edit_message_text", _edit))
    patches.enter_context(patch.object(bot_cls, "answer_callback_query", _answer_cq))
    patches.enter_context(patch.object(bot_cls, "delete_message", _delete))
    patches.enter_context(patch.object(bot_cls, "get_me", _get_me))
    patches.enter_context(patch.object(bot_cls, "delete_webhook", AsyncMock(return_value=True)))
    patches.enter_context(patch.object(bot_cls, "send_chat_action", _send_action))
    patches.enter_context(patch.object(bot_cls, "edit_message_reply_markup", AsyncMock(return_value=True)))
    patches.enter_context(patch.object(PhotoSize, "get_file", _get_file))
    patches.enter_context(patch.object(Voice, "get_file", _get_file))
    patches.enter_context(patch.object(Document, "get_file", _get_file))

    case_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                bot.handle_callback,
                pattern=r"^ACTION\|(?:file|reset|cancel|add_detail|continue_thin)$",
            ),
            MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_case_input),
            MessageHandler(filters.VOICE, bot.handle_case_input),
            MessageHandler(filters.PHOTO, bot.handle_case_input),
            MessageHandler(filters.Document.ALL, bot.handle_case_input),
        ],
        states={
            bot.AWAIT_CASE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_case_input),
                MessageHandler(filters.VOICE, bot.handle_case_input),
                MessageHandler(filters.PHOTO, bot.handle_case_input),
                MessageHandler(filters.Document.ALL, bot.handle_case_input),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^ACTION\|add_detail$"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^ACTION\|continue_thin$"),
            ],
            bot.AWAIT_DOC_INTENT: [
                CallbackQueryHandler(bot.handle_document_intent, pattern=r"^DOCUSE\|"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^CANCEL\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_mid_conversation_text),
            ],
            bot.AWAIT_FORM_CHOICE: [
                CallbackQueryHandler(bot.handle_form_choice, pattern=r"^FORM\|"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^CANCEL\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_mid_conversation_text),
            ],
            bot.AWAIT_TEMPLATE_REVIEW: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_template_review_text),
                MessageHandler(filters.VOICE, bot.handle_case_input),
                MessageHandler(filters.PHOTO, bot.handle_case_input),
                MessageHandler(filters.Document.ALL, bot.handle_case_input),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^CASE\|"),
                CallbackQueryHandler(bot.handle_form_choice, pattern=r"^FORM\|"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^ACTION\|add_detail$"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^ACTION\|continue_thin$"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^CANCEL\|"),
            ],
            bot.AWAIT_APPROVAL: [
                CallbackQueryHandler(bot.handle_approval_approve, pattern=r"^APPROVE\|"),
                CallbackQueryHandler(bot.handle_approval_edit, pattern=r"^EDIT\|"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^CANCEL\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_mid_conversation_text),
            ],
            bot.AWAIT_EDIT_FIELD: [
                CallbackQueryHandler(bot.handle_edit_field, pattern=r"^FIELD\|"),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^CANCEL\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_mid_conversation_text),
            ],
            bot.AWAIT_EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_edit_value_with_intent),
                MessageHandler(~filters.COMMAND & ~filters.TEXT, bot.handle_edit_value),
                CallbackQueryHandler(bot.handle_callback, pattern=r"^CANCEL\|"),
            ],
        },
        fallbacks=[
            CommandHandler("start", bot.start),
            CommandHandler("help", bot.help_command),
            CommandHandler("settings", bot.settings_command),
            CommandHandler("cancel", bot.setup_cancel),
            CallbackQueryHandler(
                bot.handle_callback,
                pattern=r"^(?:INFO\|.*|CANCEL\|.*|ACTION\|(?:file|setup|reset|cancel|add_detail|continue_thin|retry_filing))$",
            ),
        ],
        per_message=False,
        allow_reentry=False,
    )

    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CommandHandler("settings", bot.settings_command))
    app.add_handler(CommandHandler("cancel", bot.cancel_command))
    app.add_handler(CommandHandler("help", bot.help_command))
    app.add_handler(CallbackQueryHandler(bot.handle_set_curriculum, pattern=r"^SET_CURRICULUM\|"))
    app.add_handler(CallbackQueryHandler(bot.handle_info_button, pattern=r"^INFO\|"))
    app.add_handler(
        CallbackQueryHandler(
            bot.handle_action_button,
            pattern=r"^ACTION\|(?!file$|reset$|cancel$|add_detail$|continue_thin$|retry_filing$).+",
        )
    )
    app.add_handler(CallbackQueryHandler(bot.handle_set_level, pattern=r"^SETLEVEL\|"))
    app.add_handler(case_conv)

    await app.initialize()
    try:
        yield app, collector, patches
    finally:
        await app.shutdown()
        patches.close()


# ---------------------------------------------------------------------------
# Case runner
# ---------------------------------------------------------------------------


def _patch_profile(monkeypatch_obj, profile: dict[str, Any]) -> None:
    monkeypatch_obj.setattr("bot.has_credentials", lambda uid: profile.get("has_credentials", True))
    monkeypatch_obj.setattr("bot.get_training_level", lambda uid: profile.get("training_level"))
    monkeypatch_obj.setattr("bot.get_curriculum", lambda uid: profile.get("curriculum"))
    monkeypatch_obj.setattr("bot.get_voice_profile", lambda uid: profile.get("voice_profile"))


def _patch_extraction(monkeypatch_obj, case: CaseDefinition) -> None:
    from models import FormDraft, FormTypeRecommendation
    from kaizen_form_filer import FORM_UUIDS

    recs = [
        FormTypeRecommendation(
            form_type=form_type,
            rationale=rationale,
            uuid=FORM_UUIDS.get(form_type),
        )
        for form_type, rationale in case.recommended_forms
    ]
    draft = FormDraft(
        form_type=case.draft_form_type,
        uuid=FORM_UUIDS.get(case.draft_form_type),
        fields=case.draft_fields,
    )

    async def fake_recommend(*args, **kwargs):
        return recs

    async def fake_extract(*args, **kwargs):
        return draft

    media_texts: dict[str, list[str]] = {
        "photo": [
            step.extracted_text
            for step in case.steps
            if step.media_type == "photo" and step.extracted_text
        ],
        "voice": [
            step.extracted_text
            for step in case.steps
            if step.media_type == "voice" and step.extracted_text
        ],
        "document": [
            step.extracted_text
            for step in case.steps
            if step.media_type == "document" and step.extracted_text
        ],
    }

    def _pop_media_text(source: str) -> str:
        queue = media_texts.get(source) or []
        if queue:
            return queue.pop(0)
        return case.steps[0].text or case.steps[0].extracted_text or ""

    async def fake_image_extract(*args, **kwargs):
        return _pop_media_text("photo")

    async def fake_voice_transcribe(*args, **kwargs):
        return _pop_media_text("voice")

    async def fake_document_extract(*args, **kwargs):
        return _pop_media_text("document")

    monkeypatch_obj.setattr("bot.recommend_form_types", fake_recommend)
    monkeypatch_obj.setattr("bot.classify_intent", AsyncMock(return_value="case"))
    monkeypatch_obj.setattr(
        "bot.extract_explicit_form_type", lambda text, *, require_intent=True: None
    )
    monkeypatch_obj.setattr("bot.extract_form_data", fake_extract)
    monkeypatch_obj.setattr("bot.extract_cbd_data", fake_extract)
    monkeypatch_obj.setattr("bot.extract_from_image", fake_image_extract)
    monkeypatch_obj.setattr("bot.transcribe_voice", fake_voice_transcribe)
    monkeypatch_obj.setattr("bot.extract_from_document", fake_document_extract)
    monkeypatch_obj.setattr("bot.is_supported_document", lambda file_name: True)


def _build_update_for_step(step: Step):
    if step.text is not None:
        return {"text": step.text}, make_text_update(step.text)
    if step.command is not None:
        return {"command": step.command}, make_command_update(step.command)
    if step.callback is not None:
        return {"callback": step.callback}, make_callback_update(step.callback)
    if step.media_type is not None:
        return _build_media_update_for_step(step)
    raise ValueError(f"Step {step.label!r} must define text, command, or callback")


def _build_media_update_for_step(step: Step):
    from tests.helpers import TEST_CHAT, TEST_USER, _next_msg_id, _next_update_id

    media_type = step.media_type
    file_name = step.file_name or {
        "photo": "handwritten-note.jpg",
        "voice": "voice-note.ogg",
        "document": "certificate.pdf",
    }.get(media_type, "attachment.bin")
    mime_type = step.mime_type or {
        "photo": "image/jpeg",
        "voice": "audio/ogg",
        "document": "application/pdf",
    }.get(media_type)

    kwargs: dict[str, Any] = {}
    if media_type == "photo":
        kwargs["photo"] = (
            PhotoSize(
                file_id=f"offline-photo-{_next_msg_id()}",
                file_unique_id=f"offline-photo-unique-{_next_msg_id()}",
                width=1200,
                height=900,
                file_size=4096,
            ),
        )
        if step.caption:
            kwargs["caption"] = step.caption
    elif media_type == "voice":
        kwargs["voice"] = Voice(
            file_id=f"offline-voice-{_next_msg_id()}",
            file_unique_id=f"offline-voice-unique-{_next_msg_id()}",
            duration=42,
            mime_type=mime_type,
            file_size=4096,
        )
    elif media_type == "document":
        kwargs["document"] = Document(
            file_id=f"offline-doc-{_next_msg_id()}",
            file_unique_id=f"offline-doc-unique-{_next_msg_id()}",
            file_name=file_name,
            mime_type=mime_type,
            file_size=8192,
        )
        if step.caption:
            kwargs["caption"] = step.caption
    else:
        raise ValueError(f"Unsupported media_type {media_type!r} for {step.label!r}")

    message = Message(
        message_id=_next_msg_id(),
        date=dt.datetime.now(tz=dt.timezone.utc),
        chat=TEST_CHAT,
        from_user=TEST_USER,
        **kwargs,
    )
    action = {
        "media_type": media_type,
        "file_name": file_name,
        "mime_type": mime_type,
    }
    if step.caption:
        action["caption"] = step.caption
    if step.extracted_text:
        action["extracted_text"] = step.extracted_text
    return action, Update(update_id=_next_update_id(), message=message)


def _observe_user_data(app, user_id: int = 99999) -> tuple[str | None, dict[str, Any] | None, list[str]]:
    """Inspect PTB user_data after a step to surface draft / recommendations."""
    try:
        user_data = app.user_data.get(user_id) or {}
    except Exception:
        return None, None, []
    chosen_form = user_data.get("chosen_form")
    raw_draft = user_data.get("draft_data") or user_data.get("pending_draft_data")
    draft: dict[str, Any] | None = None
    if raw_draft:
        if isinstance(raw_draft, dict):
            draft = {
                "form_type": raw_draft.get("form_type"),
                "fields": raw_draft.get("fields") or {},
            }
        else:
            draft = {
                "form_type": getattr(raw_draft, "form_type", None),
                "fields": dict(getattr(raw_draft, "fields", {}) or {}),
            }
    recs = []
    raw_recs = user_data.get("form_recommendations") or []
    for rec in raw_recs:
        form_type = getattr(rec, "form_type", None) or (rec.get("form_type") if isinstance(rec, dict) else None)
        if form_type:
            recs.append(form_type)
    return chosen_form, draft, recs


async def _run_step(app, collector, step: Step, user_id: int = 99999) -> StepObservation:
    action_meta, update = _build_update_for_step(step)
    _prepare_update(update, app.bot)
    collector.drain()
    obs = StepObservation(label=step.label, action=action_meta)
    try:
        await asyncio.wait_for(app.process_update(update), timeout=30)
    except asyncio.TimeoutError:
        obs.timed_out = True
        obs.passed = False
        obs.failures.append("step timed out after 30s")
        return obs
    except Exception as exc:  # noqa: BLE001 — capture surfaced exception verbatim
        obs.error = f"{type(exc).__name__}: {exc}"
        obs.passed = False
        obs.failures.append(obs.error)
        return obs

    drained = collector.drain()
    for record in drained:
        text = record.get("text") or ""
        markup = record.get("reply_markup")
        msg_buttons = _flatten_buttons(markup)
        obs.bot_messages.append({
            "method": record.get("method"),
            "text": text,
            "buttons": msg_buttons,
        })
        obs.buttons.extend(msg_buttons)

    chosen_form, draft, recs = _observe_user_data(app, user_id=user_id)
    obs.observed_chosen_form = chosen_form
    obs.observed_draft = draft
    obs.observed_form_recommendations = recs

    combined_text = " ".join(m["text"] for m in obs.bot_messages).lower()
    if not obs.bot_messages:
        obs.failures.append("no bot messages produced")
    if step.expect_text_any and not any(t.lower() in combined_text for t in step.expect_text_any):
        obs.failures.append(f"expected one of {step.expect_text_any!r}")
    if step.media_type is not None:
        expected_source = "document" if step.media_type == "document" else step.media_type
        case_source = (app.user_data.get(user_id) or {}).get("case_input_source")
        allowed_sources = {expected_source, "mixed"}
        if case_source and case_source not in allowed_sources:
            obs.failures.append(
                f"expected case_input_source {expected_source!r}, observed {case_source!r}"
            )
    if step.expect_button_any:
        labels = " ".join(b["callback_data"] + " " + b["text"] for b in obs.buttons).lower()
        if not any(t.lower() in labels for t in step.expect_button_any):
            obs.failures.append(f"expected button containing one of {step.expect_button_any!r}")
    for marker in step.forbid_text_any:
        if marker.lower() in combined_text:
            obs.failures.append(f"forbidden marker present: {marker!r}")
    obs.passed = not obs.failures
    return obs


async def run_case(case: CaseDefinition, monkeypatch_obj) -> CaseTranscript:
    """Run a single case end-to-end against the offline app and return a transcript."""
    transcript = CaseTranscript(
        case_id=case.case_id,
        persona=case.persona,
        profile=dict(case.profile),
    )
    _patch_profile(monkeypatch_obj, case.profile)
    _patch_extraction(monkeypatch_obj, case)

    async with offline_application() as (app, collector, _patches):
        for step in case.steps:
            obs = await _run_step(app, collector, step)
            transcript.steps.append(obs)
            if not obs.passed:
                transcript.passed = False
    return transcript


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def write_reports(transcripts: list[CaseTranscript], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "transcript.json"
    md_path = out_dir / "transcript.md"

    payload = {
        "generated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "summary": {
            "cases": len(transcripts),
            "passed": sum(1 for t in transcripts if t.passed),
            "failed": sum(1 for t in transcripts if not t.passed),
        },
        "cases": [t.to_dict() for t in transcripts],
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    lines: list[str] = []
    lines.append("# Telegram QA offline transcript\n")
    lines.append(f"Generated: {payload['generated_at']}\n")
    lines.append(
        f"Cases: {payload['summary']['cases']} — "
        f"passed {payload['summary']['passed']}, failed {payload['summary']['failed']}\n",
    )
    for case in transcripts:
        verdict = "PASS" if case.passed else "FAIL"
        lines.append(f"\n## {case.case_id} — {case.persona} — {verdict}\n")
        lines.append(f"- Profile: {case.profile}\n")
        for step in case.steps:
            action_key = "media_type" if "media_type" in step.action else list(step.action)[0]
            head = f"step **{step.label}** ({action_key}={step.action[action_key]!r})"
            if step.timed_out:
                head += " — TIMEOUT"
            elif step.error:
                head += f" — ERROR {step.error}"
            elif step.passed:
                head += " — ok"
            else:
                head += " — FAIL"
            lines.append(f"\n### {head}\n")
            if step.bot_messages:
                lines.append("Bot replies:\n")
                for idx, msg in enumerate(step.bot_messages, start=1):
                    text = (msg["text"] or "").strip().splitlines()
                    first = text[0] if text else ""
                    suffix = "" if len(text) <= 1 else f" (+{len(text)-1} more lines)"
                    lines.append(f"  {idx}. `{msg['method']}`: {first}{suffix}\n")
            if step.buttons:
                btn_summary = ", ".join(
                    f"[{b['text']}→{b['callback_data']}]" for b in step.buttons[:12]
                )
                more = "" if len(step.buttons) <= 12 else f" (+{len(step.buttons)-12} more)"
                lines.append(f"Buttons: {btn_summary}{more}\n")
            if step.observed_form_recommendations:
                lines.append(
                    f"Observed recommendations: {step.observed_form_recommendations}\n",
                )
            if step.observed_chosen_form:
                lines.append(f"Chosen form: {step.observed_chosen_form}\n")
            if step.observed_draft:
                lines.append(
                    f"Draft ({step.observed_draft.get('form_type')}): "
                    f"{sorted((step.observed_draft.get('fields') or {}).keys())}\n",
                )
            if step.failures:
                lines.append(f"Failures: {step.failures}\n")
    md_path.write_text("".join(lines), encoding="utf-8")
    return json_path, md_path


def default_artifact_dir(root: Path) -> Path:
    stamp = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return root / ".artifacts" / "telegram-qa-transcript" / stamp
