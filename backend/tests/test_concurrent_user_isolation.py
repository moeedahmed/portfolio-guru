"""Offline multi-user isolation checks for the filing readiness sprint.

No Telegram, Kaizen, credentials, browser, or network. These tests exercise
the serialised state shapes the bot stores through PicklePersistence so two
users filing around the same time cannot share draft state.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from hypothesis import given, settings, strategies as st


def _context():
    return SimpleNamespace(user_data={})


def test_active_drafts_are_isolated_per_user_context():
    import bot
    from models import FormDraft

    user_a = _context()
    user_b = _context()
    user_a.user_data["case_user_text"] = ["A reflection"]
    user_b.user_data["case_user_text"] = ["B reflection"]

    bot._store_draft(
        user_a,
        FormDraft(
            form_type="CBD",
            fields={"reflection": "A reflection", "stage_of_training": "Higher"},
            uuid="a-draft",
        ),
    )
    bot._store_draft(
        user_b,
        FormDraft(
            form_type="DOPS",
            fields={"reflection": "B reflection", "stage_of_training": "ACCS"},
            uuid="b-draft",
        ),
    )

    draft_a = bot._load_draft(user_a)
    draft_b = bot._load_draft(user_b)

    assert draft_a.form_type == "CBD"
    assert draft_a.fields["reflection"] == "A reflection"
    assert draft_a.uuid == "a-draft"
    assert draft_b.form_type == "DOPS"
    assert draft_b.fields["reflection"] == "B reflection"
    assert draft_b.uuid == "b-draft"
    assert user_a.user_data["draft_data"] != user_b.user_data["draft_data"]


def test_retryable_last_filed_case_state_isolated_between_users():
    import bot

    user_a = _context()
    user_b = _context()

    user_a.user_data.update({
        "last_filing_status": "partial",
        "last_filing_uncertain": True,
        "last_amend_draft": {
            "_type": "FORM",
            "form_type": "CBD",
            "fields": {"reflection": "A case"},
            "uuid": "a-draft",
        },
        "last_amend_case_text": "A patient case",
        "last_amend_chosen_form": "CBD",
    })
    user_b.user_data.update({
        "last_filing_status": "partial",
        "last_filing_uncertain": True,
        "last_amend_draft": {
            "_type": "FORM",
            "form_type": "DOPS",
            "fields": {"reflection": "B case"},
            "uuid": "b-draft",
        },
        "last_amend_case_text": "B patient case",
        "last_amend_chosen_form": "DOPS",
    })

    assert bot._restore_retryable_draft(user_a) is True
    assert bot._restore_retryable_draft(user_b) is True

    assert bot._load_draft(user_a).uuid == "a-draft"
    assert bot._load_draft(user_b).uuid == "b-draft"
    assert user_a.user_data["case_text"] == "A patient case"
    assert user_b.user_data["case_text"] == "B patient case"



@pytest.mark.asyncio
@settings(max_examples=12, derandomize=True, database=None, deadline=None)
@given(
    user_ids=st.lists(st.integers(10000, 99999), min_size=2, max_size=3, unique=True),
    form_type=st.sampled_from(("CBD", "DOPS")),
    replay_order=st.lists(st.integers(0, 5), min_size=1, max_size=5),
)
async def test_concurrent_approvals_and_old_replays_never_file_another_users_draft(
    user_ids, form_type, replay_order,
):
    import bot
    import draft_backup
    import supabase_sync
    from models import FormDraft
    from tests.bot_simulator import BotSimulator

    # All state is fresh per example, including deterministic callback tokens.
    with pytest.MonkeyPatch.context() as mp:
        async with asyncio.timeout(3):
            tokens = count(1)
            mp.setattr(bot.secrets, "token_hex", lambda size: f"{next(tokens):06x}")
            mp.setattr(bot, "get_credentials", lambda uid: (f"offline-user-{uid}", "fake"))
            mp.setattr(bot, "_needs_filing_curriculum_choice", lambda uid: False)
            mp.setattr(bot, "get_training_level", lambda uid: "HIGHER")
            mp.setattr(bot, "get_curriculum", lambda uid: "2025")
            mp.setattr(bot, "_typing_until", AsyncMock())
            # Cancel the production progress timer at its first suspension; this
            # property has no sleeps, and does not wait for cosmetic progress text.
            mp.setattr(bot.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
            mp.setattr(bot, "record_case_filed", AsyncMock())
            mp.setattr(bot, "check_can_file", AsyncMock(return_value=(True, 1, None, "pro_plus")))
            mp.setattr(bot, "get_case_history", AsyncMock(return_value=[]))
            mp.setattr(draft_backup, "save", Mock())
            mp.setattr(draft_backup, "discard", Mock())
            mp.setattr(supabase_sync, "mirror_case", Mock())

            simulators = {uid: BotSimulator(user_id=uid) for uid in user_ids}
            contexts = {uid: sim._make_context() for uid, sim in simulators.items()}
            filed = []
            reached_filer = asyncio.Barrier(len(user_ids) + 1)
            release_filer = asyncio.Event()

            async def file_offline(**kwargs):
                filed.append(deepcopy(kwargs))
                await reached_filer.wait()
                await release_filer.wait()
                return {"status": "success", "filled": ["clinical_reasoning"], "skipped": [],
                        "method": "deterministic", "verified": True,
                        "draft_url": f"https://kaizenep.com/offline/{kwargs['telegram_user_id']}"}
            mp.setattr(bot, "route_filing", file_offline)

            def store_round(round_number):
                approvals = {}
                for uid in user_ids:
                    context = contexts[uid]
                    marker = f"Only user {uid}, draft {round_number}"
                    context.user_data["case_text"] = marker
                    bot._store_draft(context, FormDraft(
                        form_type=form_type, uuid=f"{uid}-{round_number}",
                        fields={"clinical_reasoning": marker, "reflection": "",
                                "date_of_encounter": "2026-10-01"},
                    ))
                    keyboard = bot._build_approval_keyboard(context=context)
                    approvals[uid] = next(button.callback_data for row in keyboard.inline_keyboard
                                          for button in row if button.callback_data.startswith("APPROVE|"))
                return approvals

            async def approve(uid, callback):
                await bot.handle_approval_approve(
                    simulators[uid]._make_callback_update(callback), contexts[uid],
                )

            def assert_payloads(round_number):
                payloads = filed[(round_number - 1) * len(user_ids):]
                assert len(payloads) == len(user_ids), filed
                assert {payload["telegram_user_id"] for payload in payloads} == set(user_ids)
                for payload in payloads:
                    uid = payload["telegram_user_id"]
                    assert payload["credentials"] == {"username": f"offline-user-{uid}", "password": "fake"}
                    assert payload["fields"]["clinical_reasoning"] == f"Only user {uid}, draft {round_number}"
                    assert payload["form_type"] == form_type

            old_approvals = None
            for round_number in (1, 2):
                approvals = store_round(round_number)
                snapshots = {uid: deepcopy(contexts[uid].user_data["draft_data"]) for uid in user_ids}
                if old_approvals:
                    # A previous case's Save cannot approve this new case. A token
                    # taken from another user's message cannot approve it either.
                    for index in replay_order:
                        uid = user_ids[index % len(user_ids)]
                        other_uid = user_ids[(index + 1) % len(user_ids)]
                        await approve(uid, old_approvals[uid])
                        await approve(uid, approvals[other_uid])
                    assert len(filed) == len(user_ids)
                    assert all(contexts[uid].user_data["draft_data"] == snapshots[uid] for uid in user_ids)

                release_filer.clear()
                tasks = [asyncio.create_task(approve(uid, approvals[uid])) for uid in reversed(user_ids)]
                try:
                    # All users are now in the filer at once. Replay while the
                    # external boundary is held, rather than depending on timing.
                    await reached_filer.wait()
                    await asyncio.gather(*(approve(user_ids[i % len(user_ids)],
                                                   approvals[user_ids[i % len(user_ids)]])
                                           for i in replay_order))
                    assert_payloads(round_number)
                finally:
                    release_filer.set()
                    await asyncio.gather(*tasks)
                await asyncio.gather(*(approve(uid, approvals[uid]) for uid in user_ids))
                assert_payloads(round_number)
                for uid in user_ids:
                    assert bot._load_draft(contexts[uid]) is None
                    assert contexts[uid].user_data["last_amend_draft"]["uuid"] == f"{uid}-{round_number}"
                old_approvals = approvals
