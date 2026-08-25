"""Durable mirror of Portfolio Guru's account layer, keyed on the Telegram id.

WHAT CHANGED AND WHY (2026-08-25)
---------------------------------
This module used to write to the EM Gurus Hub project and resolve every call
through ``emgurus_user_id`` — a UUID the doctor only obtained by completing a
``/link`` on emgurus.com. Exactly one person had done that. So every mirror
function returned early for every beta doctor, silently, and the "cloud mirror"
in the ROPA had never held a single user's data.

It now writes to Portfolio Guru's own project in London (eu-west-2), keyed on
``telegram_user_id`` directly. No link, no resolver, nothing to no-op against.

DESIGN
------

1. **Best-effort, never raise.** A Supabase failure must never break a doctor
   mid-case. Everything here logs and swallows.

2. **SQLite stays on the hot path.** Reads are local, so the bot works offline
   and never blocks a filing on the network. This is the durable copy, not the
   query path.

3. **Gated on config.** No ``SUPABASE_URL`` / ``SUPABASE_SERVICE_ROLE_KEY`` and
   every function returns immediately.

4. **No clinical content, ever.** ``mirror_case`` records the fact of a filing.
   The case narrative is not sent, not encrypted-and-sent, not summarised. See
   docs/data-architecture-plan-2026-08-24.md, decision 2.
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
    """Cached Supabase client, or None when not configured. Caches failure too,
    so an unconfigured bot doesn't retry on every write."""
    global _client, _client_init_failed
    if _client is not None:
        return _client
    if _client_init_failed:
        return None

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        _client_init_failed = True
        return None

    try:
        from supabase import create_client
        _client = create_client(url, key)
        return _client
    except Exception as exc:
        logger.warning("Supabase client init failed; mirror disabled: %s", exc)
        _client_init_failed = True
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert(table: str, payload: dict[str, Any], *, on_conflict: str) -> None:
    sb = _supabase()
    if sb is None:
        return
    try:
        sb.table(table).upsert(payload, on_conflict=on_conflict).execute()
    except Exception as exc:
        logger.warning("%s upsert failed: %s", table, exc)


def _insert(table: str, payload: dict[str, Any]) -> None:
    sb = _supabase()
    if sb is None:
        return
    try:
        sb.table(table).insert(payload).execute()
    except Exception as exc:
        logger.warning("%s insert failed: %s", table, exc)


def ensure_user(telegram_user_id: int) -> None:
    """Make sure an anchor row exists before writing satellite rows."""
    _upsert(
        "pg_users",
        {"telegram_user_id": telegram_user_id, "updated_at": _now()},
        on_conflict="telegram_user_id",
    )


# ---------------------------------------------------------------------------
# Mirror functions — one per local store path.
# ---------------------------------------------------------------------------

def mirror_credentials(
    telegram_user_id: int,
    encrypted_username: bytes,
    encrypted_password: bytes,
) -> None:
    """Mirror Fernet ciphertext as-is. The key lives in BWS and is never stored
    alongside what it protects, so this column is opaque to Supabase."""
    ensure_user(telegram_user_id)
    _upsert(
        "pg_credentials",
        {
            "telegram_user_id": telegram_user_id,
            "kaizen_username_enc": encrypted_username.decode("latin1"),
            "kaizen_password_enc": encrypted_password.decode("latin1"),
            "updated_at": _now(),
        },
        on_conflict="telegram_user_id",
    )


def mirror_profile(
    telegram_user_id: int,
    *,
    training_level: str | None = None,
    curriculum: str | None = None,
    kaizen_role: str | None = None,
    voice_profile_json: str | dict | None = None,
    voice_examples_count: int | None = None,
) -> None:
    """Upsert the fields actually supplied; leave the rest untouched."""
    payload: dict[str, Any] = {"telegram_user_id": telegram_user_id, "updated_at": _now()}
    if training_level is not None:
        payload["training_level"] = training_level
    if curriculum is not None:
        payload["curriculum"] = curriculum
    if kaizen_role is not None:
        payload["kaizen_role"] = kaizen_role
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

    if len(payload) == 2:  # id + updated_at only
        return
    ensure_user(telegram_user_id)
    _upsert("pg_profile", payload, on_conflict="telegram_user_id")


def mirror_usage(telegram_user_id: int, form_type: str, status: str = "filed") -> None:
    ensure_user(telegram_user_id)
    _insert("pg_usage", {
        "telegram_user_id": telegram_user_id,
        "form_type": form_type,
        "status": status,
    })


def mirror_kc_coverage(telegram_user_id: int, form_type: str, kcs_selected: list) -> None:
    ensure_user(telegram_user_id)
    _insert("pg_kc_coverage", {
        "telegram_user_id": telegram_user_id,
        "form_type": form_type,
        "kcs_selected": kcs_selected or [],
    })


def mirror_tier(
    telegram_user_id: int,
    tier: str,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    is_beta: bool | None = None,
) -> None:
    payload: dict[str, Any] = {
        "telegram_user_id": telegram_user_id,
        "tier": tier,
        "updated_at": _now(),
    }
    if stripe_customer_id is not None:
        payload["stripe_customer_id"] = stripe_customer_id
    if stripe_subscription_id is not None:
        payload["stripe_subscription_id"] = stripe_subscription_id
    if is_beta is not None:
        payload["is_beta"] = is_beta
    _upsert("pg_users", payload, on_conflict="telegram_user_id")


