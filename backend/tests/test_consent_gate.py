"""Art 9(2)(a) explicit-consent gate tests (launch checklist 1.2).

Invariants pinned here:

1. An unconsented user's case is blocked BEFORE any LLM/processor call, for
   every input path (they all route through handle_case_input's gate).
2. Accepting records a versioned, hashed, timestamped grant; the gate opens.
3. Declining keeps the gate closed and processes nothing.
4. A consent-version bump re-gates previously consented users.
5. The record store is append-only: withdrawal adds a record, the original
   grant survives as evidence.
6. The shipped consent text matches its immutable archived copy byte-for-byte
   (docs/legal/consent-versions/) — wording can't drift without a version bump.
7. A group member can't consent on the prompted user's behalf.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from telegram.ext import ConversationHandler

from tests.bot_simulator import BotSimulator


@pytest.fixture
def tmp_consent_db(tmp_path, monkeypatch):
    import usage

    monkeypatch.setattr(usage, "DB_PATH", str(tmp_path / "usage.db"))
    import consent

    return consent


# ─── The gate in handle_case_input ────────────────────────────────────────


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_unconsented_case_is_blocked_before_any_processing(tmp_consent_db):
    import bot
    from bot import handle_case_input

    sim = BotSimulator()
    update = sim._make_text_update(
        "45M with chest pain, troponin positive, managed as ACS and reflected on escalation."
    )
    context = sim._make_context()

    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 0, 5, "free"))) as can_file, \
         patch("bot.classify_intent", new_callable=AsyncMock) as classify, \
         patch("bot.recommend_form_types", new_callable=AsyncMock) as recommend:
        result = await handle_case_input(update, context)

    assert result == ConversationHandler.END
    text = sim.get_last_text() or ""
    assert "consent" in text.lower()
    assert "has not been processed" in text
    # Nothing clinical reached a processor and no usage was counted.
    classify.assert_not_awaited()
    recommend.assert_not_awaited()
    can_file.assert_not_awaited()
    button_data = [data for _, data in sim.get_last_buttons()]
    assert any(d.startswith("CONSENT|accept|") for d in button_data)
    assert any(d.startswith("CONSENT|decline|") for d in button_data)


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_accept_records_grant_and_opens_the_gate(tmp_consent_db):
    consent = tmp_consent_db
    from bot import handle_consent_callback

    sim = BotSimulator()
    user_id = sim.user_id
    update = sim._make_callback_update(f"CONSENT|accept|{user_id}")
    context = sim._make_context()

    await handle_consent_callback(update, context)

    assert await consent.has_current_consent(user_id) is True
    status = await consent.get_consent_status(user_id)
    assert status["version"] == consent.CONSENT_VERSION
    assert status["action"] == "granted"
    assert status["at"]  # timestamped
    edited = update.callback_query.edit_message_text.call_args.args[0]
    assert consent.CONSENT_VERSION in edited


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_decline_keeps_the_gate_closed(tmp_consent_db):
    consent = tmp_consent_db
    from bot import handle_consent_callback

    sim = BotSimulator()
    user_id = sim.user_id
    update = sim._make_callback_update(f"CONSENT|decline|{user_id}")
    context = sim._make_context()

    await handle_consent_callback(update, context)

    assert await consent.has_current_consent(user_id) is False
    edited = update.callback_query.edit_message_text.call_args.args[0]
    assert "nothing was processed" in edited.lower()


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_another_users_tap_cannot_grant_consent(tmp_consent_db):
    consent = tmp_consent_db
    from bot import handle_consent_callback

    sim = BotSimulator()
    prompted_uid = sim.user_id + 1  # prompt belongs to someone else
    update = sim._make_callback_update(f"CONSENT|accept|{prompted_uid}")
    context = sim._make_context()

    await handle_consent_callback(update, context)

    assert await consent.has_current_consent(sim.user_id) is False
    assert await consent.has_current_consent(prompted_uid) is False
    update.callback_query.edit_message_text.assert_not_called()


# ─── Record semantics ─────────────────────────────────────────────────────


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_version_bump_regates_consented_users(tmp_consent_db, monkeypatch):
    consent = tmp_consent_db
    user_id = 201
    await consent.record_consent(user_id)
    assert await consent.has_current_consent(user_id) is True

    monkeypatch.setattr(consent, "CONSENT_VERSION", "2099-01-01.v2")
    assert await consent.has_current_consent(user_id) is False


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_withdrawal_appends_and_preserves_the_grant(tmp_consent_db):
    consent = tmp_consent_db
    import aiosqlite
    import usage

    user_id = 202
    await consent.record_consent(user_id)
    await consent.record_withdrawal(user_id)

    assert await consent.has_current_consent(user_id) is False
    async with aiosqlite.connect(usage.DB_PATH) as db:
        async with db.execute(
            "SELECT action FROM consent_records WHERE telegram_user_id = ? ORDER BY id",
            (user_id,),
        ) as cursor:
            actions = [r[0] for r in await cursor.fetchall()]
    assert actions == ["granted", "withdrawn"]  # append-only, grant preserved

    # Re-granting after withdrawal is recorded as such.
    await consent.record_consent(user_id)
    assert await consent.has_current_consent(user_id) is True
    status = await consent.get_consent_status(user_id)
    assert status["action"] == "re-granted"


@pytest.mark.consent_gate
@pytest.mark.asyncio
async def test_withdrawal_without_grant_is_a_noop(tmp_consent_db):
    consent = tmp_consent_db
    import aiosqlite
    import usage

    await consent.record_withdrawal(303)
    async with aiosqlite.connect(usage.DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM consent_records") as cursor:
            assert (await cursor.fetchone())[0] == 0


# ─── Immutable wording archive ────────────────────────────────────────────


def test_shipped_consent_text_matches_immutable_archive():
    import hashlib

    import consent

    archive = (
        Path(__file__).resolve().parent.parent.parent
        / "docs" / "legal" / "consent-versions" / f"{consent.CONSENT_VERSION}.md"
    )
    assert archive.is_file(), (
        f"missing archived copy for consent version {consent.CONSENT_VERSION} — "
        "every shipped version must be archived in docs/legal/consent-versions/"
    )
    archived = archive.read_text(encoding="utf-8")
    assert archived == consent.CONSENT_TEXT, (
        "consent wording differs from its archived copy — changing the text "
        "requires a NEW CONSENT_VERSION (and a new archive file), never an edit"
    )
    assert hashlib.sha256(archived.encode("utf-8")).hexdigest() == consent.consent_text_hash()
