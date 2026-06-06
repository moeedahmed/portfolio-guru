"""
Usage tracking for Portfolio Guru.
Records filed cases per user for metering and portfolio health analysis.
Uses aiosqlite for async SQLite access.
"""
import json
import os
import re
import sqlite3
import aiosqlite
from datetime import datetime, timezone, timedelta

_DEFAULT_DB = os.path.expanduser("~/.openclaw/data/portfolio-guru/usage.db")
DB_PATH = os.environ.get("USAGE_DB_PATH", _DEFAULT_DB)

# Tier limits
# - free: 5 cases/month (taste-only — gives the user enough to feel the magic)
# - pro: legacy tier (100/mo). No new sign-ups; existing subscribers honoured.
# - pro_plus: Unlimited, the only paid tier currently sold (£9.99/mo).
TIER_LIMITS = {
    "free": 5,
    "pro": 100,
    "pro_plus": -1,
}


async def _ensure_db():
    """Create tables if they don't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                form_type TEXT NOT NULL,
                filed_at TEXT DEFAULT (datetime('now')),
                month_key TEXT GENERATED ALWAYS AS (strftime('%Y-%m', filed_at)) STORED,
                status TEXT DEFAULT 'filed'
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_usage_user_month
            ON portfolio_usage(telegram_user_id, month_key)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                telegram_user_id INTEGER PRIMARY KEY,
                tier TEXT DEFAULT 'free',
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stripe_webhook_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                processed_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS kc_coverage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                form_type TEXT NOT NULL,
                kcs_selected TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_kc_coverage_user
            ON kc_coverage(telegram_user_id)
        """)
        # Additive migration: is_beta flag (unlimited override, not a paid tier).
        try:
            await db.execute("ALTER TABLE user_profiles ADD COLUMN is_beta INTEGER DEFAULT 0")
        except Exception:
            pass  # column already exists
        await db.commit()


async def record_case_filed(user_id: int, form_type: str, status: str = "filed"):
    """Record a filed case for usage tracking."""
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO portfolio_usage (telegram_user_id, form_type, status) VALUES (?, ?, ?)",
            (user_id, form_type, status),
        )
        await db.commit()

    try:
        from supabase_sync import mirror_usage
        mirror_usage(user_id, form_type)
    except Exception:
        pass


async def get_cases_this_month(user_id: int) -> int:
    """Count cases filed this month."""
    await _ensure_db()
    month_key = datetime.now().strftime("%Y-%m")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM portfolio_usage WHERE telegram_user_id = ? AND month_key = ?",
            (user_id, month_key),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_case_history(user_id: int, months: int = 6) -> list:
    """Get filed cases with form types for portfolio health analysis."""
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT form_type, filed_at, status FROM portfolio_usage
               WHERE telegram_user_id = ?
               AND filed_at >= datetime('now', ?)
               ORDER BY filed_at DESC""",
            (user_id, f"-{months} months"),
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"form_type": r["form_type"], "filed_at": r["filed_at"], "status": r["status"]} for r in rows]


async def delete_portfolio_evidence(user_id: int) -> dict[str, int]:
    """Delete local portfolio evidence for one Telegram user.

    This intentionally leaves ``user_profiles`` billing/subscription rows
    alone. It clears only the local filing/KC evidence that Portfolio Health
    and usage counters read.
    """
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        usage_cursor = await db.execute(
            "DELETE FROM portfolio_usage WHERE telegram_user_id = ?",
            (user_id,),
        )
        kc_cursor = await db.execute(
            "DELETE FROM kc_coverage WHERE telegram_user_id = ?",
            (user_id,),
        )
        await db.commit()
    return {
        "portfolio_usage": max(usage_cursor.rowcount or 0, 0),
        "kc_coverage": max(kc_cursor.rowcount or 0, 0),
    }


async def delete_user_portfolio_history(user_id: int) -> None:
    """Delete account-scoped Portfolio Guru history for one Telegram user."""
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM portfolio_usage WHERE telegram_user_id = ?", (user_id,))
        await db.execute("DELETE FROM kc_coverage WHERE telegram_user_id = ?", (user_id,))
        await db.commit()


def delete_user_portfolio_history_sync(user_id: int) -> None:
    """Synchronous variant used during credential account rotation."""
    if not os.path.exists(DB_PATH):
        return
    with sqlite3.connect(DB_PATH) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "portfolio_usage" in tables:
            db.execute("DELETE FROM portfolio_usage WHERE telegram_user_id = ?", (user_id,))
        if "kc_coverage" in tables:
            db.execute("DELETE FROM kc_coverage WHERE telegram_user_id = ?", (user_id,))
        db.commit()


async def get_user_tier(user_id: int) -> str:
    """Get user's subscription tier. Returns 'free' if not found."""
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT tier FROM user_profiles WHERE telegram_user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else "free"


