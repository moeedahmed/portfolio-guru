#!/usr/bin/env python3
"""Operator tool for proving passwordless Kaizen filing on one account.

Three steps, run in order by the operator for their own Telegram user id:

  link             Create a one-time phone link through the loopback broker.
                   The clinician signs into Kaizen themselves; their session,
                   never their password, is kept in the beta's session cache.
  status           Check whether that kept session still opens Kaizen, and
                   append the result to a JSON-lines log. Run it periodically
                   to measure how long a Kaizen session actually lasts.
  save-test-draft  Save one clearly labelled synthetic CBD *draft* through the
                   beta's own filer with no username and no password, using
                   only the kept session. Never submits to a supervisor.

The Connect Kaizen page must be running; the bot starts it when
PG_ENABLE_PASSWORDLESS_CONNECT is on (backend/run_local.sh).
Nothing here reads, prompts for, or stores a Kaizen password.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

DEFAULT_LOG = (
    Path.home() / ".openclaw/data/portfolio-guru/mobile-handoff/session-lifetime.jsonl"
)
TEST_DRAFT_MARKER = "PORTFOLIO GURU PASSWORDLESS TEST DRAFT - SAFE TO DELETE"


def cmd_link(user_id: int) -> int:
    from mobile_kaizen_handoff import ConnectLinkUnavailable, create_connect_link

    try:
        link = create_connect_link(user_id)
    except ConnectLinkUnavailable as exc:
        print(f"No link: {exc}.", file=sys.stderr)
        return 1
    print(f"Open within {link.expires_in_seconds // 60} minutes (single use):")
    print(link.url)
    return 0


async def _session_opens_kaizen(user_id: int) -> bool:
    from playwright.async_api import async_playwright

    import kaizen_form_filer

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context()).new_page()
            return await kaizen_form_filer.use_cached_session(page, user_id)
        finally:
            await browser.close()


def cmd_status(user_id: int, log_path: Path) -> int:
    import kaizen_form_filer

    cache = kaizen_form_filer._session_cache_path(user_id)
    if not cache.exists():
        print("No kept session for this user. Run `link` first.")
        return 1
    age_hours = (time.time() - cache.stat().st_mtime) / 3600
    alive = asyncio.run(_session_opens_kaizen(user_id))
    entry = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_id": user_id,
        "session_alive": alive,
        "hours_since_session_saved": round(age_hours, 2),
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    state = "still signed in" if alive else "EXPIRED - Kaizen asked to sign in again"
    print(f"Session {state} ({age_hours:.1f} h after it was saved). Logged to {log_path}")
    return 0 if alive else 2


def _synthetic_cbd_fields() -> dict[str, object]:
    # Clearly synthetic, contains no patient information, and names itself as
    # a deletable test so it can never be mistaken for real evidence.
    note = (
        f"{TEST_DRAFT_MARKER}. Synthetic entry created to prove that Portfolio "
        "Guru can save a Kaizen draft without storing the doctor's password."
    )
    return {
        "date_of_encounter": date.today().isoformat(),
        "clinical_setting": "Emergency Department",
        "patient_presentation": note,
        "trainee_role": note,
        "clinical_reasoning": note,
        "reflection": note,
        "curriculum_links": [],
        "key_capabilities": [],
    }


async def _save_test_draft(user_id: int) -> dict:
    import kaizen_form_filer

    # Use an isolated headless browser, never the shared managed Chrome, so the
    # proof cannot pick up whoever that profile happens to be signed in as.
    kaizen_form_filer.KAIZEN_USE_CDP = False
    return await kaizen_form_filer.file_to_kaizen(
        form_type="CBD",
        fields=_synthetic_cbd_fields(),
        username="",
        password="",
        submit=False,
        telegram_user_id=user_id,
    )


def cmd_save_test_draft(user_id: int) -> int:
    import kaizen_form_filer

    # Refuse up front rather than let the filer fall through to a login
    # attempt with empty credentials.
    if not kaizen_form_filer.load_session_state(user_id):
        print("No kept session for this user. Run `link` first.")
        return 1
    result = asyncio.run(_save_test_draft(user_id))
    status = result.get("status")
    print(
        json.dumps(
            {
                "status": status,
                "filled": len(result.get("filled") or []),
                "skipped": len(result.get("skipped") or []),
                "error": result.get("error") or "",
            },
            indent=2,
        )
    )
    if status in {"success", "partial"}:
        print(f'Now check Kaizen for a CBD draft starting "{TEST_DRAFT_MARKER}".')
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("link", "status", "save-test-draft"):
        command = sub.add_parser(name)
        command.add_argument("--user-id", type=int, required=True)
        if name == "status":
            command.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args(argv)
    if args.user_id <= 0:
        parser.error("--user-id must be a positive Telegram user id")
    if args.command == "link":
        return cmd_link(args.user_id)
    if args.command == "status":
        return cmd_status(args.user_id, args.log)
    return cmd_save_test_draft(args.user_id)


if __name__ == "__main__":
    raise SystemExit(main())