def mirror_consent(
    telegram_user_id: int,
    *,
    consent_version: str,
    consent_text_hash: str,
    action: str,
    channel: str = "telegram",
    lawful_basis: str = "art9_2a_explicit_consent",
) -> None:
    """Mirror a consent grant or withdrawal.

    Consent records were the one legally load-bearing store with no durable copy
    at all — they existed only in SQLite on a single unencrypted disk. They are
    the evidence of the lawful basis for every past act of processing, so losing
    that disk meant losing the ability to demonstrate compliance. Append-only,
    here as locally: a withdrawal adds a row, it never overwrites the grant.
    """
    ensure_user(telegram_user_id)
    _insert("pg_consent_records", {
        "telegram_user_id": telegram_user_id,
        "consent_version": consent_version,
        "consent_text_hash": consent_text_hash,
        "action": action,
        "channel": channel,
        "lawful_basis": lawful_basis,
    })


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
    """Mirror the FACT of a filing — never its content.

    ``case_text_encrypted`` and ``extracted_fields`` are still accepted so call
    sites need not change, and are deliberately DISCARDED. Kaizen holds the
    evidence; a second copy here would make this an Art. 9 store for no gain.
    """
    ensure_user(telegram_user_id)
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
    _insert("pg_filings", payload)


def mirror_chase(
    telegram_user_id: int,
    assessor_email: str,
    assessor_name: str,
    chase_date: str,
    method: str = "manual",
    ticket_summary: str = "",
    chase_number: int = 1,
) -> None:
    """Mirror an assessor chase. Note this row holds a THIRD party's name and
    email — someone who never used the product — so it has its own ROPA line."""
    ensure_user(telegram_user_id)
    _insert("pg_chase_log", {
        "telegram_user_id": telegram_user_id,
        "assessor_email": assessor_email,
        "assessor_name": assessor_name,
        "chase_date": chase_date,
        "method": method,
        "ticket_summary": ticket_summary,
        "chase_number": chase_number,
    })


# --- Beta access requests ---------------------------------------------------


def store_beta_request(user_id: int, username: str | None) -> None:
    _insert("pg_beta_requests", {
        "telegram_user_id": user_id,
        "username": (username or "").lstrip("@"),
        "tier_requested": "beta",
        "status": "pending",
    })


def get_beta_request_by_username(username: str) -> dict | None:
    sb = _supabase()
    if sb is None:
        return None
    try:
        result = (
            sb.table("pg_beta_requests")
            .select("*")
            .eq("username", username.lstrip("@"))
            .eq("status", "pending")
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as exc:
        logger.warning("get_beta_request_by_username failed for %s: %s", username, exc)
        return None


def approve_beta_request(user_id: int, tier: str = "pro") -> bool:
    sb = _supabase()
    if sb is None:
        return False
    try:
        sb.table("pg_beta_requests").update({
            "status": "approved",
            "approved_at": _now(),
        }).eq("telegram_user_id", user_id).eq("status", "pending").execute()
        return True
    except Exception as exc:
        logger.warning("approve_beta_request failed for %s: %s", user_id, exc)
        return False


# ---------------------------------------------------------------------------
# Erasure — GDPR Art. 17.
# ---------------------------------------------------------------------------

# Consent records are deliberately absent: they are the evidence of the lawful
# basis for processing that already happened, and deleting them would destroy
# the ability to demonstrate compliance. A withdrawal is recorded as a new row.
ERASABLE_TABLES = (
    "pg_credentials",
    "pg_filings",
    "pg_profile",
    "pg_usage",
    "pg_kc_coverage",
    "pg_chase_log",
    "pg_beta_requests",
)


def delete_user_data(telegram_user_id: int, *, include_billing_link: bool = False) -> dict:
    """Erase this user's mirrored data. Best-effort; never raises.

    ``pg_users`` (identity, tier, Stripe ids) is KEPT by default so a /reset
    doesn't orphan an active subscription — the billing relationship has its own
    retention basis. Pass ``include_billing_link=True`` for a full erasure.
    """
    result: dict[str, Any] = {}
    sb = _supabase()
    if sb is None:
        return {"_skipped": "supabase not configured"}

    tables = list(ERASABLE_TABLES)
    if include_billing_link:
        tables.append("pg_users")

    for table in tables:
        try:
            sb.table(table).delete().eq("telegram_user_id", telegram_user_id).execute()
            result[table] = "deleted"
        except Exception as exc:
            logger.warning("delete_user_data: %s purge failed for %s: %s",
                           table, telegram_user_id, exc)
            result[table] = f"error: {exc}"

    logger.info("delete_user_data for %s: %s", telegram_user_id, result)
    return result


# ---------------------------------------------------------------------------
# Diagnostics.
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    return _supabase() is not None


def link_status(telegram_user_id: int) -> dict:
    """Mirror status for a user. There is no account link any more — the
    Telegram id IS the key — so this reports configuration only."""
    return {"mirror_enabled": _supabase() is not None, "telegram_user_id": telegram_user_id}


def consume_link_token(token: str, telegram_user_id: int) -> tuple[bool, str]:
    """Retired. /link existed to bind a Telegram user to an EM Gurus Hub UUID
    because the mirror was keyed on it. It no longer is.

    The command stays registered and answers plainly rather than erroring,
    because doctors may still be holding old instructions telling them to link.
    """
    return (
        False,
        "You don't need to link anything any more — Portfolio Guru already "
        "recognises you here. Just send a case whenever you're ready.",
    )


__all__ = [
    "ERASABLE_TABLES",
    "approve_beta_request",
    "consume_link_token",
    "delete_user_data",
    "ensure_user",
    "get_beta_request_by_username",
    "is_enabled",
    "link_status",
    "mirror_case",
    "mirror_chase",
    "mirror_consent",
    "mirror_credentials",
    "mirror_kc_coverage",
    "mirror_profile",
    "mirror_tier",
    "mirror_usage",
    "store_beta_request",
]
