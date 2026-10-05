#!/usr/bin/env python3
"""Exact-SHA local staging receipts. No credentials and no release-card changes."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TARGET = "portfolio_guru_test_bot"


def proof_dir() -> Path:
    return Path(os.environ.get("PORTFOLIO_GURU_STAGING_PROOF_DIR", str(
        Path.home() / ".openclaw/data/portfolio-guru-staging/staging-proofs"
    )))


def read(sha: str) -> dict:
    value = json.loads((proof_dir() / f"{sha}.json").read_text())
    if not isinstance(value, dict) or value.get("sha") != sha or value.get("target") != TARGET:
        raise ValueError("staging receipt does not name this SHA and test bot")
    return value


def write(sha: str, value: dict) -> None:
    directory = proof_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".proof-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, directory / f"{sha}.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("deploy", "automated", "approve", "status", "gate"))
    parser.add_argument("--sha", required=True)
    parser.add_argument("--result", choices=("pass", "fail"))
    parser.add_argument("--note")
    parser.add_argument("--risk", choices=("internal", "telegram", "broad"), default="internal")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.sha):
        parser.error("--sha requires a full 40-hex commit")
    sha = args.sha.lower()
    now = datetime.now(timezone.utc).isoformat()
    try:
        if args.action == "deploy":
            if not args.result:
                raise ValueError("deploy requires --result")
            value = {"sha": sha, "target": TARGET, "utc_time": now,
                     "smoke": args.result, "automated": "pending", "moeed_approved": False}
        else:
            value = read(sha)
            if args.action == "status":
                print(json.dumps(value, indent=2, sort_keys=True))
                return 0
            if value.get("smoke") != "pass":
                raise ValueError(f"staging deploy smoke missing: scripts/stage.sh deploy --sha {sha}")
            if args.action == "automated":
                if not args.result:
                    raise ValueError("automated requires --result")
                value.update(automated=args.result, automated_at=now, moeed_approved=False)
            else:
                if value.get("automated") != "pass":
                    raise ValueError(f"staging automated smoke missing: scripts/stage.sh smoke --sha {sha}")
                if args.action == "approve":
                    note = args.note or ""
                    if not note.strip() or len(note) > 500 or any(ord(c) < 32 for c in note):
                        raise ValueError("approve requires a short, non-empty single-line --note")
                    value.update(moeed_approved=True, approved_at=now, approval_note=note)
                elif args.risk != "internal" and value.get("moeed_approved") is not True:
                    raise ValueError(f'staging owner approval missing: scripts/stage.sh approve --sha {sha} --note "Moeed tried test bot and tapped Ship"')
                if args.action == "gate":
                    return 0
        write(sha, value)
        return 0
    except FileNotFoundError:
        print(f"STAGING BLOCKED: scripts/stage.sh deploy --sha {sha}")
        return 1
    except (OSError, ValueError, TypeError) as exc:
        print(f"STAGING BLOCKED: {exc}; scripts/stage.sh deploy --sha {sha}" if args.action == "gate" and "scripts/stage.sh" not in str(exc) else f"STAGING BLOCKED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
