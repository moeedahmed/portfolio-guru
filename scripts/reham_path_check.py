#!/usr/bin/env python3
"""Static guardrails for the beta-user phone journey.

The "Reham path" is the sanity check for user-facing Telegram workflow fixes:
do not close a change just because the reported line is patched. Check the
adjacent phone journey, hide implementation detail, and prove the real bot is
running the committed code.
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []

    bot = read("backend/bot.py")
    dogfood = read("scripts/dogfood_smoke.sh")
    preflight = read("scripts/preflight.sh")
    dev_workflow = read("docs/dev-workflow.md")

    blocked_bot_literals = [
        "Consent recorded (version",
        "You consented to version",
        "Current consent version",
    ]
    for literal in blocked_bot_literals:
        require(
            literal not in bot,
            f"User-facing bot copy exposes internal consent metadata: {literal!r}",
            failures,
        )

    require(
        'msg = f"❌ Filing didn\'t complete\\n{form_name}\\n\\n{body}"' in bot,
        "FORM_UNAVAILABLE must use the clean body only, without details_suffix.",
        failures,
    )
    require(
        "Review the consent notice before your first case." in bot
        and "🔐 Review consent" in bot,
        "Setup Step 3 must stay a short checkpoint with a separate Review consent action.",
        failures,
    )

    require(
        "scripts/verify_live_runtime.py" in dogfood,
        "Dogfood smoke must prove the real launchd bot runtime, not only a restart command.",
        failures,
    )
    require(
        "Reham path" in dogfood,
        "Dogfood smoke must include the beta-user phone journey check.",
        failures,
    )
    require(
        "scripts/reham_path_check.py" in preflight,
        "Preflight must run the Reham path static guardrail.",
        failures,
    )
    require(
        "Reham path" in dev_workflow,
        "Developer workflow docs must name the Reham path closure gate.",
        failures,
    )

    if failures:
        print("REHAM_PATH_CHECK_FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("REHAM_PATH_CHECK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
