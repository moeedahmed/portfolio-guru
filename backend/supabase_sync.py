"""Best-effort Supabase mirror for Portfolio Guru's dedicated London project.

Project wozigfujdifakfqlaurm (eu-west-2) uses pg_* tables keyed directly on
telegram_user_id. SQLite remains primary and on the hot path: local writes
happen first, and mirror failures are logged and swallowed, never raised.
Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY disables the mirror.
No Hub link, UUID resolution or backfill is required. Clinical content and
third-party assessor details are never mirrored.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_client = None
_client_init_failed = False


def _supabase() -> Any | None:
    """Return a cached Supabase client, or None if not configured. Safe to
    call repeatedly — caches both success and failure."""
    global _client, _client_init_failed
    if _client is not None:
        return _client
    if _client_init_failed:
        return None

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        # Not configured — bot runs SQLite-only.
        _client_init_failed = True
        return None

    try:
        from supabase import create_client
        _client = create_client(url, key)
        return _client
    except Exception as exc:
        logger.warning("Supabase client init failed; mirror disabled: %s", type(exc).__name__)
        _client_init_failed = True
        return None


# ---------------------------------------------------------------------------
# Mirror functions — one per store path.
# ---------------------------------------------------------------------------

def _ciphertext_text(value: bytes) -> str:
    """Fernet tokens are ASCII; retain legacy non-ASCII byte values losslessly."""
    try:
        return value.decode("ascii")
    except UnicodeDecodeError:
        return value.decode("latin1")


def mirror_credentials(
    telegram_user_id: int,
    encrypted_username: bytes,
    encrypted_password: bytes,
) -> None:
    """Mirror Fernet ciphertext as text, without decrypting or re-encrypting."""
    sb = _supabase()
    if sb is None:
        return
    try:
        sb.table("pg_credentials").upsert({
            "telegram_user_id": telegram_user_id,
            "kaizen_username_enc": _ciphertext_text(encrypted_username),
            "kaizen_password_enc": _ciphertext_text(encrypted_password),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="telegram_user_id").execute()
    except Exception as exc:
        logger.warning("mirror_credentials failed for %s: %s", telegram_user_id, type(exc).__name__)


def delete_mirrored_credentials(telegram_user_id: int) -> None:
    """Remove the mirrored Kaizen login only (user chose passwordless)."""
    sb = _supabase()
    if sb is None:
        return
    try:
        sb.table("pg_credentials").delete().eq("telegram_user_id", telegram_user_id).execute()
    except Exception as exc:
        logger.warning("delete_mirrored_credentials failed for %s: %s", telegram_user_id, type(exc).__name__)


def mirror_profile(
    telegram_user_id: int,
    *,
    training_level: str | None = None,
    curriculum: str | None = None,
    voice_profile_json: str | dict | None = None,
    voice_examples_count: int | None = None,
) -> None:
    """Mirror a partial profile update to pg_profile. Only the fields
    passed (non-None) are upserted; existing values for other columns are
    left out of the update."""
    sb = _supabase()
    if sb is None:
        return

    payload: dict[str, Any] = {"telegram_user_id": telegram_user_id}
    if training_level is not None:
        payload["training_level"] = training_level
    if curriculum is not None:
        payload["curriculum"] = curriculum
    if voice_profile_json is not None:
        if isinstance(voice_profile_json, str):
            try:
                voice_profile_json = json.loads(voice_profile_json)
            except (TypeError, ValueError):
                voice_profile_json = None
        if voice_profile_json is not None:
            payload["voice_profile"] = voice_profile_json
    if voice_examples_count is not None:
        payload["voice_examples_count"] = voice_examples_count

    if len(payload) == 1:
        # Only the user id is set — nothing to upsert.
        return

    try:
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        sb.table("pg_profile").upsert(
            payload, on_conflict="telegram_user_id"
        ).execute()
    except Exception as exc:
        logger.warning("mirror_profile failed for %s: %s", telegram_user_id, type(exc).__name__)


def mirror_usage(telegram_user_id: int, form_type: str) -> None:
    """Mirror a single case-filed event to pg_usage. Append-only."""
    sb = _supabase()
    if sb is None:
        return
    try:
        sb.table("pg_usage").insert({
            "telegram_user_id": telegram_user_id,
            "form_type": form_type,
        }).execute()
    except Exception as exc:
        logger.warning("mirror_usage failed for %s: %s", telegram_user_id, type(exc).__name__)


def mirror_tier(
    telegram_user_id: int,
    tier: str,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
) -> None:
    """Mirror a tier change (Stripe webhook or /settier) to pg_users."""
    sb = _supabase()
    if sb is None:
        return
    payload: dict[str, Any] = {"telegram_user_id": telegram_user_id, "tier": tier}
    if stripe_customer_id is not None:
        payload["stripe_customer_id"] = stripe_customer_id
    if stripe_subscription_id is not None:
        payload["stripe_subscription_id"] = stripe_subscription_id
    try:
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        sb.table("pg_users").upsert(
            payload, on_conflict="telegram_user_id"
        ).execute()
    except Exception as exc:
        logger.warning("mirror_tier failed for %s: %s", telegram_user_id, type(exc).__name__)


# --- Beta request helpers ---


def store_beta_request(user_id: int, username: str | None) -> None:
    """Record a beta access request from a user."""
    sb = _supabase()
    if sb is None:
        return
    try:
        sb.table("pg_beta_requests").insert({
            "telegram_user_id": user_id,
            "username": username or "",
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as exc:
        logger.warning("store_beta_request failed for %s: %s", user_id, type(exc).__name__)


def get_beta_request_by_username(username: str) -> dict | None:
    """Find a pending beta request by Telegram @username."""
    sb = _supabase()
    if sb is None:
        return None
    clean = username.lstrip("@")
    try:
        result = (
            sb.table("pg_beta_requests")
            .select("*")
            .eq("username", clean)
            .eq("status", "pending")
            .execute()
        )
        rows = result.data
        return {**rows[0], "user_id": rows[0]["telegram_user_id"]} if rows else None
    except Exception as exc:
        logger.warning("get_beta_request_by_username failed for %s: %s", username, type(exc).__name__)
        return None


def approve_beta_request(user_id: int, tier: str = "pro") -> bool:
    """Mark pending requests approved; the caller owns the local tier change.

    ``tier`` is retained for caller compatibility. Returns whether the update
    completed without error.
    """
    sb = _supabase()
    if sb is None:
        return False
    try:
        sb.table("pg_beta_requests").update({
            "status": "approved",
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }).eq("telegram_user_id", user_id).eq("status", "pending").execute()
        return True
    except Exception as exc:
        logger.warning("approve_beta_request failed for %s: %s", user_id, type(exc).__name__)
        return False



def mirror_chase(
    telegram_user_id: int,
    assessor_email: str,
    assessor_name: str,
    chase_date: str,
    method: str = "manual",
    ticket_summary: str = "",
    chase_number: int = 1,
) -> None:
    """Keep assessor names/emails in the local chase log only."""
    logger.debug("Chase mirroring disabled: London holds no assessor data")


def mirror_case(
    telegram_user_id: int,
    form_type: str,
    status: str,
    *,
    kaizen_event_id: str | None = None,
    case_text_encrypted: bytes | None = None,
    extracted_fields: dict | None = None,
    curriculum_links: list | None = None,
    key_capabilities: list | None = None,
    source: str = "bot",
) -> None:
    """Mirror the FACT of a filed case — never its content.

    Portfolio Guru no longer keeps clinical narrative once Kaizen has confirmed
    the save (docs/data-architecture-plan-2026-08-24.md, decision 2). Kaizen
    holds the evidence; a second encrypted copy in a mirror bought nothing and
    made every downstream store an Art. 9 store.

    ``case_text_encrypted`` and ``extracted_fields`` are still accepted so the
    call sites don't have to change, and are deliberately DISCARDED here. The
    row keeps form type, status, Kaizen event id and RCEM taxonomy references,
    which is everything /health, KC coverage and ARCP projection actually read.
    """
    sb = _supabase()
    if sb is None:
        return
    payload: dict[str, Any] = {
        "telegram_user_id": telegram_user_id,
        "form_type": form_type,
        "status": status,
        "source": source,
        "curriculum_links": curriculum_links or [],
        "key_capabilities": key_capabilities or [],
    }
    if kaizen_event_id:
        payload["kaizen_event_id"] = kaizen_event_id
    try:
        sb.table("pg_filings").insert(payload).execute()
    except Exception as exc:
        logger.warning("mirror_case failed for %s: %s", telegram_user_id, type(exc).__name__)


# ---------------------------------------------------------------------------
# Erasure — GDPR Art. 17 / right to be forgotten.
# ---------------------------------------------------------------------------

def delete_user_data(telegram_user_id: int, *, include_billing_link: bool = False) -> dict:
    """Erase this user's mirrored data from Supabase (supports GDPR Art. 17).

    Deletes credentials, filing metadata, training profile, usage, curriculum
    coverage and beta requests by telegram_user_id. Consent records are never
    deleted. By default pg_users (tier and Stripe IDs) is retained to avoid
    orphaning an active subscription; include_billing_link opts into erasing it.

    Best-effort and never raises — mirrors the module's design principle. The
    service-role key bypasses RLS, so the deletes apply. Returns a
    ``{table: "deleted"|"error: ..."}`` map for logging/audit.
    """
    result: dict[str, Any] = {}
    sb = _supabase()
    if sb is None:
        result["_skipped"] = "supabase not configured"
        return result

    tables = [
        "pg_credentials",
        "pg_filings",
        "pg_profile",
        "pg_usage",
        "pg_kc_coverage",
        "pg_beta_requests",
    ]
    if include_billing_link:
        tables.append("pg_users")

    for table in tables:
        try:
            sb.table(table).delete().eq("telegram_user_id", telegram_user_id).execute()
            result[table] = "deleted"
        except Exception as exc:
            logger.warning(
                "delete_user_data: %s purge failed for %s: %s", table, telegram_user_id, type(exc).__name__
            )
            result[table] = f"error: {type(exc).__name__}"

    logger.info("delete_user_data for %s: %s", telegram_user_id, result)
    return result


# ---------------------------------------------------------------------------
# Diagnostics — handy for /admin commands later.
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """True when a configured Supabase client is available (no health probe)."""
    return _supabase() is not None
