#!/usr/bin/env python3
"""Generate a Telethon session string for live testing.

Reads TELEGRAM_API_ID and TELEGRAM_API_HASH from BWS, then prompts
interactively for phone number and code. Prints the session string
to copy into BWS.

Usage:
    python3 tests/generate_session.py
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path


SECRETS_MAP = Path.home() / ".openclaw" / "workspace" / "secrets.json"


def _bws_get(secret_id: str) -> str:
    """Fetch a secret value from Bitwarden Secrets Manager."""
    bws_token = open(os.path.expanduser("~/.openclaw/.bws-token")).read().strip()
    result = subprocess.run(
        [os.path.expanduser("~/.cargo/bin/bws"), "secret", "get", secret_id, "--output", "json"],
        capture_output=True,
        text=True,
        env={**os.environ, "BWS_ACCESS_TOKEN": bws_token},
    )
    return json.loads(result.stdout)["value"]


def _mapped_bws_secret(name: str) -> str:
    """Fetch a named secret from the OpenClaw BWS map."""
    data = json.loads(SECRETS_MAP.read_text())
    entry = data["credentials"][name]
    secret_id = entry.get("bws_secret_id") or entry.get("bwsId")
    if not secret_id:
        raise RuntimeError(f"{name} has no BWS secret id in {SECRETS_MAP}")
    return _bws_get(secret_id)


def _clean_env_value(value: str | None) -> str:
    return (value or "").strip().strip('"').strip("'")


async def main():
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("telethon not installed. Run: pip install telethon")
        sys.exit(1)

    api_id = _clean_env_value(os.environ.get("TELEGRAM_API_ID")) or _clean_env_value(
        _mapped_bws_secret("TELEGRAM_API_ID")
    )
    api_hash = _clean_env_value(os.environ.get("TELEGRAM_API_HASH")) or _clean_env_value(
        _mapped_bws_secret("TELEGRAM_API_HASH")
    )

    if not api_id or not api_hash:
        print("TELEGRAM_API_ID and TELEGRAM_API_HASH not found in env.")
        print("Set them manually or check OpenClaw secrets.json.")
        sys.exit(1)

    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.start()

    session_string = client.session.save()
    print("\n=== Your Telethon session string ===")
    print(session_string)
    print("\nStore this in BWS and set as TELETHON_SESSION env var for live tests.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
