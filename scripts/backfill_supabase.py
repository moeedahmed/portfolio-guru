#!/usr/bin/env python3
"""Backfill the local account layer into the London Supabase project.

One-off migration for decision 1 of docs/data-architecture-plan-2026-08-24.md:
SQLite stops being the only copy of anyone's data. Reads the two local
databases and writes them to the pg_* tables, keyed on telegram_user_id.

WHAT MOVES
    portfolio_guru.db  usercredential  -> pg_credentials   (Fernet blobs, as-is)
                       userprofile     -> pg_profile
    usage.db           user_profiles   -> pg_users         (tier + Stripe ids)
                       portfolio_usage -> pg_usage
                       kc_coverage     -> pg_kc_coverage
                       consent_records -> pg_consent_records

WHAT DOES NOT MOVE
    Clinical content of any kind. There is none left locally to move, and the
    target schema has nowhere to put it.

USAGE
    # Show exactly what would be written. Default, no writes.
    scripts/backfill_supabase.py

    # Write it.
    scripts/backfill_supabase.py --apply

REQUIRES
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  (the Portfolio Guru project)

IDEMPOTENCY
    Per-user tables upsert, so re-running is safe. The append-only tables
    (usage, KC coverage, consent) would DOUBLE on a second run, and duplicated
    usage rows would corrupt free-tier metering — so this refuses to touch a
    table that already holds rows for a user unless --force says otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

DATA_DIR = Path(os.environ.get(
    "PORTFOLIO_GURU_DATA_DIR",
    Path.home() / ".openclaw" / "data" / "portfolio-guru",
))
CORE_DB = DATA_DIR / "portfolio_guru.db"
USAGE_DB = DATA_DIR / "usage.db"

APPEND_ONLY = {
    "pg_usage": "portfolio_usage",
    "pg_kc_coverage": "kc_coverage",
    "pg_consent_records": "consent_records",
}


def _rows(db: Path, query: str) -> list[dict]:
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(query)]
    finally:
        conn.close()


def collect() -> dict[str, list[dict]]:
    """Read everything out of SQLite, shaped for the target tables."""
    users = {
        r["telegram_user_id"]: {
            "telegram_user_id": r["telegram_user_id"],
            "tier": r["tier"] or "free",
            "is_beta": bool(r["is_beta"]),
            "stripe_customer_id": r["stripe_customer_id"],
            "stripe_subscription_id": r["stripe_subscription_id"],
        }
        for r in _rows(USAGE_DB, "SELECT * FROM user_profiles")
    }

    credentials = [
        {
            "telegram_user_id": r["telegram_user_id"],
            "kaizen_username_enc": bytes(r["kaizen_username_enc"]).decode("latin1"),
            "kaizen_password_enc": bytes(r["kaizen_password_enc"]).decode("latin1"),
        }
        for r in _rows(CORE_DB, "SELECT * FROM usercredential")
    ]

    profiles = [
        {
            "telegram_user_id": r["telegram_user_id"],
            "training_level": r["training_level"],
            "curriculum": r["curriculum"] or "2025",
            "kaizen_role": r["kaizen_role"],
            "voice_profile": json.loads(r["voice_profile"]) if r["voice_profile"] else None,
            "voice_examples_count": r["voice_examples_count"] or 0,
        }
        for r in _rows(CORE_DB, "SELECT * FROM userprofile")
    ]

    usage = [
        {
            "telegram_user_id": r["telegram_user_id"],
            "form_type": r["form_type"],
            "status": r["status"] or "filed",
            "filed_at": r["filed_at"],
        }
        for r in _rows(USAGE_DB, "SELECT * FROM portfolio_usage")
    ]

    kc = [
        {
            "telegram_user_id": r["telegram_user_id"],
            "form_type": r["form_type"],
            "kcs_selected": json.loads(r["kcs_selected"]) if r["kcs_selected"] else [],
            "created_at": r["created_at"],
        }
        for r in _rows(USAGE_DB, "SELECT * FROM kc_coverage")
    ]

    consent = [
        {
            "telegram_user_id": r["telegram_user_id"],
            "consent_version": r["consent_version"],
            "consent_text_hash": r["consent_text_hash"],
            "action": r["action"],
            "channel": r["channel"],
            "lawful_basis": r["lawful_basis"],
            "created_at": r["created_at"],
        }
        for r in _rows(USAGE_DB, "SELECT * FROM consent_records")
    ]

    # Every user seen anywhere needs an anchor row, not just those with a tier.
    for row in credentials + profiles + usage + kc + consent:
        users.setdefault(row["telegram_user_id"], {
            "telegram_user_id": row["telegram_user_id"],
            "tier": "free",
        })

    return {
        "pg_users": list(users.values()),
        "pg_credentials": credentials,
        "pg_profile": profiles,
        "pg_usage": usage,
        "pg_kc_coverage": kc,
        "pg_consent_records": consent,
    }


def _client():
    from supabase_sync import _supabase

    sb = _supabase()
    if sb is None:
        sys.exit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — nothing to write to.")
    return sb


def _existing_count(sb, table: str) -> int:
    try:
        return sb.table(table).select("*", count="exact").limit(1).execute().count or 0
    except Exception as exc:
        sys.exit(f"Could not read {table} — has the schema been applied? ({exc})")


def _chunks(rows: list, size: int = 500):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually write (default is a dry run)")
    ap.add_argument("--force", action="store_true",
                    help="write append-only tables even if they already hold rows (risks duplicates)")
    args = ap.parse_args()

    data = collect()

    print(f"Source: {DATA_DIR}")
    for table, rows in data.items():
        print(f"  {table:<22} {len(rows):>6} rows")
    total = sum(len(r) for r in data.values())
    print(f"  {'TOTAL':<22} {total:>6} rows")

    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply.")
        return 0

    sb = _client()

    # Refuse before writing anything, rather than half-way through: duplicated
    # pg_usage rows would silently corrupt free-tier metering.
    if not args.force:
        occupied = [t for t in APPEND_ONLY if _existing_count(sb, t) > 0]
        if occupied:
            print(f"\nREFUSING: append-only tables already hold rows: {', '.join(occupied)}.")
            print("Re-running would duplicate them. Use --force only if you know they are empty of this data.")
            return 1

    print()
    for table, rows in data.items():
        if not rows:
            print(f"  {table:<22} skipped (nothing to write)")
            continue
        written = 0
        for chunk in _chunks(rows):
            if table in APPEND_ONLY:
                sb.table(table).insert(chunk).execute()
            else:
                sb.table(table).upsert(chunk, on_conflict="telegram_user_id").execute()
            written += len(chunk)
        print(f"  {table:<22} wrote {written}")

    print("\nVerifying row counts against the source...")
    ok = True
    for table, rows in data.items():
        remote = _existing_count(sb, table)
        match = "OK " if remote >= len(rows) else "MISMATCH"
        if remote < len(rows):
            ok = False
        print(f"  {match} {table:<22} local={len(rows):<6} remote={remote}")

    print("\nBackfill complete." if ok else "\nBackfill finished WITH MISMATCHES — investigate before trusting the mirror.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
