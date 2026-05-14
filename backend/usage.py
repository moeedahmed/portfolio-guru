"""
Usage tracking for Portfolio Guru.
Records filed cases per user for metering and portfolio health analysis.
Uses aiosqlite for async SQLite access.
"""
import os
import aiosqlite
from datetime import datetime, timezone, timedelta

_DEFAULT_DB = os.path.expanduser("~/.openclaw/data/portfolio-guru/usage.db")
DB_PATH = os.environ.get("USAGE_DB_PATH", _DEFAULT_DB)

# Tier limits
TIER_LIMITS = {
    "free": 10,
    "pro": 100,
    "pro_plus": -1,  # unlimited
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
    """Return case limit for tier: free=10, pro=100, pro_plus=unlimited (-1)."""
    return TIER_LIMITS.get(tier, 10)


async def check_can_file(user_id: int) -> tuple:
    """Check if user can file. Returns (allowed, used, limit, tier)."""
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
