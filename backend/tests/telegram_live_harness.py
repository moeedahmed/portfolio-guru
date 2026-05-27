"""Shared Telethon harness for live Portfolio Guru workflow checks.

The harness is intentionally small: Telethon drives the bot as a real Telegram
user, captures a transcript, and applies scenario-level assertions. Heavier
visual proof belongs in OpenClaw QA Lab, not in this product repo.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

LIVE_APPROVAL_VALUE = "portfolio-guru-live-qa-approved"
DEFAULT_ALLOWED_BOT_USERNAMES = ("portfolio_guru_bot",)
FORBIDDEN_RESPONSE_MARKERS = (
    "traceback",
    "exception",
    "internal server error",
    "undefined",
    "null",
)


@dataclass(frozen=True)
class TelegramStep:
    name: str
    message: str
    expect_text_any: tuple[str, ...] = ()
    expect_button_any: tuple[str, ...] = ()
    click_button_any: tuple[str, ...] = ()
    expect_after_click_text_any: tuple[str, ...] = ()
    expect_after_click_button_any: tuple[str, ...] = ()
    forbid_text_any: tuple[str, ...] = FORBIDDEN_RESPONSE_MARKERS
    forbid_button_any: tuple[str, ...] = ()
    timeout_seconds: int = 90
    followup_limit: int = 4


@dataclass
class TelegramExchange:
    step: str
    action: str
    received: str
    buttons: list[str] = field(default_factory=list)
    clicked_button: str = ""


def telethon_env() -> dict[str, str]:
    """Return Telethon credentials, accepting both old and new env names."""
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "portfolio_guru_bot").lstrip("@")
    return {
        "session": os.environ.get("TELETHON_SESSION", ""),
        "api_id": os.environ.get("TELETHON_API_ID") or os.environ.get("TELEGRAM_API_ID", ""),
        "api_hash": os.environ.get("TELETHON_API_HASH") or os.environ.get("TELEGRAM_API_HASH", ""),
        "bot_username": bot_username,
        "approval": os.environ.get("TELEGRAM_LIVE_APPROVED", ""),
    }


def allowed_bot_usernames() -> set[str]:
    configured = os.environ.get("TELEGRAM_LIVE_ALLOWED_BOTS", "")
    names = configured.split(",") if configured else DEFAULT_ALLOWED_BOT_USERNAMES
    return {name.strip().lstrip("@") for name in names if name.strip()}


def normalise_bot_username(bot_username: str) -> str:
    return bot_username.strip().lstrip("@")


def assert_live_telegram_guardrails(bot_username: str | None = None) -> None:
    env = telethon_env()
    if env["approval"] != LIVE_APPROVAL_VALUE:
        raise RuntimeError(
            "Live Telegram QA is blocked until Moeed explicitly approves this run. "
            f"Set TELEGRAM_LIVE_APPROVED={LIVE_APPROVAL_VALUE} only after approval."
        )
    allowed = allowed_bot_usernames()
    if env["bot_username"] not in allowed:
        raise RuntimeError(
            f"Live Telegram QA target @{env['bot_username']} is not allowlisted. "
            f"Allowed targets: {', '.join(sorted(allowed))}"
        )
    if bot_username is not None:
        target = normalise_bot_username(bot_username)
        if target != env["bot_username"]:
            raise RuntimeError(f"Refusing to send live Telegram messages to @{target}")
        if target not in allowed:
            raise RuntimeError(
                f"Live Telegram QA target @{target} is not allowlisted. "
                f"Allowed targets: {', '.join(sorted(allowed))}"
            )


def has_telethon_env() -> bool:
    env = telethon_env()
    if not (env["session"] and env["api_id"] and env["api_hash"]):
        return False
    try:
        assert_live_telegram_guardrails()
    except RuntimeError:
        return False
    return True


def button_texts(message) -> list[str]:
    return [button.text for row in (getattr(message, "buttons", None) or []) for button in row]


def _contains_any(value: str, tokens: tuple[str, ...]) -> bool:
    return any(token.lower() in value.lower() for token in tokens)


def _button_matches(button_text: str, tokens: tuple[str, ...]) -> bool:
    return any(token.lower() in button_text.lower() for token in tokens)


def _find_button(buttons, tokens: tuple[str, ...]):
    for row in buttons or []:
        for button in row:
            if _button_matches(getattr(button, "text", ""), tokens):
                return button
    return None


def _matches_expectation(message, step: TelegramStep) -> bool:
    received = getattr(message, "raw_text", "") or ""
    buttons = button_texts(message)
    text_ok = not step.expect_text_any or _contains_any(received, step.expect_text_any)
    buttons_ok = not step.expect_button_any or any(
        _button_matches(button, step.expect_button_any) for button in buttons
    )
    forbidden_text_ok = not step.forbid_text_any or not _contains_any(received, step.forbid_text_any)
    forbidden_buttons_ok = not step.forbid_button_any or not any(
        _button_matches(button, step.forbid_button_any) for button in buttons
    )
    return bool(received.strip()) and text_ok and buttons_ok and forbidden_text_ok and forbidden_buttons_ok


def _assert_message_matches(message, step: TelegramStep, phase: str) -> None:
    received = getattr(message, "raw_text", "") or ""
    buttons = button_texts(message)
    assert received.strip(), f"{step.name} {phase}: bot returned empty text"
    if step.expect_text_any:
        assert _contains_any(received, step.expect_text_any), (
            f"{step.name} {phase}: expected one of {step.expect_text_any!r}; got {received!r}"
        )
    if step.expect_button_any:
        assert any(_button_matches(button, step.expect_button_any) for button in buttons), (
            f"{step.name} {phase}: expected one of {step.expect_button_any!r}; got buttons {buttons!r}"
        )
    if step.forbid_text_any:
        assert not _contains_any(received, step.forbid_text_any), (
            f"{step.name} {phase}: response leaked forbidden marker; got {received!r}"
        )
    if step.forbid_button_any:
        assert not any(_button_matches(button, step.forbid_button_any) for button in buttons), (
            f"{step.name} {phase}: forbidden button present; got buttons {buttons!r}"
        )


def _click_expectation_step(step: TelegramStep) -> TelegramStep:
    return TelegramStep(
        name=step.name,
        message=step.message,
        expect_text_any=step.expect_after_click_text_any,
        expect_button_any=step.expect_after_click_button_any,
        forbid_text_any=step.forbid_text_any,
        forbid_button_any=step.forbid_button_any,
        timeout_seconds=step.timeout_seconds,
        followup_limit=step.followup_limit,
    )


async def wait_for_matching_message(
    client,
    chat_id: str | int,
    timeout_seconds: int,
    expect_text_any: tuple[str, ...] = (),
    expect_buttons: bool = False,
    expect_button_any: tuple[str, ...] = (),
    forbid_text_any: tuple[str, ...] = FORBIDDEN_RESPONSE_MARKERS,
    min_id: int | None = None,
) -> any:
    """Poll recent chat history until a message matches text/buttons expectations."""
    import asyncio
    import time
    start_time = time.time()

    while time.time() - start_time < timeout_seconds:
        try:
            messages = await client.get_messages(chat_id, limit=5)
            for msg in messages:
                if msg.out:
                    continue
                if min_id is not None and getattr(msg, "id", 0) < min_id:
                    continue
                received = getattr(msg, "raw_text", "") or ""
                buttons = button_texts(msg)

                text_ok = not expect_text_any or _contains_any(received, expect_text_any)
                buttons_ok = not expect_buttons or bool(buttons or msg.reply_markup)
                button_text_ok = not expect_button_any or any(
                    _button_matches(button, expect_button_any) for button in buttons
                )
                forbidden_text_ok = not forbid_text_any or not _contains_any(received, forbid_text_any)

                if received.strip() and text_ok and buttons_ok and button_text_ok and forbidden_text_ok:
                    return msg
        except Exception:
            pass
        await asyncio.sleep(0.5)

    raise TimeoutError(
        f"Timed out waiting for message in chat {chat_id} "
        f"matching text {expect_text_any} and buttons {expect_button_any} "
        f"(buttons expected: {expect_buttons})"
    )


async def _wait_for_matching_response(conv, step: TelegramStep):
    try:
        return await wait_for_matching_message(
            conv.client,
            conv.input_chat,
            step.timeout_seconds,
            expect_text_any=step.expect_text_any,
            expect_buttons=bool(step.expect_button_any),
            expect_button_any=step.expect_button_any,
            forbid_text_any=step.forbid_text_any,
        )
    except TimeoutError:
        return await conv.get_response(timeout=1)


async def run_telegram_workflow(client, bot_username: str, steps: Iterable[TelegramStep]) -> list[TelegramExchange]:
    """Run a scenario against the live bot and return a transcript."""
    bot_username = normalise_bot_username(bot_username)
    assert_live_telegram_guardrails(bot_username)
    transcript: list[TelegramExchange] = []
    async with client.conversation(bot_username, timeout=max(step.timeout_seconds for step in steps)) as conv:
        for step in steps:
            sent = await conv.send_message(step.message)
            reply = await wait_for_matching_message(
                client,
                bot_username,
                step.timeout_seconds,
                expect_text_any=step.expect_text_any,
                expect_buttons=bool(step.expect_button_any),
                expect_button_any=step.expect_button_any,
                forbid_text_any=step.forbid_text_any,
                min_id=getattr(sent, "id", None),
            )
            exchange = TelegramExchange(
                step=step.name,
                action=f"send:{step.message}",
                received=reply.raw_text or "",
                buttons=button_texts(reply),
            )
            _assert_message_matches(reply, step, "after send")
            transcript.append(exchange)
            if step.click_button_any:
                button = _find_button(getattr(reply, "buttons", None), step.click_button_any)
                assert button is not None, (
                    f"{step.name}: expected clickable button matching {step.click_button_any!r}; "
                    f"got buttons {exchange.buttons!r}"
                )
                clicked_text = button.text
                await button.click()
                click_step = _click_expectation_step(step)
                followup = await wait_for_matching_message(
                    client,
                    bot_username,
                    click_step.timeout_seconds,
                    expect_text_any=click_step.expect_text_any,
                    expect_buttons=bool(click_step.expect_button_any),
                    expect_button_any=click_step.expect_button_any,
                    forbid_text_any=click_step.forbid_text_any,
                    min_id=getattr(reply, "id", None),
                )
                _assert_message_matches(followup, click_step, f"after clicking {clicked_text!r}")
                transcript.append(
                    TelegramExchange(
                        step=step.name,
                        action="click_button",
                        received=followup.raw_text or "",
                        buttons=button_texts(followup),
                        clicked_button=clicked_text,
                    )
                )
    write_transcript_artifact(transcript)
    return transcript


def assert_transcript_is_sensible(transcript: list[TelegramExchange]) -> None:
    """Low-cost semantic guard for the live workflow."""
    assert transcript, "No Telegram transcript captured"
    combined = "\n".join(exchange.received for exchange in transcript).lower()
    assert not any(marker in combined for marker in FORBIDDEN_RESPONSE_MARKERS), combined
    clicked = [exchange for exchange in transcript if exchange.action == "click_button"]
    if clicked:
        assert all(exchange.clicked_button for exchange in clicked), "Button click transcript missing button label"


def write_transcript_artifact(transcript: list[TelegramExchange]) -> None:
    artifact_dir = os.environ.get("TELEGRAM_E2E_ARTIFACT_DIR")
    if not artifact_dir:
        return
    path = Path(artifact_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / "portfolio-guru-telegram-transcript.json").write_text(
        json.dumps([asdict(exchange) for exchange in transcript], indent=2),
        encoding="utf-8",
    )
