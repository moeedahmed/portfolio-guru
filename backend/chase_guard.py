"""
Chase Guard — prevents assessor chase spamming.
Ported from Medic's kaizen_chase_guard.py.

Rules:
- 14-day minimum between chases per assessor
- Max 3 chases per assessor total
"""

import json
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

CHASE_LOG_PATH = os.path.join(os.path.dirname(__file__), "chase_log.json")


def _load_log() -> dict:
    try:
        with open(CHASE_LOG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "_meta": {
                "description": "Kaizen assessor chase log",
                "version": "2026-03-15",
                "rules": {
                    "minDaysBetweenChases": 14,
                    "maxChasesPerAssessor": 3,
                    "requireApprovalAfterMax": True,
                    "preSendAuditRequired": True,
                },
            },
            "chases": [],
        }


def _save_log(data: dict) -> None:
    with open(CHASE_LOG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_assessor_chases(email: str) -> list[dict]:
    """Get all chase entries for an assessor."""
    data = _load_log()
    return [c for c in data["chases"] if c["assessor_email"].lower() == email.lower()]


def check_allowed(email: str) -> tuple[bool, str]:
    """
    Check if a chase is allowed for this assessor.
    Returns (allowed: bool, reason: str).
    """
    data = _load_log()
    rules = data["_meta"]["rules"]
    chases = [c for c in data["chases"] if c["assessor_email"].lower() == email.lower()]

    if not chases:
        return True, "No previous chases — allowed"

    # Check total count
    if len(chases) >= rules["maxChasesPerAssessor"]:
        return False, (
            f"BLOCKED: {len(chases)} chases already sent "
            f"(max {rules['maxChasesPerAssessor']}). Manual approval required."
        )

    # Check recency
    last_chase = max(chases, key=lambda c: c["date"])
    last_date = datetime.fromisoformat(last_chase["date"])
    days_since = (datetime.now() - last_date).days
    min_days = rules["minDaysBetweenChases"]

    if days_since < min_days:
        next_allowed = (last_date + timedelta(days=min_days)).strftime("%d %b %Y")
        return False, (
            f"BLOCKED: Last chase was {days_since} days ago "
            f"(min {min_days} days). Next allowed: {next_allowed}"
        )

    return True, f"Allowed — {len(chases)} previous chase(s), last {days_since} days ago"


def log_chase(email: str, name: str, method: str = "manual", ticket_summary: str = "") -> dict:
    """Log a chase that was confirmed by the user."""
    data = _load_log()
    chases_for = [c for c in data["chases"] if c["assessor_email"].lower() == email.lower()]
    entry = {
        "assessor_email": email.lower(),
        "assessor_name": name,
        "date": datetime.now().isoformat()[:10],
        "method": method,
        "tickets": ticket_summary,
        "chase_number": len(chases_for) + 1,
    }
    data["chases"].append(entry)
    _save_log(data)
    return entry
