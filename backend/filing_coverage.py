"""
Filing coverage tracker — tracks how many times each form has been filed
and field fill rates. Used by filer_router.py to decide between Playwright
and browser-use.
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

COVERAGE_PATH = Path(__file__).parent / "filing_coverage.json"

# Fields considered "important" — low fill rates here trigger browser-use
IMPORTANT_FIELDS = {
    "curriculum_links", "key_capabilities", "reflection",
    "clinical_reasoning", "case_to_discuss",
}

PLAYWRIGHT_MIN_RUNS = 5
FILL_RATE_THRESHOLD = 0.5
CURRICULUM_FILL_THRESHOLD = 0.8


def load_coverage() -> Dict[str, Any]:
    """Read filing_coverage.json. Returns empty dict if missing."""
    if not COVERAGE_PATH.exists():
        return {}
    try:
        return json.loads(COVERAGE_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not load coverage data: {e}")
        return {}


def _save_coverage(data: Dict[str, Any]) -> None:
    """Write coverage data back to disk."""
    try:
        COVERAGE_PATH.write_text(json.dumps(data, indent=2))
    except OSError as e:
        logger.error(f"Could not save coverage data: {e}")


def record_run(
    form_type: str,
    method: str,
    filled_fields: List[str],
    skipped_fields: List[str],
) -> None:
    """Update coverage after a filing run."""
    data = load_coverage()
    entry = data.setdefault(form_type, {
        "playwright_runs": 0,
        "browser_use_runs": 0,
        "field_fill_rates": {},
        "user_pushbacks": [],
        "last_updated": str(date.today()),
    })

    if method == "deterministic":
        entry["playwright_runs"] = entry.get("playwright_runs", 0) + 1
    else:
        entry["browser_use_runs"] = entry.get("browser_use_runs", 0) + 1

    # Update field fill rates (running average)
    rates = entry.setdefault("field_fill_rates", {})
    all_fields = set(filled_fields) | set(skipped_fields)
    total_runs = entry.get("playwright_runs", 0) + entry.get("browser_use_runs", 0)

    for field in all_fields:
        was_filled = 1.0 if field in filled_fields else 0.0
        old_rate = rates.get(field, 0.0)
        if total_runs <= 1:
            rates[field] = was_filled
        else:
            # Exponential moving average (recent runs weighted more)
            rates[field] = round(old_rate * 0.7 + was_filled * 0.3, 3)

    entry["last_updated"] = str(date.today())
    _save_coverage(data)


def record_pushback(form_type: str, field_name: str) -> None:
    """Record that the user flagged a field as missed after filing."""
    data = load_coverage()
    entry = data.setdefault(form_type, {
        "playwright_runs": 0,
        "browser_use_runs": 0,
        "field_fill_rates": {},
        "user_pushbacks": [],
        "last_updated": str(date.today()),
    })
    pushbacks = entry.setdefault("user_pushbacks", [])
    if field_name not in pushbacks:
        pushbacks.append(field_name)
    entry["last_updated"] = str(date.today())
    _save_coverage(data)
    logger.info(f"Recorded pushback for {form_type}.{field_name}")


def should_use_browser_use(
    form_type: str,
    curriculum_was_requested: bool = False,
) -> bool:
    """
    Decide whether to use browser-use instead of Playwright.

    Returns True if:
    - playwright_runs < 5
    - any important field has fill_rate < 0.5
    - curriculum was requested AND curriculum fill rate < 0.8
    - any field in user_pushbacks
    """
    data = load_coverage()
    entry = data.get(form_type)

    if not entry:
        # Never filed — use browser-use
        return True

    playwright_runs = entry.get("playwright_runs", 0)
    if playwright_runs < PLAYWRIGHT_MIN_RUNS:
        return True

    rates = entry.get("field_fill_rates", {})
    pushbacks = entry.get("user_pushbacks", [])

    # Any pushback field → browser-use
    if pushbacks:
        return True

    # Check important fields
    for field in IMPORTANT_FIELDS:
        if field in rates and rates[field] < FILL_RATE_THRESHOLD:
            return True

    # Curriculum check
    if curriculum_was_requested:
        curriculum_rate = rates.get("curriculum_links", 0.0)
        if curriculum_rate < CURRICULUM_FILL_THRESHOLD:
            return True

    return False
