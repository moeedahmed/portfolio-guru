"""
DOM learner — extracts field UUIDs discovered during browser-use runs
and patches FORM_FIELD_MAP in kaizen_filer.py automatically.

After each browser-use filing, compares discovered UUIDs against the
existing Playwright mapping. New fields are added to FORM_FIELD_MAP
so future Playwright runs can fill them deterministically.

Auto-patching of tracked source is OFF by default. Set
PORTFOLIO_GURU_DOM_AUTOLEARN=1 to enable it for explicit ops runs.
Without the flag, learn_from_browser_use_run is a no-op so neither
ordinary runtime filings nor tests can mutate tracked source files.

The learning log is runtime state; when autolearn is opted in, it writes
to ~/.openclaw/data/portfolio-guru/dom_learning_log.json by default.
Override via PORTFOLIO_GURU_DOM_LEARNING_LOG_PATH (tests redirect to tmp).
KAIZEN_FILER_PATH still resolves to the tracked kaizen_form_filer.py
source — patching that file is the whole point of autolearn, so the
opt-in flag, not the path, is the safety boundary.
"""

import json
import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_RUNTIME_DATA_DIR = Path.home() / ".openclaw" / "data" / "portfolio-guru"


def _autolearn_enabled() -> bool:
    """Auto-patching of tracked source must be explicitly opted in."""
    return os.environ.get("PORTFOLIO_GURU_DOM_AUTOLEARN") == "1"


def _resolve_kaizen_filer_path() -> Path:
    override = os.environ.get("PORTFOLIO_GURU_KAIZEN_FILER_PATH")
    if override:
        return Path(override)
    return Path(__file__).parent / "kaizen_form_filer.py"


def _resolve_learning_log_path() -> Path:
    override = os.environ.get("PORTFOLIO_GURU_DOM_LEARNING_LOG_PATH")
    if override:
        return Path(override)
    return _RUNTIME_DATA_DIR / "dom_learning_log.json"


KAIZEN_FILER_PATH = Path(__file__).parent / "kaizen_form_filer.py"
DOM_LEARNING_LOG_PATH = _RUNTIME_DATA_DIR / "dom_learning_log.json"


def _load_learning_log() -> list:
    """Load existing learning log entries."""
    path = _resolve_learning_log_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_learning_log(entries: list) -> None:
    """Persist learning log."""
    path = _resolve_learning_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, indent=2))
    except OSError as e:
        logger.error(f"Could not save DOM learning log: {e}")


def _get_current_field_map(form_type: str) -> Dict[str, str]:
    """Read FORM_FIELD_MAP[form_type] from kaizen_form_filer.py at import time."""
    try:
        from kaizen_form_filer import FORM_FIELD_MAP
        return dict(FORM_FIELD_MAP.get(form_type, {}))
    except ImportError:
        logger.error("Could not import FORM_FIELD_MAP from kaizen_form_filer")
        return {}


def _patch_form_field_map(form_type: str, new_fields: Dict[str, str]) -> bool:
    """
    Patch FORM_FIELD_MAP in kaizen_filer.py by adding new field→UUID entries
    to the specified form_type's dict. Uses string replacement to avoid
    rewriting the whole file.

    Returns True if the file was patched successfully.
    """
    if not new_fields:
        return False

    path = _resolve_kaizen_filer_path()
    try:
        source = path.read_text()
    except OSError as e:
        logger.error(f"Could not read kaizen_filer.py: {e}")
        return False

    # Find the closing brace of the form_type's dict within FORM_FIELD_MAP
    # Pattern: "FORM_TYPE": { ... }
    # We look for the form_type key and find its closing brace
    pattern = rf'("{form_type}":\s*\{{[^}}]*)\}}'
    match = re.search(pattern, source, re.DOTALL)

    if not match:
        logger.warning(f"Could not find {form_type} entry in FORM_FIELD_MAP")
        return False

    existing_block = match.group(1)

    # Build new entries
    new_entries_lines = []
    for field, uuid in new_fields.items():
        new_entries_lines.append(f'        "{field}": "{uuid}",')
    new_entries_str = "\n" + "\n".join(new_entries_lines)

    # Insert before closing brace
    patched_block = existing_block + new_entries_str + "\n    }"
    patched_source = source[:match.start()] + patched_block + source[match.end():]

    try:
        path.write_text(patched_source)
        logger.info(f"Patched FORM_FIELD_MAP.{form_type} with {len(new_fields)} new fields")
        return True
    except OSError as e:
        logger.error(f"Could not write patched kaizen_filer.py: {e}")
        return False


async def learn_from_browser_use_run(
    form_type: str,
    browser_use_result: Dict[str, Any],
) -> Dict[str, str]:
    """
    Extract any new field UUIDs from a browser-use run result and patch
    kaizen_filer.py's FORM_FIELD_MAP.

    Off by default — set PORTFOLIO_GURU_DOM_AUTOLEARN=1 to opt in. The
    canonical pattern is to discover UUIDs via deliberate scraping/ops
    tooling and review the diff before committing; silent runtime patching
    of tracked source on every browser-use call is too risky for a private
    beta whose live bot runs unattended.

    Args:
        form_type: Form short code (e.g. "QIAT")
        browser_use_result: Result dict from browser_filer.py, expected to
            contain "discovered_uuids": {"field_name": "uuid"}

    Returns:
        Dict of newly learned field→UUID mappings (empty if nothing new or
        if auto-learn is disabled).
    """
    if not _autolearn_enabled():
        logger.debug(
            "DOM auto-learn disabled (PORTFOLIO_GURU_DOM_AUTOLEARN!=1) — "
            "skipping patch for %s",
            form_type,
        )
        return {}

    discovered = browser_use_result.get("discovered_uuids", {})
    if not discovered:
        logger.debug(f"No discovered_uuids in browser-use result for {form_type}")
        return {}

    # Compare against existing FORM_FIELD_MAP
    existing = _get_current_field_map(form_type)
    new_fields = {}

    for field, uuid in discovered.items():
        if field not in existing and uuid:
            new_fields[field] = uuid

    if not new_fields:
        logger.debug(f"All discovered UUIDs for {form_type} already in FORM_FIELD_MAP")
        return {}

    # Patch kaizen_filer.py
    from filing_coverage import load_coverage
    coverage = load_coverage()
    entry = coverage.get(form_type, {})
    run_count = entry.get("browser_use_runs", 0)

    patched = _patch_form_field_map(form_type, new_fields)

    # Log to DOM learning log
    log_entries = _load_learning_log()
    for field, uuid in new_fields.items():
        log_entries.append({
            "date": str(date.today()),
            "form_type": form_type,
            "field": field,
            "uuid": uuid,
            "source": f"browser-use run #{run_count}",
            "patched": patched,
        })
    _save_learning_log(log_entries)

    logger.info(f"Learned {len(new_fields)} new UUIDs for {form_type}: {list(new_fields.keys())}")
    return new_fields
