#!/usr/bin/env bash
#
# scripts/verify_release.sh
#
# Full offline release-readiness gate for Portfolio Guru: verify:changed plus
# the complete offline pytest suite (matching the CI "Tests" job in
# .github/workflows/test.yml) and existing preflight git-state checks.
#
# Still strictly offline/mocked — no live Telegram (test_e2e_live.py /
# `-m live`), no live Vertex AI, no live Kaizen/Playwright submission, no
# live Stripe network. Live Telegram E2E stays a separate, explicitly
# approved gate (see AGENTS.md).
#
# Usage: bash scripts/verify_release.sh

set -euo pipefail

# Same fixed throwaway values as the CI Tests job, scoped only to the full
# offline pytest child. They are not exported and cannot reach deploy/runtime
# or live-proof commands.
OFFLINE_TEST_ENV=(
  FERNET_SECRET_KEY=5Wv33F9sq99WGD2lEzwwd3J_JH5p6vxKdDiAwCWqoYQ=
  TELEGRAM_BOT_TOKEN=fake
  GOOGLE_API_KEY=fake
)

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
cd "$ROOT"

echo "=== verify:release — Portfolio Guru full offline release gate ==="

bash scripts/verify_changed.sh

cd backend
if [[ -x venv/bin/python3 ]]; then
  PY="venv/bin/python3"
elif [[ -x .venv/bin/python3 ]]; then
  PY=".venv/bin/python3"
else
  PY="python3"
fi

echo
echo "--- Full offline pytest suite (matches CI Tests job) ---"
env "${OFFLINE_TEST_ENV[@]}" "$PY" -m pytest tests/ -q --ignore=tests/test_e2e.py --ignore=tests/test_e2e_live.py

echo
echo "verify:release PASSED."
echo "Note: this is the offline gate only. Deployment, runtime identity and"
echo "approved live proof remain outside this gate, but run together through"
echo "the one exact-SHA release card in scripts/release_loop.sh."
