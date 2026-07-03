"""Explicit-consent gate for clinical case content (UK GDPR Art 9(2)(a)).

Copy + record spec: docs/legal/consent-copy.md (solicitor review pending —
any wording change MUST bump CONSENT_VERSION, which re-prompts every user
before further health-data processing). The exact text of each shipped
version is archived in docs/legal/consent-versions/<version>.md; a test pins
the archive to consent_text_hash() so shipped wording can't drift silently.

Records are append-only evidence: a withdrawal adds a record, it never
overwrites the grant, and no erasure path may delete this table — the
consent history is what proves the lawful basis for past processing.
"""
import hashlib
import os

import aiosqlite

import usage  # consent records live in the same DB; tests patch usage.DB_PATH

CONSENT_VERSION = "2026-07-03.v1"
LAWFUL_BASIS = "art9_2a_explicit_consent"

CONSENT_TEXT = (
    "🔐 Consent before your first case\n"
    "\n"
    "Your case notes count as health data, so Portfolio Guru needs your "
    "consent before it drafts from them.\n"
    "\n"
    "Please confirm:\n"
    "\n"
    "• I will only send anonymised case details.\n"
    "\n"
    "• Portfolio Guru can use my case notes to draft portfolio entries.\n"
    "\n"
    "• Case content is processed by Google Gemini via Vertex AI in the EU "
    "(London).\n"
    "\n"
    "• My Kaizen login is stored encrypted and is not sent to the AI model.\n"
    "\n"
    "• Portfolio Guru saves drafts only. Nothing is submitted to a supervisor.\n"
    "\n"
    "• I can withdraw consent and erase my data any time with /reset.\n"
    "\n"
    "Tap \"I consent\" to continue. This confirms you are a GMC-registered "
    "doctor using this for your own training record.\n"
    "\n"
    f"Full details: /privacy · Consent version {CONSENT_VERSION}\n"
)


def consent_text_hash() -> str:
    """Hash of the exact wording shown — stored per record as evidence."""
    return hashlib.sha256(CONSENT_TEXT.encode("utf-8")).hexdigest()


async def _ensure_table():
    os.makedirs(os.path.dirname(usage.DB_PATH), exist_ok=True)
    async with aiosqlite.connect(usage.DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS consent_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                consent_version TEXT NOT NULL,
                consent_text_hash TEXT NOT NULL,
                action TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'telegram',
                lawful_basis TEXT NOT NULL DEFAULT 'art9_2a_explicit_consent',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_consent_user
            ON consent_records(telegram_user_id, consent_version)
        """)
        await db.commit()


async def has_current_consent(user_id: int) -> bool:
    """True only if the user's latest record for the CURRENT version is a
    grant. A version bump therefore re-gates everyone until they re-accept."""
    await _ensure_table()
    async with aiosqlite.connect(usage.DB_PATH) as db:
        async with db.execute(
            "SELECT action FROM consent_records "
            "WHERE telegram_user_id = ? AND consent_version = ? "
            "ORDER BY id DESC LIMIT 1",
            (user_id, CONSENT_VERSION),
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row) and row[0] in ("granted", "re-granted")


async def record_consent(user_id: int, channel: str = "telegram") -> None:
    """Append a grant for the current version (append-only, never updates)."""
    await _ensure_table()
    async with aiosqlite.connect(usage.DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM consent_records WHERE telegram_user_id = ? LIMIT 1",
            (user_id,),
        ) as cursor:
            has_history = await cursor.fetchone() is not None
        await db.execute(
            "INSERT INTO consent_records "
            "(telegram_user_id, consent_version, consent_text_hash, action, channel, lawful_basis) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                user_id,
                CONSENT_VERSION,
                consent_text_hash(),
                "re-granted" if has_history else "granted",
                channel,
                LAWFUL_BASIS,
            ),
        )
        await db.commit()


async def record_withdrawal(user_id: int, channel: str = "telegram") -> None:
    """Append a withdrawal. No-op for users who never granted consent, so an
    unconsented /reset doesn't log spurious withdrawal records."""
    await _ensure_table()
    async with aiosqlite.connect(usage.DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM consent_records "
            "WHERE telegram_user_id = ? AND action IN ('granted', 're-granted') LIMIT 1",
            (user_id,),
        ) as cursor:
            if await cursor.fetchone() is None:
                return
        await db.execute(
            "INSERT INTO consent_records "
            "(telegram_user_id, consent_version, consent_text_hash, action, channel, lawful_basis) "
            "VALUES (?, ?, ?, 'withdrawn', ?, ?)",
            (user_id, CONSENT_VERSION, consent_text_hash(), channel, LAWFUL_BASIS),
        )
        await db.commit()


async def get_consent_status(user_id: int) -> dict | None:
    """Latest consent record for /privacy: {version, action, at} or None."""
    await _ensure_table()
    async with aiosqlite.connect(usage.DB_PATH) as db:
        async with db.execute(
            "SELECT consent_version, action, created_at FROM consent_records "
            "WHERE telegram_user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {"version": row[0], "action": row[1], "at": row[2]}
