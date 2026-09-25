"""Time-based retention for clinical case content (launch checklist 1.5).

This module's original docstring claimed the Supabase mirror was the only
durable store of clinical content. A live audit on 2026-08-24 found that was
false on three counts, all now fixed elsewhere:

- ``drafts/`` held plaintext case narrative that survived /reset entirely
  (now encrypted, erased on save, TTL'd — see ``draft_backup.py``);
- ``bot_persistence`` retained case text and drafts for 20 users indefinitely
  (now scrubbed before it reaches disk — see ``clinical_persistence.py``);
- ``dogfood-audit.ndjson`` held 44MB of readable narrative
  (now restricted to operator and synthetic traffic).

What remains true, and is what this purge covers: the Supabase mirror no longer
carries clinical content at all (``supabase_sync.mirror_case`` discards it), so
this purge is a backstop for rows written before that change. Attachment and
voice temp files are still unlinked inline in bot.py, and usage.db rows are RCEM
taxonomy plus timestamps with no patient detail.

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