async def get_monthly_limit(tier: str) -> int:
    """Return case limit for tier: free=5, pro=100, pro_plus/Unlimited=-1."""
    return TIER_LIMITS.get(tier, 5)


async def is_beta_tester(user_id: int) -> bool:
    """Return True if the user has the beta_tester unlimited override."""
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT is_beta FROM user_profiles WHERE telegram_user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row[0]) if row and row[0] is not None else False


async def set_beta_tester(user_id: int, is_beta: bool) -> None:
    """Upsert the beta_tester flag for a user (unlimited override, orthogonal to tier)."""
    await _ensure_db()
    flag = 1 if is_beta else 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO user_profiles (telegram_user_id, is_beta)
               VALUES (?, ?)
               ON CONFLICT(telegram_user_id) DO UPDATE SET
                   is_beta = excluded.is_beta,
                   updated_at = datetime('now')""",
            (user_id, flag),
        )
        await db.commit()


async def check_can_file(user_id: int) -> tuple:
    """Check if user can file. Returns (allowed, used, limit, tier).

    Beta testers bypass the tier limit and report tier="beta", limit=-1.
    """
    if await is_beta_tester(user_id):
        used = await get_cases_this_month(user_id)
        return (True, used, -1, "beta")
    tier = await get_user_tier(user_id)
    limit = await get_monthly_limit(tier)
    used = await get_cases_this_month(user_id)
    if limit == -1:
        return (True, used, limit, tier)
    return (used < limit, used, limit, tier)


async def get_all_active_users() -> list[int]:
    """Return all distinct telegram_user_ids who have filed at least one case."""
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT telegram_user_id FROM portfolio_usage"
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def get_cases_this_week(user_id: int) -> int:
    """Count cases filed Mon–Sun of current week."""
    await _ensure_db()
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    monday_str = monday.strftime("%Y-%m-%d 00:00:00")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM portfolio_usage WHERE telegram_user_id = ? AND filed_at >= ?",
            (user_id, monday_str),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_user_by_stripe_customer(stripe_customer_id: str) -> int | None:
    """Look up telegram_user_id by Stripe customer ID. Returns None if not found."""
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT telegram_user_id FROM user_profiles WHERE stripe_customer_id = ?",
            (stripe_customer_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def get_user_by_stripe_subscription(stripe_subscription_id: str) -> int | None:
    """Look up telegram_user_id by Stripe subscription ID. Returns None if not found."""
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT telegram_user_id FROM user_profiles WHERE stripe_subscription_id = ?",
            (stripe_subscription_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_user_tier(user_id: int, tier: str, stripe_customer_id: str = None, stripe_subscription_id: str = None):
    """Set or update a user's subscription tier."""
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO user_profiles (telegram_user_id, tier, stripe_customer_id, stripe_subscription_id)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(telegram_user_id) DO UPDATE SET
                   tier = excluded.tier,
                   stripe_customer_id = COALESCE(excluded.stripe_customer_id, user_profiles.stripe_customer_id),
                   stripe_subscription_id = COALESCE(excluded.stripe_subscription_id, user_profiles.stripe_subscription_id),
                   updated_at = datetime('now')""",
            (user_id, tier, stripe_customer_id, stripe_subscription_id),
        )
        await db.commit()

    try:
        from supabase_sync import mirror_tier
        mirror_tier(user_id, tier, stripe_customer_id=stripe_customer_id, stripe_subscription_id=stripe_subscription_id)
    except Exception:
        pass


async def has_processed_stripe_event(event_id: str) -> bool:
    """Return whether a Stripe webhook event has already been processed."""
    if not event_id:
        return False
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM stripe_webhook_events WHERE event_id = ?",
            (event_id,),
        ) as cursor:
            return await cursor.fetchone() is not None


async def mark_stripe_event_processed(event_id: str, event_type: str):
    """Record a Stripe webhook event as processed for idempotency."""
    if not event_id:
        return
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO stripe_webhook_events (event_id, event_type)
               VALUES (?, ?)""",
            (event_id, event_type),
        )
        await db.commit()


