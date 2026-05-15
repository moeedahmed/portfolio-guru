"""
Supabase dual-write mirror for Portfolio Guru.

The bot's canonical store is still SQLite (credentials.db, profile_store db,
usage.db, chase_log.json). Every write goes there first. THIS module then
mirrors the write to the EM Gurus Hub Supabase project so the web app can
read it without depending on the Mac Mini.

DESIGN PRINCIPLES
-----------------

1. **Best-effort, never raise.** Any failure here is logged and swallowed.
   The bot's user-facing flow must NEVER break because Supabase was slow
   or unreachable.

2. **Lazy / gated.** If SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY env vars
   are not set, every mirror function returns immediately. This keeps the
   bot working offline, in tests, and in setups where Supabase isn't yet
   configured.

3. **Telegram-keyed in, UUID-keyed out.** The bot writes by telegram_user_id;
   Supabase needs emgurus_user_id (the auth.users UUID). The resolver
   `_resolve_emgurus_user_id` looks up portfolio_users and caches the
   answer in-memory.

4. **Skip when unlinked.** If a telegram_user_id has no portfolio_users row
   (i.e. the user hasn't linked their EM Gurus Hub account yet), mirror
   functions silently no-op. The link is established later via the /link
   command (Sprint 3+).

5. **Idempotent upserts** wherever the schema allows them. Usage and chase
   inserts are append-only; everything else is upserted by primary key.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 300  # 5 min — small cache to avoid hammering Supabase
_id_cache: dict[int, tuple[str | None, float]] = {}
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
        logger.warning("Supabase client init failed; mirror disabled: %s", exc)
        _client_init_failed = True
        return None


def _resolve_emgurus_user_id(telegram_user_id: int) -> str | None:
    """Return the auth.users UUID linked to this Telegram user, or None if
    the user hasn't linked yet. Cached for 5 minutes."""
    now = time.monotonic()
    cached = _id_cache.get(telegram_user_id)
    if cached and (now - cached[1]) < _CACHE_TTL_SECONDS:
        return cached[0]

    sb = _supabase()
    if sb is None:
        return None
    try:
        resp = (
            sb.table("portfolio_users")
            .select("emgurus_user_id")
            .eq("telegram_user_id", telegram_user_id)
            .limit(1)
            .execute()
        )
        emgurus_user_id = resp.data[0]["emgurus_user_id"] if resp.data else None
    except Exception as exc:
        logger.debug("portfolio_users lookup failed for %s: %s", telegram_user_id, exc)
        emgurus_user_id = None

    _id_cache[telegram_user_id] = (emgurus_user_id, now)
    return emgurus_user_id


def _ensure_user(telegram_user_id: int, emgurus_user_id: str | None = None) -> str | None:
    """Resolve or create the portfolio_users row. If emgurus_user_id is
    provided (e.g. after a /link completion), upsert with that mapping.
    Otherwise return whatever's currently linked (or None)."""
    if emgurus_user_id is None:
        return _resolve_emgurus_user_id(telegram_user_id)

    sb = _supabase()
    if sb is None:
        return None
    try:
        # Use the security-definer helper so we don't fight RLS on insert.
        sb.rpc("ensure_portfolio_user", {
            "p_emgurus_user_id": emgurus_user_id,
            "p_telegram_user_id": telegram_user_id,
        }).execute()
        # Invalidate cache so subsequent reads see the new mapping.
        _id_cache.pop(telegram_user_id, None)
        return emgurus_user_id
    except Exception as exc:
        logger.warning("ensure_portfolio_user failed for %s -> %s: %s",
                       telegram_user_id, emgurus_user_id, exc)
        return None


# ---------------------------------------------------------------------------
# Mirror functions — one per store path.
# ---------------------------------------------------------------------------

def mirror_credentials(
    telegram_user_id: int,
    encrypted_username: bytes,
    encrypted_password: bytes,
) -> None:
    """Mirror a credential save to portfolio_credentials. Bytes are passed
    through AS-IS — same Fernet key is shared, no re-encryption."""
    sb = _supabase()
    if sb is None:
        return
    uid = _resolve_emgurus_user_id(telegram_user_id)
    if uid is None:
        return
    try:
        sb.table("portfolio_credentials").upsert({
            "emgurus_user_id": uid,
            "encrypted_username": encrypted_username.decode("latin1"),
            "encrypted_password": encrypted_password.decode("latin1"),
        }, on_conflict="emgurus_user_id").execute()
    except Exception as exc:
        logger.warning("mirror_credentials failed for %s: %s", telegram_user_id, exc)


