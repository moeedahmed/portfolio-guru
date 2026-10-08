#!/usr/bin/env python3
"""On-demand only. Approval must come from the foreground environment."""
import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from kaizen_live_check import FAILED, REFUSED, GuardRefusal, parser, require_approval, run_check, write_report


def main():
    args = parser().parse_args()
    try:
        require_approval()  # Before dotenv, credential imports or any browser.
        from dotenv import load_dotenv
        load_dotenv(ROOT / "backend" / ".env")
        require_approval()  # Staging/offline settings in dotenv also fail closed.
        os.environ["PYTHON_DOTENV_DISABLED"] = "1"
        directory = ROOT / ".artifacts" / "kaizen-live-check" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        report, code = asyncio.run(run_check(args.forms))
        write_report(report, directory)
        print(f"Kaizen check {report['status']}. Reports: {directory}")
        return code
    except GuardRefusal as exc:
        print(f"Kaizen check refused: {exc}", file=sys.stderr)
        return REFUSED
    except Exception:
        print("Kaizen check failed. No provider error details retained.", file=sys.stderr)
        return FAILED


if __name__ == "__main__":
    sys.exit(main())
