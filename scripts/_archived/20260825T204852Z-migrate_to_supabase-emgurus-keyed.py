"""
One-off migration: bot's SQLite + JSON data to Supabase portfolio_* tables.

This is the Sprint 1 backfill from docs/WEB_APP_SPEC.md. It reads the bot's
on-device data (credentials, profile, usage, chase log) and inserts it into
the shared emgurus-hub Supabase project so the web app can read it.

USAGE
-----

    # Dry-run for the admin user — shows what would be written, no DB changes.
    python scripts/migrate_to_supabase.py \\
      --telegram-user-id 6912896590 \\
      --emgurus-user-id  820b6af9-c9df-4a7a-bd57-d9bfdd5019ca \\
      --dry-run

    # For real — same args without --dry-run.
    python scripts/migrate_to_supabase.py \\
      --telegram-user-id 6912896590 \\
      --emgurus-user-id  820b6af9-c9df-4a7a-bd57-d9bfdd5019ca

ENV VARS REQUIRED
-----------------

    SUPABASE_URL                 e.g. https://xxx.supabase.co
    SUPABASE_SERVICE_ROLE_KEY    service-role key from Supabase project settings
    FERNET_SECRET_KEY            same key the bot uses (already loaded by bot venv)

WHAT IT DOES
------------

For one (telegram_user_id, emgurus_user_id) pair:

1. Reads the bot's SQLite databases:
   - ~/.openclaw/data/portfolio-guru/portfolio_guru.db (UserCredential, UserProfile)
   - ~/.openclaw/data/portfolio-guru/usage.db (portfolio_usage)
2. Reads backend/chase_log.json if present.
3. Writes to Supabase:
   - public.portfolio_users (links the IDs)
   - public.portfolio_credentials (encrypted_username, encrypted_password —
     Fernet ciphertext copied AS-IS; no re-encryption)
   - public.portfolio_profile (training_level, curriculum,
     voice_profile_json, voice_examples_count)
   - public.portfolio_usage (per-case usage records — fresh insert)
   - public.portfolio_chase_log (per-chase records)

WHAT IT DOES NOT DO
-------------------

- Migrate filed cases — the bot doesn't store these durably anywhere yet
  (they're ephemeral per session). Case history starts accumulating in
  Supabase once the bot's dual-write goes live (Sprint 2).
- Touch other users — by design, one user at a time so we can verify
  parity before going broader.
- Re-encrypt anything — credential bytes are copied AS-IS; the same Fernet
  key must be available to Supabase Edge Functions for decryption later.

VERIFY AFTER RUNNING
--------------------

In the Supabase dashboard, confirm rows in portfolio_users,
portfolio_credentials, portfolio_profile, portfolio_usage, and
portfolio_chase_log. For credentials, run a Fernet round-trip
separately against the bot's existing decrypt path to confirm parity.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure we can import bot modules
HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv
load_dotenv(BACKEND / ".env")

try:
    from supabase import create_client, Client
except ImportError:
    print("ERROR: supabase-py is not installed.")
    print("Run: pip install supabase")
    sys.exit(1)

from sqlmodel import Session, select
from credentials import engine as cred_engine, UserCredential
from profile_store import engine as prof_engine, UserProfile


CHASE_LOG_PATH = BACKEND / "chase_log.json"
USAGE_DB_PATH = os.path.expanduser("~/.openclaw/data/portfolio-guru/usage.db")


def _supabase_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
        sys.exit(1)
    return create_client(url, key)


def _read_credentials(telegram_user_id: int) -> dict | None:
    with Session(cred_engine) as session:
        row = session.exec(
            select(UserCredential).where(UserCredential.telegram_user_id == telegram_user_id)
        ).first()
        if not row:
            return None
        return {
            "encrypted_username": bytes(row.kaizen_username_enc),
            "encrypted_password": bytes(row.kaizen_password_enc),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


def _read_profile(telegram_user_id: int) -> dict | None:
    with Session(prof_engine) as session:
        row = session.exec(
            select(UserProfile).where(UserProfile.telegram_user_id == telegram_user_id)
        ).first()
        if not row:
            return None
        return {
            "training_level": row.training_level,
            "curriculum": row.curriculum or "2025",
            "voice_profile_json": json.loads(row.voice_profile) if row.voice_profile else None,
            "voice_examples_count": row.voice_examples_count or 0,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


def _read_usage_records(telegram_user_id: int) -> list[dict]:
    if not os.path.exists(USAGE_DB_PATH):
        return []
    import sqlite3
    rows = []
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT form_type, filed_at FROM portfolio_usage WHERE telegram_user_id = ? ORDER BY filed_at",
            (telegram_user_id,),
        )
        for form_type, filed_at in cursor.fetchall():
            rows.append({"form_type": form_type, "filed_at": filed_at})
    return rows


def _read_chase_log(telegram_user_id: int | None = None) -> list[dict]:
    """The chase log is a single shared file (not partitioned per user). We
    copy all rows for the admin migration; future per-user migrations will
    need a way to filter — for now this is fine since only the admin
    generates chase entries on a private-beta bot."""
    if not CHASE_LOG_PATH.exists():
        return []
    with open(CHASE_LOG_PATH) as f:
        data = json.load(f)
    chases = data.get("chases", [])
    rows = []
    for c in chases:
        rows.append({
            "assessor_email": c.get("assessor_email", ""),
            "assessor_name": c.get("assessor_name", ""),
            "chase_date": c.get("date"),
            "method": c.get("method", "manual"),
            "ticket_summary": c.get("tickets", ""),
            "chase_number": c.get("chase_number", 1),
        })
    return rows


def _read_user_profile(telegram_user_id: int) -> dict:
    """Read the user's tier + Stripe IDs from the bot's user_profiles table
    in usage.db. Returns defaults when no row exists."""
    defaults = {"tier": "free", "stripe_customer_id": None, "stripe_subscription_id": None}
    if not os.path.exists(USAGE_DB_PATH):
        return defaults
    import sqlite3
    with sqlite3.connect(USAGE_DB_PATH) as conn:
        try:
            cursor = conn.execute(
                "SELECT tier, stripe_customer_id, stripe_subscription_id "
                "FROM user_profiles WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return defaults
            return {
                "tier": row[0] or "free",
                "stripe_customer_id": row[1],
                "stripe_subscription_id": row[2],
            }
        except Exception:
            return defaults


def run(telegram_user_id: int, emgurus_user_id: str, dry_run: bool) -> None:
    print(f"\n=== Portfolio Guru migration ===")
    print(f"telegram_user_id : {telegram_user_id}")
    print(f"emgurus_user_id  : {emgurus_user_id}")
    print(f"dry_run          : {dry_run}\n")

    credentials = _read_credentials(telegram_user_id)
    profile = _read_profile(telegram_user_id)
    usage_records = _read_usage_records(telegram_user_id)
    chase_records = _read_chase_log(telegram_user_id)
    user_profile = _read_user_profile(telegram_user_id)

    print(f"Credentials      : {'present' if credentials else 'NOT FOUND'}")
    print(f"Profile          : {'present' if profile else 'NOT FOUND'}")
    print(f"Usage records    : {len(usage_records)}")
    print(f"Chase records    : {len(chase_records)}")
    print(f"Tier             : {user_profile['tier']}")
    if user_profile["stripe_customer_id"]:
        print(f"Stripe customer  : {user_profile['stripe_customer_id']}")
    print()

    if dry_run:
        if profile:
            print(f"Profile preview  : training_level={profile['training_level']}, "
                  f"curriculum={profile['curriculum']}, "
                  f"voice_examples={profile['voice_examples_count']}")
        if usage_records[:3]:
            print(f"First 3 usage    : {usage_records[:3]}")
        if chase_records[:3]:
            print(f"First 3 chase    : {chase_records[:3]}")
        print("\n[DRY RUN] no rows written. Re-run without --dry-run to apply.")
        return

    sb = _supabase_client()

    print("→ Upserting portfolio_users…")
    sb.table("portfolio_users").upsert({
        "emgurus_user_id": emgurus_user_id,
        "telegram_user_id": telegram_user_id,
        "linked_at": datetime.utcnow().isoformat(),
        "tier": user_profile["tier"],
        "stripe_customer_id": user_profile["stripe_customer_id"],
        "stripe_subscription_id": user_profile["stripe_subscription_id"],
    }, on_conflict="emgurus_user_id").execute()

    if credentials:
        print("→ Upserting portfolio_credentials…")
        sb.table("portfolio_credentials").upsert({
            "emgurus_user_id": emgurus_user_id,
            "encrypted_username": credentials["encrypted_username"].decode("latin1"),
            "encrypted_password": credentials["encrypted_password"].decode("latin1"),
        }, on_conflict="emgurus_user_id").execute()

    if profile:
        print("→ Upserting portfolio_profile…")
        sb.table("portfolio_profile").upsert({
            "emgurus_user_id": emgurus_user_id,
            "training_level": profile["training_level"],
            "curriculum": profile["curriculum"],
            "voice_profile_json": profile["voice_profile_json"],
            "voice_examples_count": profile["voice_examples_count"],
        }, on_conflict="emgurus_user_id").execute()

    if usage_records:
        print(f"→ Inserting {len(usage_records)} portfolio_usage rows…")
        rows = [{"emgurus_user_id": emgurus_user_id, **r} for r in usage_records]
        for i in range(0, len(rows), 100):
            sb.table("portfolio_usage").insert(rows[i:i + 100]).execute()

    if chase_records:
        print(f"→ Inserting {len(chase_records)} portfolio_chase_log rows…")
        rows = [{"emgurus_user_id": emgurus_user_id, **r} for r in chase_records]
        for i in range(0, len(rows), 100):
            sb.table("portfolio_chase_log").insert(rows[i:i + 100]).execute()

    print("\n✓ Migration complete. Verify in Supabase dashboard:")
    print("  - portfolio_users         (one row, tier correct)")
    print("  - portfolio_credentials   (encrypted bytes set)")
    print("  - portfolio_profile       (training_level, curriculum, voice profile)")
    print("  - portfolio_usage         (case count matches SQLite)")
    print("  - portfolio_chase_log     (chase entries match chase_log.json)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--telegram-user-id", type=int, required=True,
                        help="Telegram user_id (the bot's internal key)")
    parser.add_argument("--emgurus-user-id", type=str, required=True,
                        help="auth.users.id (uuid) of the corresponding hub user")
    parser.add_argument("--dry-run", action="store_true",
                        help="Read SQLite + JSON and print summary, but do not write to Supabase")
    args = parser.parse_args()

    try:
        import uuid
        uuid.UUID(args.emgurus_user_id)
    except ValueError:
        print(f"ERROR: --emgurus-user-id must be a valid uuid, got {args.emgurus_user_id!r}")
        sys.exit(1)

    run(args.telegram_user_id, args.emgurus_user_id, args.dry_run)


if __name__ == "__main__":
    main()
