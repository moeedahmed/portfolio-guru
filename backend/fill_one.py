#!/usr/bin/env python3
"""
fill_one.py — single-ticket Kaizen filer CLI.

A thin CLI wrapper around filer_router.route_filing() for external callers
(Claude Code via SSH, cron jobs, scripts). Reads a ticket JSON from stdin or a
file path, runs the router against the CDP-managed Chrome, prints a structured
JSON result to stdout.

Routing discipline (see AGENTS.md § Filing Routing Discipline): all filing goes
through filer_router.route_filing — never call fill_kaizen_form directly.
submit=False is hard-coded; this CLI is draft-only by invariant.

The Telegram bot (bot.py) also goes through filer_router.route_filing; this CLI
is the equivalent entrypoint for scripted/external callers.

USAGE
    # from stdin
    cat ticket.json | python fill_one.py

    # from file arg
    python fill_one.py ticket.json

    # dry-run (validates ticket shape, does not connect to Chrome)
    python fill_one.py ticket.json --dry-run

REQUIRED ENV
    KAIZEN_USERNAME    your Kaizen login email
    KAIZEN_PASSWORD    your Kaizen password
    KAIZEN_CDP_URL     (optional) defaults to http://localhost:18800 — Chrome
                       must be running with --remote-debugging-port=18800

TICKET JSON SCHEMA
    {
      "form_type": "TEACH",         # must be a key in FORM_UUIDS
      "save_as_draft": true,        # must be true — draft-only is the invariant
      "reuse_draft": false,         # optional — if true, edit an existing draft of
                                    # the same form_type instead of creating a new
                                    # one. Form-type based, not UUID based.
      "fields": {
        # Universal headers (all forms):
        "date_of_encounter": "15/4/2026",   # maps to startDate
        "end_date":          "15/4/2026",   # maps to endDate
        "description":       "one-line summary for timeline",

        # Form-specific fields — keys must match FORM_FIELD_MAP[form_type]:
        "title_of_session":         "...",
        "learning_outcomes":        "...",

        # Curriculum tags — list of KC label-prefixes (text-matched):
        "curriculum_links": [
          "Higher SLO9 Key Capability 1",
          "Higher SLO9 Key Capability 2",
          "Higher SLO10 Key Capability 1"
        ]
      },
      # Optional file attachment (FILE_UPLOAD form, supplementary evidence on any form):
      "attachment_drive_url": "https://drive.google.com/file/d/<id>/view",   # filer downloads
      "attachment_path":      "/local/path/to/file.pdf",                     # OR local path
    }

RESULT JSON (stdout)
    {
      "status":     "success" | "partial" | "failed",
      "filled":     ["date_of_teaching", "title_of_session", ...],
      "skipped":    [...],
      "errors":     [...],
      "screenshot": null
    }

EXIT CODES
    0 = success,  1 = partial,  2 = failed,  3 = usage/env error
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure peer imports (filer_router, kaizen_form_filer) resolve when invoked
# from any CWD.
_BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKEND_DIR))

try:
    from filer_router import route_filing
    from kaizen_form_filer import FORM_UUIDS, FORM_FIELD_MAP
except Exception as e:
    print(json.dumps({
        "status": "failed",
        "filled": [],
        "skipped": [],
        "errors": [f"import filer_router/kaizen_form_filer failed: {type(e).__name__}: {e}"],
        "screenshot": None,
    }))
    sys.exit(3)


def _die(code: int, message: str) -> None:
    """Emit a failed-status JSON result and exit."""
    print(json.dumps({
        "status": "failed",
        "filled": [],
        "skipped": [],
        "errors": [message],
        "screenshot": None,
    }, indent=2))
    sys.exit(code)


def _load_ticket() -> dict:
    """Read ticket JSON from --dry-run-compatible argv or stdin."""
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    if args:
        path = Path(args[0])
        if not path.exists():
            _die(3, f"Ticket file not found: {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            _die(3, f"Ticket JSON parse error ({path.name}): {e}")

    raw = sys.stdin.read().strip()
    if not raw:
        _die(3, "No input: pass ticket JSON via stdin or file arg (see fill_one.py --help)")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        _die(3, f"Ticket JSON parse error (stdin): {e}")


def _validate(ticket: dict) -> tuple[str, dict, str | None, bool, str | None, str | None]:
    """Validate ticket shape.

    Returns (form_type, fields, draft_uuid, save_as_draft, attachment_drive_url,
    attachment_path). draft_uuid is preserved in the return tuple for backwards
    compatibility with existing tests, but the router does not support UUID-based
    draft targeting — tickets that include a non-empty draft_uuid are rejected.
    """
    form_type = ticket.get("form_type")
    if not form_type:
        _die(3, "Missing required field: form_type")
    if form_type not in FORM_UUIDS:
        _die(3, f"Unknown form_type '{form_type}'. Known: {', '.join(sorted(FORM_UUIDS.keys()))}")

    fields = ticket.get("fields")
    if not isinstance(fields, dict):
        _die(3, "fields must be a dict matching FORM_FIELD_MAP schema")

    # Procedural Log needs both the event start date and the section-level
    # visible Date of Activity. Existing callers usually provide one date; copy
    # it into date_occurred_on so both fields are filled and verified.
    if form_type == "PROC_LOG" and fields.get("date_of_activity") and not fields.get("date_occurred_on"):
        fields = dict(fields)
        fields["date_occurred_on"] = fields["date_of_activity"]

    # Warn on unknown field keys — the filer will skip them, so surface the mismatch early
    known_keys = set(FORM_FIELD_MAP.get(form_type, {}).keys())
    # These are universal/special keys the filer handles regardless of form:
    universal = {"date_of_encounter", "date_occurred_on", "end_date", "description", "curriculum_section",
                 "curriculum_links", "key_capabilities", "stage", "stage_of_training",
                 "assessor_email", "assessor_query", "assessor_name"}
    unknown = [k for k in fields.keys() if k not in known_keys and k not in universal]
    if unknown:
        sys.stderr.write(
            f"warning: unknown field keys for {form_type}: {unknown}\n"
            f"         expected one of: {sorted(known_keys)}\n"
        )

    draft_uuid = ticket.get("draft_uuid")
    if draft_uuid:
        # filer_router.route_filing exposes reuse_draft (form-type based) but
        # not UUID-based draft targeting. Refuse rather than silently dropping
        # the targeting — the caller intended a specific draft.
        _die(3, "draft_uuid is not supported by filer_router.route_filing. "
                "Use reuse_draft=true to edit an existing draft of the same form_type, "
                "or remove draft_uuid to create a new draft.")
    save_as_draft = ticket.get("save_as_draft", True)
    # Draft-only is the product invariant for non-bot entrypoints. fill_one is
    # a CLI for external callers; submit/sign-off happens manually in Kaizen UI.
    if save_as_draft is not True:
        _die(3, "save_as_draft must be true. fill_one.py is draft-only; submit/sign-off via the Kaizen UI manually.")
    # Attachment support: ticket may include attachment_drive_url (for files
    # downloadable from Drive) or attachment_path (local file path on the
    # host running this filer). Both are forwarded through route_filing →
    # file_to_kaizen, which handles the upload on the deterministic path.
    attachment_drive_url = ticket.get("attachment_drive_url")
    attachment_path = ticket.get("attachment_path")
    return form_type, fields, draft_uuid, save_as_draft, attachment_drive_url, attachment_path


async def _run(
    form_type: str,
    fields: dict,
    save_as_draft: bool,
    reuse_draft: bool = False,
    attachment_drive_url: str | None = None,
    attachment_path: str | None = None,
) -> dict:
    username = os.environ.get("KAIZEN_USERNAME", "").strip()
    password = os.environ.get("KAIZEN_PASSWORD", "").strip()
    if not username or not password:
        _die(3, "KAIZEN_USERNAME and KAIZEN_PASSWORD must be set in environment")

    # save_as_draft is enforced True by _validate; pin submit=False here so the
    # draft-only invariant is independent of the field name on the ticket.
    return await route_filing(
        platform="kaizen",
        form_type=form_type,
        fields=fields,
        credentials={"username": username, "password": password},
        submit=False,
        reuse_draft=reuse_draft,
        attachment_path=attachment_path,
        attachment_drive_url=attachment_drive_url,
    )


def main() -> None:
    ticket = _load_ticket()
    form_type, fields, draft_uuid, save_as_draft, attachment_drive_url, attachment_path = _validate(ticket)
    reuse_draft = bool(ticket.get("reuse_draft", False))

    if "--dry-run" in sys.argv[1:]:
        # Validation-only path: confirm ticket shape without touching Chrome
        print(json.dumps({
            "status": "success",
            "filled": [],
            "skipped": [],
            "errors": [],
            "screenshot": None,
            "dry_run": True,
            "form_type": form_type,
            "form_uuid": FORM_UUIDS[form_type],
            "field_count": len(fields),
            "kc_count": len(fields.get("curriculum_links") or []),
            "save_as_draft": save_as_draft,
            "reuse_draft": reuse_draft,
            "draft_uuid": draft_uuid,
        }, indent=2))
        sys.exit(0)

    try:
        result = asyncio.run(_run(
            form_type,
            fields,
            save_as_draft,
            reuse_draft=reuse_draft,
            attachment_drive_url=attachment_drive_url,
            attachment_path=attachment_path,
        ))
    except KeyboardInterrupt:
        _die(2, "interrupted")
    except Exception as e:
        _die(2, f"router raised: {type(e).__name__}: {e}")

    # Normalise route_filing's contract (error: str | None, errors absent) into
    # the legacy CLI shape (errors: list, screenshot: path|None) external
    # callers parse from stdout.
    errors: list[str] = []
    if result.get("error"):
        errors.append(result["error"])
    errors.extend(result.get("errors", []) or [])
    output = {
        "status": result.get("status", "failed"),
        "filled": result.get("filled", []),
        "skipped": result.get("skipped", []),
        "errors": errors,
        "screenshot": result.get("screenshot") or result.get("screenshot_path"),
        "method": result.get("method"),
    }
    print(json.dumps(output, indent=2))
    sys.exit({"success": 0, "partial": 1, "failed": 2}.get(output.get("status"), 2))


if __name__ == "__main__":
    main()