def mirror_profile(
    telegram_user_id: int,
    *,
    training_level: str | None = None,
    curriculum: str | None = None,
    voice_profile_json: str | dict | None = None,
    voice_examples_count: int | None = None,
) -> None:
    """Mirror a partial profile update to portfolio_profile. Only the fields
    passed (non-None) are upserted; existing values for other columns are
    preserved server-side via JSON merge."""
    sb = _supabase()
    if sb is None:
        return
    uid = _resolve_emgurus_user_id(telegram_user_id)
    if uid is None:
        return

    payload: dict[str, Any] = {"emgurus_user_id": uid}
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
            payload["voice_profile_json"] = voice_profile_json
    if voice_examples_count is not None:
        payload["voice_examples_count"] = voice_examples_count

    if len(payload) == 1:
        # Only the user id is set — nothing to upsert.
        return

    try:
        sb.table("portfolio_profile").upsert(
            payload, on_conflict="emgurus_user_id"
        ).execute()
    except Exception as exc:
        logger.warning("mirror_profile failed for %s: %s", telegram_user_id, exc)


def mirror_usage(telegram_user_id: int, form_type: str) -> None:
    """Mirror a single case-filed event to portfolio_usage. Append-only."""
    sb = _supabase()
    if sb is None:
        return
    uid = _resolve_emgurus_user_id(telegram_user_id)
    if uid is None:
        return
    try:
        sb.table("portfolio_usage").insert({
            "emgurus_user_id": uid,
            "form_type": form_type,
        }).execute()
    except Exception as exc:
        logger.warning("mirror_usage failed for %s: %s", telegram_user_id, exc)


def mirror_tier(
    telegram_user_id: int,
    tier: str,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
) -> None:
    """Mirror a tier change (Stripe webhook or /settier) to portfolio_users."""
    sb = _supabase()
    if sb is None:
        return
    uid = _resolve_emgurus_user_id(telegram_user_id)
    if uid is None:
        return
    payload: dict[str, Any] = {"emgurus_user_id": uid, "tier": tier}
    if stripe_customer_id is not None:
        payload["stripe_customer_id"] = stripe_customer_id
    if stripe_subscription_id is not None:
        payload["stripe_subscription_id"] = stripe_subscription_id
    try:
        sb.table("portfolio_users").upsert(
            payload, on_conflict="emgurus_user_id"
        ).execute()
    except Exception as exc:
        logger.warning("mirror_tier failed for %s: %s", telegram_user_id, exc)


def mirror_chase(
    telegram_user_id: int,
    assessor_email: str,
    assessor_name: str,
    chase_date: str,
    method: str = "manual",
    ticket_summary: str = "",
    chase_number: int = 1,
) -> None:
    """Mirror an assessor chase log entry to portfolio_chase_log."""
    sb = _supabase()
    if sb is None:
        return
    uid = _resolve_emgurus_user_id(telegram_user_id)
    if uid is None:
        return
    try:
        sb.table("portfolio_chase_log").insert({
            "emgurus_user_id": uid,
            "assessor_email": assessor_email,
            "assessor_name": assessor_name,
            "chase_date": chase_date,
            "method": method,
            "ticket_summary": ticket_summary,
            "chase_number": chase_number,
        }).execute()
    except Exception as exc:
        logger.warning("mirror_chase failed for %s: %s", telegram_user_id, exc)


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
    """Mirror a filed case (success / partial / failed) to portfolio_cases.

    This is the first time the bot durably persists case content — case_text
    is encrypted with the same Fernet key the credentials use, never stored
    plaintext. extracted_fields is the FormDraft.fields dict the bot
    produced.
    """
    sb = _supabase()
    if sb is None:
        return
    uid = _resolve_emgurus_user_id(telegram_user_id)
    if uid is None:
        return
    payload: dict[str, Any] = {
        "emgurus_user_id": uid,
        "form_type": form_type,
        "status": status,
        "source": source,
        "extracted_fields": extracted_fields or {},
        "curriculum_links": curriculum_links or [],
        "key_capabilities": key_capabilities or [],
    }
    if kaizen_event_id:
        payload["kaizen_event_id"] = kaizen_event_id
    if case_text_encrypted:
        payload["case_text_encrypted"] = case_text_encrypted.decode("latin1")
    try:
        sb.table("portfolio_cases").insert(payload).execute()
    except Exception as exc:
        logger.warning("mirror_case failed for %s: %s", telegram_user_id, exc)


# ---------------------------------------------------------------------------
# Diagnostics — handy for /admin commands later.
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """True when the Supabase mirror is configured and reachable."""
    return _supabase() is not None


def link_status(telegram_user_id: int) -> dict:
    """Return a small dict describing the mirror status for a user. Useful
    in /status or admin diagnostics."""
    sb = _supabase()
    info: dict[str, Any] = {
        "mirror_enabled": sb is not None,
        "emgurus_user_id": None,
    }
    if sb is None:
        return info
    uid = _resolve_emgurus_user_id(telegram_user_id)
    info["emgurus_user_id"] = uid
    return info
