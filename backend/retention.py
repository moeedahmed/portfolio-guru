"""Compatibility retention hook for the metadata-only London mirror.

pg_filings has no clinical columns to purge. Local draft and persistence
retention are handled by their own stores; this hook never contacts Supabase.
"""
import os
from datetime import datetime, timedelta, timezone

DEFAULT_RETENTION_DAYS = 180


def retention_days() -> int:
    try:
        return max(1, int(os.environ.get("PG_CLINICAL_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def purge_expired_clinical_content(now: datetime | None = None) -> dict:
    """Report that no clinical content is held, preserving the scheduled hook."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=retention_days())).isoformat()
    return {
        "status": "ok", "rows": 0, "cutoff": cutoff,
        "reason": "pg_filings holds no clinical content",
    }
