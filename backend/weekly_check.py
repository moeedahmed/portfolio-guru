"""
Weekly gap-detection nudge for Portfolio Guru.

Standalone script — sends each active user a personalised Telegram message
showing cases filed this week and their longest form gap.

Run via external cron: python backend/weekly_check.py
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta

import aiohttp
import aiosqlite

_DEFAULT_DB = os.path.expanduser("~/.openclaw/data/portfolio-guru/usage.db")
DB_PATH = os.environ.get("USAGE_DB_PATH", _DEFAULT_DB)
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

FORM_LABELS = {
    "CBD": "CBD",
    "DOPS": "DOPS",
    "MINI_CEX": "Mini-CEX",
    "ACAT": "ACAT",
    "LAT": "LAT",
    "ACAF": "ACAF",
    "STAT": "STAT",
    "MSF": "MSF",
    "QIAT": "QIAT",
    "JCF": "JCF",
    "ESLE_ASSESS": "ESLE",
    "AUDIT": "Audit",
    "REFLECT_LOG": "Reflective Log",
    "COMPLAINT": "Complaint",
    "SERIOUS_INC": "Serious Incident",
    "CRIT_INCIDENT": "Critical Incident",
    "PDP": "PDP",
    "APPRAISAL": "Appraisal",
    "TEACH": "Teaching",
    "TEACH_OBS": "Teaching Observation",
    "TEACH_CONFID": "Confidentiality",
    "SDL": "SDL",
    "EDU_ACT": "Educational Activity",
    "EDU_MEETING": "ES Meeting",
    "EDU_MEETING_SUPP": "ES Meeting (Supp)",
    "FORMAL_COURSE": "Formal Course",
    "PROC_LOG": "Procedure Log",
    "US_CASE": "Ultrasound Case",
    "RESEARCH": "Research",
    "CLIN_GOV": "Clinical Governance",
    "COST_IMPROVE": "Cost Improvement",
    "EQUIP_SERVICE": "Equipment/Service",
    "BUSINESS_CASE": "Business Case",
    # Management forms
    "MGMT_ROTA": "Rota Management",
    "MGMT_RISK": "Risk Management",
    "MGMT_RISK_PROC": "Risk Procedure",
    "MGMT_INFO": "Information Management",
    "MGMT_EXPERIENCE": "Management Experience",
    "MGMT_REPORT": "Management Report",
    "MGMT_COMPLAINT": "Management Complaint",
    "MGMT_GUIDELINE": "Guideline Development",
    "MGMT_INDUCTION": "Induction",
    "MGMT_PROJECT": "Management Project",
    "MGMT_RECRUIT": "Recruitment",
    "MGMT_TRAINING_EVT": "Training Event",
    # Programme admin forms
    "OOP": "Out of Programme",
    "ABSENCE": "Absence",
    "CCT": "CCT Application",
    "HIGHER_PROG": "Higher Programme",
    "FILE_UPLOAD": "File Upload",
}


def _label(form_type: str) -> str:
    """Human-readable label for a form type. Strips _2021 suffix before lookup."""
    key = form_type.replace("_2021", "")
    return FORM_LABELS.get(key, key)


def _monday_of_this_week() -> str:
    """Return Monday 00:00 UTC of the current ISO week as an ISO string."""
    now = datetime.utcnow()
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


async def _get_active_users(db: aiosqlite.Connection) -> list[int]:
    async with db.execute(
        "SELECT DISTINCT telegram_user_id FROM portfolio_usage"
    ) as cur:
        return [row[0] for row in await cur.fetchall()]


async def _cases_this_week(db: aiosqlite.Connection, user_id: int) -> int:
    monday = _monday_of_this_week()
    async with db.execute(
        "SELECT COUNT(*) FROM portfolio_usage WHERE telegram_user_id = ? AND filed_at >= ?",
        (user_id, monday),
    ) as cur:
        row = await cur.fetchone()
        return row[0] if row else 0


async def _longest_gap(
    db: aiosqlite.Connection, user_id: int
) -> tuple[str, int] | None:
    """Return (form_label, days) for the form type with the oldest most-recent filing.

    Returns None if no form has a gap >= 7 days.
    """
    async with db.execute(
        """SELECT form_type, MAX(filed_at) as last_filed
           FROM portfolio_usage
           WHERE telegram_user_id = ?
           GROUP BY form_type""",
        (user_id,),
    ) as cur:
        rows = await cur.fetchall()

    if not rows:
        return None

    now = datetime.utcnow()
    worst_form = None
    worst_days = 0

    for form_type, last_filed_str in rows:
        try:
            last_filed = datetime.fromisoformat(last_filed_str)
        except (ValueError, TypeError):
            continue
        gap_days = (now - last_filed).days
        if gap_days > worst_days:
            worst_days = gap_days
            worst_form = form_type

    if worst_days < 7 or worst_form is None:
        return None

    return (_label(worst_form), worst_days)


def _build_message(cases: int, gap: tuple[str, int] | None) -> str:
    lines = []

    if cases > 0:
        lines.append("\U0001f4cb Your portfolio this week")
        lines.append("")
        lines.append(f"Cases filed: {cases} this week")
    else:
        lines.append("\U0001f4cb Portfolio check-in")
        lines.append("")
        lines.append("No cases filed this week \u2014 that's fine, but worth a nudge.")

    if gap:
        label, days = gap
        lines.append("")
        lines.append(f"Longest gap: no {label} in {days} days")

    lines.append("")
    if cases > 0:
        lines.append("Keep the momentum going \u2014 tap below to file a case.")
    else:
        lines.append("One case takes 2 minutes. Tap below to get started.")

    return "\n".join(lines)


async def _send_message(
    session: aiohttp.ClientSession, chat_id: int, text: str
) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": json.dumps(
            {
                "inline_keyboard": [
                    [{"text": "\U0001f4cb File a case", "callback_data": "ACTION|file"}]
                ]
            }
        ),
    }
    try:
        async with session.post(url, data=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                print(f"  [ERROR] Telegram API {resp.status}: {body}")
                return False
            result = await resp.json()
            if not result.get("ok"):
                print(f"  [ERROR] Telegram API not ok: {result}")
                return False
            return True
    except Exception as exc:
        print(f"  [ERROR] send failed: {exc}")
        return False


async def main():
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    if not os.path.exists(DB_PATH):
        print(f"No database at {DB_PATH} — nothing to do")
        print("Weekly check complete: 0 sent, 0 failed")
        return

    sent = 0
    failed = 0

    async with aiosqlite.connect(DB_PATH) as db:
        users = await _get_active_users(db)
        if not users:
            print("No active users found")
            print("Weekly check complete: 0 sent, 0 failed")
            return

        print(f"Processing {len(users)} user(s)...")

        async with aiohttp.ClientSession() as session:
            for user_id in users:
                try:
                    cases = await _cases_this_week(db, user_id)
                    gap = await _longest_gap(db, user_id)
                    text = _build_message(cases, gap)
                    ok = await _send_message(session, user_id, text)
                    if ok:
                        sent += 1
                        print(f"  Sent to {user_id}")
                    else:
                        failed += 1
                except Exception as exc:
                    print(f"  [ERROR] user {user_id}: {exc}")
                    failed += 1

    print(f"Weekly check complete: {sent} sent, {failed} failed")


if __name__ == "__main__":
    asyncio.run(main())
