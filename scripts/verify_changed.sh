#!/usr/bin/env bash
#
# scripts/verify_changed.sh
#
# Fast, offline change-safety gate for Portfolio Guru. Run this before
# calling ANY change "done". No live Telegram, no live Vertex AI, no live
# Kaizen/Playwright, no live Stripe network calls — everything here is
# mocked/offline pytest plus static guardrails.
#
# Covers the critical product journeys (grounded in the current test suite,
# not invented):
#   1. Case capture -> extraction -> form recommendation
#   2. Draft preview -> approval -> Kaizen draft save (draft-only, never submit)
#   3. Telegram channel contract / callback state safety
#   4. Consent / beta gating
#   5. Stripe billing state machine (offline, no live network)
#   6. Attachment handoff to the correct Kaizen ticket/form
#   7. Funnel/reliability telemetry (PHI-free)
#
# Usage: bash scripts/verify_changed.sh

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
cd "$ROOT"

echo "=== verify:changed — Portfolio Guru fast change-safety gate ==="

echo
echo "--- Static guardrail: setup/consent path ---"
python3 scripts/setup_consent_path_check.py

if [[ ! -d backend ]]; then
  echo "ERROR: backend/ directory not found." >&2
  exit 1
fi

cd backend
if [[ -x venv/bin/python3 ]]; then
  PY="venv/bin/python3"
elif [[ -x .venv/bin/python3 ]]; then
  PY=".venv/bin/python3"
else
  PY="python3"
fi

JOURNEY_TESTS=(
  # 1. Case capture -> extraction -> form recommendation
  tests/test_conversational_case_engine.py
  tests/test_deterministic_form_recommender.py
  tests/test_form_recommender_per_shape.py
  tests/test_vnext_form_recommender.py
  # 2. Draft preview -> approval -> Kaizen draft save
  tests/test_vnext_draft_preview.py
  tests/test_filing_reliability.py
  tests/test_filing_attempt_log.py
  tests/test_curriculum_filing_recovery.py
  # 3. Telegram channel contract / callback state safety
  tests/test_channel_contract.py
  tests/test_channel_reply_policy.py
  tests/test_channel_actions.py
  tests/test_controlled_flexibility.py
  tests/test_concurrent_user_isolation.py
  # 4. Consent / beta gating
  tests/test_consent_gate.py
  # 5. Stripe billing state machine (offline)
  tests/test_stripe_handler.py
  tests/test_stripe_webhook_e2e.py
  tests/test_stripe_reconciliation.py
  # 6. Attachment handoff
  tests/test_attachment_handoff.py
  # 7. Funnel/reliability telemetry
  tests/test_funnel_metrics.py
)

echo
echo "--- Journey smoke: ${#JOURNEY_TESTS[@]} test files, offline/mocked only ---"
"$PY" -m pytest "${JOURNEY_TESTS[@]}" -q

echo
echo "verify:changed PASSED."
