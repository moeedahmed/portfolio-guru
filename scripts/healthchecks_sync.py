#!/usr/bin/env python3
"""Sync Healthchecks.io checks from a declarative manifest. Idempotent.

Every scheduled job on the Mac Mini is a launchd agent with a reverse-DNS
label. This script keeps a Healthchecks.io check per job, named after that
label, so an alert names the exact thing to restart with no translation step.

Why a script and not the web UI: clicking does not survive. A manifest in the
repo can be reviewed, diffed, re-applied after an account change, and extended
by an agent without anyone remembering what was configured by hand.

Idempotency comes from the API's own upsert: `unique: ["name"]` updates an
existing check with the same name instead of creating a duplicate. Re-running
this is always safe.

Usage:
    scripts/healthchecks_sync.py            # apply the manifest
    scripts/healthchecks_sync.py --dry-run  # show what would change
    scripts/healthchecks_sync.py --list     # show existing checks and status

The API key is read from HEALTHCHECKS_API_KEY, else BWS key
HEALTHCHECKS_API_KEY. It must be a read-write project API key
(Healthchecks.io → Project Settings → API keys).

Ping URLs are printed, never committed: they are write-capable credentials.
Store them in BWS under the names the manifest gives.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

API_ROOT = "https://healthchecks.io/api/v3"

# Timezone matters: launchd StartCalendarInterval fires in LOCAL time, so a
# cron schedule declared in UTC would drift by an hour half the year and page
# the operator every night through British Summer Time.
TZ = "Europe/London"

# The manifest. One entry per scheduled job that must not fail silently.
#
# `name` mirrors the launchd label exactly — an alert saying
# "com.portfolioguru.backup is down" tells you precisely what to restart.
#
# Use `schedule` (cron) for jobs that run on a clock, `timeout` for jobs that
# ping continuously. `grace` is how late is allowed before alerting: generous
# enough to absorb a slow run, tight enough to matter.
CHECKS = [
    {
        "name": "com.portfolioguru.bot",
        "tags": "portfolio-guru mac-mini liveness",
        "desc": (
            "Telegram bot liveness. bot.py pings every 5 min from a job-queue "
            "task. Silence means the process is gone or the event loop has "
            "wedged — launchd's KeepAlive only catches the former. "
            "Restart: launchctl bootout/bootstrap com.portfolioguru.bot. "
            "Runbook: docs/disaster-recovery.md"
        ),
        "timeout": 300,
        "grace": 900,
        "bws_secret": "PG_HEARTBEAT_URL",
    },
    {
        "name": "com.portfolioguru.backup",
        "tags": "portfolio-guru mac-mini backup",
        "desc": (
            "Nightly encrypted off-device backup to gs://portfolio-guru-eu-backups. "
            "Pings on verified upload, /fail on failure. Silence means the job "
            "never ran at all — the case no in-script check can report. "
            "This check exists because off-device upload failed silently for 53 "
            "nights (2026-06-26 to 2026-08-17). Runbook: scripts/restore_db.md"
        ),
        "schedule": "30 3 * * *",
        "grace": 7200,
        "bws_secret": "PG_BACKUP_HEALTHCHECK_URL",
    },
    {
        # NOTE (2026-08-24): the two entries above are named after launchd
        # labels; the healthchecks skill was corrected the same day to say the
        # dashboard convention is "Mac Mini <Service>". Run --list and
        # reconcile before the next apply — if the dashboard uses the other
        # scheme, re-running this manifest creates duplicates rather than
        # updating. This entry is left in the file's existing scheme so one
        # apply cannot silently split the naming three ways.
        "name": "com.portfolioguru.bot.signoff-chase",
        "tags": "portfolio-guru mac-mini nudge",
        "desc": (
            "Weekly Portfolio Health sign-off chase (Wed 19:00 Europe/London), "
            "a job-queue task inside bot.py. Pings on EVERY completed run, "
            "including runs that message nobody, and /fail if the run aborts. "
            "That matters more here than elsewhere: this feature's success "
            "signal is silence, so a dead job and a clean portfolio look "
            "identical to the user — only this check tells them apart. "
            "Silence means the bot is down, the job was not registered "
            "(PG_ENABLE_SIGNOFF_CHASE unset), or the event loop wedged. "
            "Restart: launchctl bootout/bootstrap com.portfolioguru.bot. "
            "Runbook: docs/disaster-recovery.md"
        ),
        "schedule": "0 19 * * 3",  # tz is applied for us: Europe/London
        # A weekly job needs generous lateness: two days still alerts well
        # before the next scheduled run, without paging on a slow evening.
        "grace": 172800,
        "bws_secret": "PG_SIGNOFF_CHASE_HEALTHCHECK_URL",
    },
]


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def get_api_key() -> str:
    key = os.environ.get("HEALTHCHECKS_API_KEY", "").strip()
    if key:
        return key

    token_path = os.path.expanduser("~/.openclaw/.bws-token")
    bws = os.path.expanduser("~/.cargo/bin/bws")
    if not (os.path.exists(token_path) and os.path.exists(bws)):
        die("no HEALTHCHECKS_API_KEY in env and BWS is unavailable")

    token = open(token_path).read().strip()
    out = subprocess.run(
        [bws, "secret", "list", "--output", "json"],
        env={**os.environ, "BWS_ACCESS_TOKEN": token},
        capture_output=True,
        text=True,
    ).stdout
    try:
        secrets = json.loads(out)
    except json.JSONDecodeError:
        die("could not read secrets from BWS")
    key = next(
        (s["value"] for s in secrets if s.get("key") == "HEALTHCHECKS_API_KEY"), ""
    )
    if not key:
        die(
            "HEALTHCHECKS_API_KEY not found in env or BWS.\n"
            "  Create a read-write API key at Healthchecks.io -> Project Settings\n"
            "  -> API keys, then store it in BWS as HEALTHCHECKS_API_KEY."
        )
    return key


def api(key: str, method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{API_ROOT}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"status": resp.status, "body": json.loads(resp.read() or "{}")}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        if e.code == 401:
            die("API key rejected (401). Is it a read-write project key?")
        die(f"{method} {path} failed: HTTP {e.code} — {detail}")
    except urllib.error.URLError as e:
        die(f"cannot reach healthchecks.io: {e.reason}")
    return {}


def cmd_list(key: str) -> None:
    checks = api(key, "GET", "/checks/")["body"].get("checks", [])
    if not checks:
        print("No checks exist in this project yet.")
        return
    print(f"{len(checks)} check(s):\n")
    for c in sorted(checks, key=lambda c: c.get("name", "")):
        print(f"  {c.get('status', '?'):8} {c.get('name', '(unnamed)')}")
        print(f"           last ping: {c.get('last_ping') or 'never'}")
    print()


def cmd_apply(key: str, dry_run: bool) -> None:
    if dry_run:
        print("DRY RUN — nothing will be created or modified.\n")

    results = []
    for spec in CHECKS:
        payload = {k: v for k, v in spec.items() if k != "bws_secret"}
        # Send alerts to every integration configured on the project. Without
        # this a check is created that watches faithfully and tells nobody.
        payload["channels"] = "*"
        if "schedule" in payload:
            payload["tz"] = TZ
        payload["unique"] = ["name"]

        if dry_run:
            print(f"would apply: {spec['name']}")
            print(f"  {json.dumps(payload, indent=2)}\n")
            continue

        res = api(key, "POST", "/checks/", payload)
        verb = "created" if res["status"] == 201 else "updated"
        ping_url = res["body"].get("ping_url", "")
        print(f"{verb}: {spec['name']}")
        results.append((spec["bws_secret"], ping_url))

    if dry_run or not results:
        return

    print("\n" + "=" * 68)
    print("Ping URLs — these are write-capable credentials. Do NOT commit them.")
    print("Store each in BWS under the name shown, then restart the bot:\n")
    for secret_name, url in results:
        print(f"  {secret_name}")
        print(f"    {url}\n")
    print("Verify afterwards with: scripts/healthchecks_sync.py --list")
    print("=" * 68)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="show changes only")
    p.add_argument("--list", action="store_true", help="list existing checks")
    args = p.parse_args()

    key = get_api_key()
    if args.list:
        cmd_list(key)
    else:
        cmd_apply(key, args.dry_run)


if __name__ == "__main__":
    main()
