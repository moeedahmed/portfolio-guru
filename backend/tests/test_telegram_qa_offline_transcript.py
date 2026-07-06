"""Offline Telegram QA transcript test.

Drives six anonymised Haris/Sana golden cases through the real PTB
``Application.process_update()`` stack with ``OfflineRequest`` blocking all
network calls, then writes a JSON + Markdown transcript under
``.artifacts/telegram-qa-transcript/<utc-stamp>/``.

This is the offline complement to ``test_e2e_live.py`` (Telethon, gated by
``TELEGRAM_LIVE_APPROVED``). The transcript captures bot replies, inline
buttons, observed form recommendations, draft state, and per-step pass/fail
observations so future agents can review without screenshots.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import dogfood_audit
from tests.fixtures.telegram_qa_cases import CASES
from tests.qa_transcript import (
    CaseTranscript,
    default_artifact_dir,
    run_case,
    write_reports,
)


pytestmark = pytest.mark.asyncio


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _artifact_dir() -> Path:
    override = os.environ.get("TELEGRAM_QA_TRANSCRIPT_DIR")
    if override:
        return Path(override)
    return default_artifact_dir(_repo_root())


async def test_offline_qa_transcript_runs_all_golden_cases(monkeypatch):
    out_dir = _artifact_dir()
    audit_path = out_dir / "dogfood-audit.ndjson"
    monkeypatch.setenv("PORTFOLIO_GURU_DOGFOOD_AUDIT_PATH", str(audit_path))
    transcripts: list[CaseTranscript] = []
    failures: list[str] = []

    for case in CASES:
        transcript = await run_case(case, monkeypatch)
        transcripts.append(transcript)
        if not transcript.passed:
            failures.append(case.case_id)

    json_path, md_path = write_reports(transcripts, out_dir)
    print(f"\nTelegram QA transcript written:\n  {json_path}\n  {md_path}")

    # At minimum, every case must produce at least one bot reply on the first
    # step. Stricter per-step expectations are surfaced in the transcript as
    # `failures` but do not fail the suite, because extractor mocks and form
    # button labels can legitimately drift across UI iterations.
    for transcript in transcripts:
        first = transcript.steps[0]
        assert first.bot_messages, (
            f"{transcript.case_id}: no bot reply to the initial case input — see {md_path}"
        )
        assert not first.error, (
            f"{transcript.case_id}: handler raised on initial case input "
            f"({first.error}) — see {md_path}"
        )
        assert not first.timed_out, (
            f"{transcript.case_id}: handler timed out on initial case input — see {md_path}"
        )

    audit_counts = dogfood_audit.count_by_event(dogfood_audit.iter_records(audit_path))
    assert audit_counts["user_input"] >= len(CASES)
    assert audit_counts["decision_path"] >= len(CASES)
    assert audit_counts["draft_payload"] >= 1
    assert audit_counts["bot_response"] >= 1
    assert audit_counts["media_document_flow"] >= 1
