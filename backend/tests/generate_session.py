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


async def main():
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("telethon not installed. Run: pip install telethon")
        sys.exit(1)

    # You can set these as env vars or fetch from BWS
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")

    if not api_id or not api_hash:
        print("TELEGRAM_API_ID and TELEGRAM_API_HASH not found in env.")
        print("Set them manually or add BWS secret IDs here.")
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
