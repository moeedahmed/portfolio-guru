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


@dataclass(frozen=True)
class TelegramStep:
    name: str
    message: str
    expect_text_any: tuple[str, ...] = ()
    expect_button_any: tuple[str, ...] = ()
    timeout_seconds: int = 90


@dataclass
class TelegramExchange:
    step: str
    sent: str
    received: str
    buttons: list[str] = field(default_factory=list)


def telethon_env() -> dict[str, str]:
    """Return Telethon credentials, accepting both old and new env names."""
    return {
        "session": os.environ.get("TELETHON_SESSION", ""),
        "api_id": os.environ.get("TELETHON_API_ID") or os.environ.get("TELEGRAM_API_ID", ""),
        "api_hash": os.environ.get("TELETHON_API_HASH") or os.environ.get("TELEGRAM_API_HASH", ""),
        "bot_username": os.environ.get("TELEGRAM_BOT_USERNAME", "portfolio_guru_bot"),
    }


def has_telethon_env() -> bool:
    env = telethon_env()
    return bool(env["session"] and env["api_id"] and env["api_hash"])


def button_texts(message) -> list[str]:
    return [button.text for row in (getattr(message, "buttons", None) or []) for button in row]


async def run_telegram_workflow(client, bot_username: str, steps: Iterable[TelegramStep]) -> list[TelegramExchange]:
    """Run a scenario against the live bot and return a transcript."""
    transcript: list[TelegramExchange] = []
    async with client.conversation(bot_username, timeout=max(step.timeout_seconds for step in steps)) as conv:
        for step in steps:
            await conv.send_message(step.message)
            reply = await conv.get_response(timeout=step.timeout_seconds)
            exchange = TelegramExchange(
                step=step.name,
                sent=step.message,
                received=reply.raw_text or "",
                buttons=button_texts(reply),
            )
            assert exchange.received.strip(), f"{step.name}: bot returned empty text"
            if step.expect_text_any:
                assert any(token.lower() in exchange.received.lower() for token in step.expect_text_any), (
                    f"{step.name}: expected one of {step.expect_text_any!r}; got {exchange.received!r}"
                )
            if step.expect_button_any:
                assert any(
                    token.lower() in button.lower()
                    for token in step.expect_button_any
                    for button in exchange.buttons
                ), f"{step.name}: expected one of {step.expect_button_any!r}; got buttons {exchange.buttons!r}"
            transcript.append(exchange)
    write_transcript_artifact(transcript)
    return transcript


def assert_transcript_is_sensible(transcript: list[TelegramExchange]) -> None:
    """Low-cost semantic guard for the live workflow."""
    assert transcript, "No Telegram transcript captured"
    combined = "\n".join(exchange.received for exchange in transcript).lower()
    bad_markers = ("traceback", "exception", "internal server error", "none", "undefined")
    assert not any(marker in combined for marker in bad_markers), combined


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
