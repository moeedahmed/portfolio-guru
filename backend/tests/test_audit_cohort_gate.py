"""The dogfood audit trail must never hold a real beta user's clinical turn.

The log keeps narrative readable on purpose, so the cohort gate is the only
thing standing between it and an undocumented Art. 9 store. These tests fail if
that gate stops holding.
"""
from __future__ import annotations

import json

import pytest

import dogfood_audit


REAL_BETA_USER = 1180562596  # shape of a genuine Telegram id
OPERATOR = 6912896590
SYNTHETIC = 99999999


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    path = tmp_path / "dogfood-audit.ndjson"
    monkeypatch.setenv("PORTFOLIO_GURU_DOGFOOD_AUDIT_PATH", str(path))
    monkeypatch.delenv("PORTFOLIO_GURU_DOGFOOD_AUDIT_COHORT", raising=False)
    monkeypatch.delenv("PORTFOLIO_GURU_DOGFOOD_AUDIT_DISABLED", raising=False)
    return path


def _record(user_id, path):
    return dogfood_audit.record_event(
        "user_input",
        user_id=user_id,
        payload={"text": "78-year-old with crushing central chest pain"},
        log_path=path,
    )


def test_real_beta_users_turn_is_not_written(audit_path):
    assert _record(REAL_BETA_USER, audit_path) is None
    assert not audit_path.exists(), "a real user's clinical turn reached the audit log"


def test_unattributed_turn_is_not_written(audit_path):
    """We can't prove an unattributed turn isn't a beta user's, so it fails closed."""
    assert _record(None, audit_path) is None
    assert not audit_path.exists()


def test_operator_and_synthetic_traffic_is_still_captured(audit_path):
    assert _record(OPERATOR, audit_path) is not None
    assert _record(SYNTHETIC, audit_path) is not None

    users = [json.loads(line)["user_id"] for line in audit_path.read_text().splitlines()]
    assert users == [OPERATOR, SYNTHETIC]


def test_harness_override_restores_full_capture(audit_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_GURU_DOGFOOD_AUDIT_COHORT", "all")

    assert _record(REAL_BETA_USER, audit_path) is not None
    assert _record(None, audit_path) is not None


def test_qa_harness_fixture_id_classifies_as_synthetic():
    """The offline QA transcript drives the whole stack as 99999. Leaving it
    unclassified counted every harness run as a real beta user in
    /filingreport — and would now let harness turns into the audit log."""
    from filing_attempt_log import is_synthetic_user

    assert is_synthetic_user(99999)
    assert not is_synthetic_user(REAL_BETA_USER)
    # 12345 means "a genuine beta user" throughout the unit suite; it must stay
    # real so those tests keep testing what they claim to.
    assert not is_synthetic_user(12345)


def test_log_rotates_at_the_size_cap_and_keeps_one_generation(audit_path, monkeypatch):
    """Nothing bounded this log before; it reached 44 MB unnoticed."""
    monkeypatch.setenv("PORTFOLIO_GURU_DOGFOOD_AUDIT_MAX_BYTES", "1000000")
    audit_path.write_bytes(b"x" * 1_200_000)

    assert _record(SYNTHETIC, audit_path) is not None

    rotated = audit_path.with_suffix(audit_path.suffix + ".1")
    assert rotated.exists(), "oversized log was not rotated"
    assert rotated.stat().st_size == 1_200_000
    # The live log restarts from the new record only.
    assert len(audit_path.read_text().splitlines()) == 1
