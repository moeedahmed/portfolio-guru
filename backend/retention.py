"""Time-based retention for durable clinical case content (launch checklist 1.5).

Data map (why this module is small): the only DURABLE store of clinical
content is the Supabase ``portfolio_cases`` mirror — Fernet-encrypted
``case_text_encrypted`` plus encrypted ``extracted_fields``. Everything else
is transient or non-clinical:

- attachment/voice temp files are unlinked inline after processing (bot.py);
- usage.db rows are RCEM taxonomy + timestamps, no patient detail;
- conversation persistence holds at most the in-flight draft, cleared on save.

The purge NULLs the clinical payload of expired rows but keeps the row —
``form_type``/``status``/``created_at`` stay, so usage history and ARCP-health
features (which read only those columns) are unaffected.

The window is PG_CLINICAL_RETENTION_DAYS (default 180) and is stated in
docs/legal/privacy-policy.md §7 — change them together.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 180


def retention_days() -> int:
    try:
        return max(1, int(os.environ.get("PG_CLINICAL_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def purge_expired_clinical_content(now: datetime | None = None) -> dict:
    """Null clinical content on portfolio_cases rows older than the window.

    Sync (the Supabase client is sync) — call via asyncio.to_thread from the
    bot. Best-effort like every other Supabase touch: failures are logged and
    reported, never raised, and re-running is idempotent (already-nulled rows
    just match the filter again with nothing to change).
    """
    from supabase_sync import _supabase

    sb = _supabase()
    if sb is None:
        return {"status": "disabled"}
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=retention_days())).isoformat()
    try:
        resp = (
            sb.table("portfolio_cases")
            .update({"case_text_encrypted": None, "extracted_fields": None})
            .lt("created_at", cutoff)
            .execute()
        )
        purged = len(resp.data or [])
        return {"status": "ok", "cutoff": cutoff, "rows": purged}
    except Exception as exc:
        logger.warning("Retention purge failed: %s", exc)
        return {"status": "error", "cutoff": cutoff, "error": str(exc)}
