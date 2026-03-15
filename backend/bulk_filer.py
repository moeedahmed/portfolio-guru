"""
Bulk filer — file multiple entries sequentially via route_filing.
"""

import logging
import sys
from typing import Any, Dict, List

from filer_router import route_filing

logger = logging.getLogger(__name__)


async def bulk_file(entries: list[dict], credentials: dict, platform: str = "kaizen") -> list[dict]:
    """
    File multiple entries sequentially.
    Each entry is a dict with keys: form_type, fields (matching FormDraft structure)
    Returns list of results: {entry_index, form_type, status, error, draft_url}
    Stops on unrecoverable error. Continues on per-entry failures.
    """
    results = []
    total = len(entries)

    for i, entry in enumerate(entries):
        form_type = entry.get("form_type", "")
        fields = entry.get("fields", {})
        curriculum_links = entry.get("curriculum_links")

        print(f"Filing {i + 1}/{total}: {form_type}...", file=sys.stderr)

        try:
            result = await route_filing(
                platform=platform,
                form_type=form_type,
                fields=fields,
                credentials=credentials,
                curriculum_links=curriculum_links,
            )
            results.append({
                "entry_index": i,
                "form_type": form_type,
                "status": result.get("status", "failed"),
                "error": result.get("error"),
                "filled": result.get("filled", []),
                "skipped": result.get("skipped", []),
            })
        except Exception as e:
            logger.error(f"Bulk filer error on entry {i} ({form_type}): {e}")
            results.append({
                "entry_index": i,
                "form_type": form_type,
                "status": "failed",
                "error": str(e),
                "filled": [],
                "skipped": [],
            })

    return results
