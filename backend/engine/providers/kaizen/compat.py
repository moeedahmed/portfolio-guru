"""Compatibility adapter — same API as kaizen_form_filer.fill_kaizen_form().

Drop-in replacement for the old Playwright-based filer.
Change imports to use this module instead:

    # Old:
    from kaizen_form_filer import fill_kaizen_form

    # New:
    from engine.providers.kaizen.compat import fill_kaizen_form
"""

import asyncio, json, os, sys
from pathlib import Path
from typing import Optional

# Re-export the existing FORM_UUIDS and FORM_FIELD_MAP for compatibility
_backend = str(Path(__file__).resolve().parent.parent.parent.parent)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

try:
    from extractor import FORM_UUIDS
except ImportError:
    FORM_UUIDS = {}

try:
    from kaizen_form_filer import FORM_FIELD_MAP
except ImportError:
    FORM_FIELD_MAP = {}


def fill_kaizen_form(
    form_type: str,
    fields: dict,
    username: str,
    password: str,
    draft_uuid: str = None,
    save_as_draft: bool = True,
    screenshot_path: str = None,
) -> dict:
    """Drop-in replacement for kaizen_form_filer.fill_kaizen_form().

    Same signature, same return format. Uses browser-harness CDP
    instead of Playwright.

    Returns:
        {
            "status": "success" | "failed",
            "filled": [field_keys...],
            "skipped": [field_keys...],
            "errors": [error_strings...],
            "screenshot": path_or_None,
        }
    """
    from . import KaizenProvider

    provider = KaizenProvider(username, password)
    filled = []
    skipped = []
    errors = []

    try:
        if not provider.connect():
            return {
                "status": "failed",
                "filled": filled,
                "skipped": skipped,
                "errors": ["Login failed"],
                "screenshot": None,
            }

        # Fill the form
        try:
            provider.fill_form(form_type, fields)
            filled = list(fields.keys())
        except Exception as e:
            errors.append(f"Fill error: {e}")
            skipped = list(fields.keys())

        # Save as draft if requested
        doc_url = None
        if save_as_draft and not errors:
            try:
                doc_url = provider.save_draft()
            except Exception as e:
                errors.append(f"Save error: {e}")

        # Screenshot
        screenshot = None
        if screenshot_path and not errors:
            try:
                provider.screenshot(screenshot_path)
                screenshot = screenshot_path
            except Exception:
                pass

        provider.disconnect()

        status = "success" if not errors else ("partial" if filled else "failed")
        return {
            "status": status,
            "filled": filled,
            "skipped": skipped,
            "errors": errors,
            "screenshot": screenshot,
            "doc_url": doc_url,
        }

    except Exception as e:
        return {
            "status": "failed",
            "filled": filled,
            "skipped": skipped,
            "errors": [str(e)],
            "screenshot": None,
        }
