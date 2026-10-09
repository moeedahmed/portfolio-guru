#!/usr/bin/env python3
"""On-demand only. Approval must come from the foreground environment."""
import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from kaizen_live_check import (
    FAILED, REFUSED, GuardRefusal, parser, require_approval, mapped_forms,
    run_check, write_report, run_inspect, write_inspect_report,
)


def main():
    args = parser().parse_args()
    try:
        require_approval()  # Before dotenv, credential imports or any browser.
        from dotenv import load_dotenv
        load_dotenv(ROOT / "backend" / ".env")
        require_approval()  # Staging/offline settings in dotenv also fail closed.
        os.environ["PYTHON_DOTENV_DISABLED"] = "1"
        if args.inspect_drafts:
            source = Path(args.inspect_drafts).resolve()
            report, code = asyncio.run(run_inspect(source, args.forms))
            write_inspect_report(report, source.parent)
            print(f"Kaizen inspection {report['status']}. Reports: {source.parent / 'inspect.json'} and {source.parent / 'inspect.md'}")
            return code
        directory = ROOT / ".artifacts" / "kaizen-live-check" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        report, code = asyncio.run(run_check(mapped_forms() if args.all_mapped else args.forms))
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