# KC strings look like "SLO1 KC2: Apply knowledge..." or "SLO10 KC3".
# The number after SLO is what we need to bucket coverage by SLO.
_SLO_NUM_RE = re.compile(r"SLO\s*(\d{1,2})", re.IGNORECASE)


def _slo_number_for_kc(kc: str) -> int | None:
    if not isinstance(kc, str):
        return None
    match = _SLO_NUM_RE.search(kc)
    if not match:
        return None
    try:
        n = int(match.group(1))
    except ValueError:
        return None
    return n if 1 <= n <= 12 else None


async def save_kc_coverage(user_id: int, form_type: str, kcs) -> None:
    """Persist the KCs demonstrated by a filed draft.

    `kcs` is the list of KC strings from the draft's `key_capabilities` (or
    `curriculum_links` fallback). No-ops on empty/invalid input.
    """
    if not kcs:
        return
    if not isinstance(kcs, (list, tuple)):
        return
    cleaned = [str(k).strip() for k in kcs if isinstance(k, (str, int, float)) and str(k).strip()]
    if not cleaned:
        return
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO kc_coverage (telegram_user_id, form_type, kcs_selected) VALUES (?, ?, ?)",
            (user_id, form_type, json.dumps(cleaned)),
        )
        await db.commit()


async def get_kc_coverage(user_id: int) -> dict:
    """Return {slo_number: [kc_string, ...]} for every KC the user has demonstrated.

    KC strings whose SLO number can't be parsed are dropped. Within each
    SLO bucket the KCs are de-duplicated, keeping first-seen order.
    """
    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT kcs_selected FROM kc_coverage WHERE telegram_user_id = ? ORDER BY created_at ASC",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    buckets: dict[int, list[str]] = {}
    seen_by_slo: dict[int, set[str]] = {}
    for (raw,) in rows:
        try:
            kcs = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            continue
        if not isinstance(kcs, list):
            continue
        for kc in kcs:
            if not isinstance(kc, str):
                continue
            slo = _slo_number_for_kc(kc)
            if slo is None:
                continue
            if kc in seen_by_slo.setdefault(slo, set()):
                continue
            seen_by_slo[slo].add(kc)
            buckets.setdefault(slo, []).append(kc)
    return buckets


async def get_kc_stats(user_id: int) -> dict:
    """Summarise a user's KC coverage.

    Returns: {
        total_kcs: int,         # distinct KCs ever demonstrated
        slos_covered: int,      # SLO buckets with at least one KC
        slos_total: int,        # always 12
        recent_kcs: list[str],  # last 5 KC strings (newest first)
    }
    """
    coverage = await get_kc_coverage(user_id)
    total_kcs = sum(len(v) for v in coverage.values())
    slos_covered = sum(1 for v in coverage.values() if v)

    await _ensure_db()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT kcs_selected FROM kc_coverage WHERE telegram_user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    recent: list[str] = []
    seen: set[str] = set()
    for (raw,) in rows:
        try:
            kcs = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            continue
        if not isinstance(kcs, list):
            continue
        for kc in kcs:
            if not isinstance(kc, str) or kc in seen:
                continue
            if _slo_number_for_kc(kc) is None:
                continue
            seen.add(kc)
            recent.append(kc)
            if len(recent) >= 5:
                break
        if len(recent) >= 5:
            break

    return {
        "total_kcs": total_kcs,
        "slos_covered": slos_covered,
        "slos_total": 12,
        "recent_kcs": recent,
    }
