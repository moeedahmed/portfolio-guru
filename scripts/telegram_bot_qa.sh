#!/usr/bin/env bash
set -euo pipefail

ROOT="${PORTFOLIO_GURU_APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BACKEND="${ROOT}/backend"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
ARTIFACT_ROOT="${TELEGRAM_BOT_QA_ARTIFACT_ROOT:-${ROOT}/.artifacts/telegram-bot-qa}"
ARTIFACT_DIR="${ARTIFACT_ROOT}/${STAMP}"
RUN_LIVE="${RUN_LIVE_TELEGRAM:-auto}"
REQUIRE_LIVE="${REQUIRE_TELEGRAM_LIVE:-0}"

mkdir -p "$ARTIFACT_DIR"

cd "$BACKEND"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

if [[ -x "venv/bin/python3" ]]; then
  PY="venv/bin/python3"
elif [[ -x ".venv/bin/python3" ]]; then
  PY=".venv/bin/python3"
elif [[ -x "../.venv/bin/python3" ]]; then
  PY="../.venv/bin/python3"
else
  PY="python3"
fi

SUMMARY="${ARTIFACT_DIR}/summary.md"
{
  printf '# Telegram bot QA\n\n'
  printf 'Started: %s\n' "$STAMP"
  printf 'Repo: Portfolio Guru\n'
  printf 'Branch: %s\n' "$(git -C "$ROOT" branch --show-current 2>/dev/null || echo unknown)"
  printf 'Commit: %s\n' "$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  printf 'Python: %s\n\n' "$PY"
} > "$SUMMARY"

run_step() {
  local name="$1"
  shift
  local log="${ARTIFACT_DIR}/${name}.log"
  printf 'Running %s...\n' "$name"
  if "$@" >"$log" 2>&1; then
    printf -- '- %s: PASS\n' "$name" >> "$SUMMARY"
  else
    printf -- '- %s: FAIL\n' "$name" >> "$SUMMARY"
    tail -80 "$log"
    exit 1
  fi
}

run_step collect-live-tests "$PY" -m pytest tests/test_e2e.py tests/test_e2e_live.py --collect-only -q -m "e2e or live"

run_step offline-bot-gate "$PY" -m pytest \
  tests/test_smoke.py \
  tests/test_flow_walker.py \
  tests/test_e2e_offline.py \
  tests/test_snapshots.py \
  tests/test_source_grounding.py \
  -q

HAS_TELETHON_ENV="$("$PY" - <<'PY'
from tests.telegram_live_harness import has_telethon_env
print("1" if has_telethon_env() else "0")
PY
)"

if [[ "$RUN_LIVE" == "0" || "$RUN_LIVE" == "false" ]]; then
  printf -- '- live-telegram: SKIP (disabled by RUN_LIVE_TELEGRAM)\n' >> "$SUMMARY"
elif [[ "$HAS_TELETHON_ENV" == "1" ]]; then
  TELEGRAM_E2E_ARTIFACT_DIR="$ARTIFACT_DIR" run_step live-telegram "$PY" -m pytest \
    tests/test_e2e.py \
    tests/test_e2e_live.py \
    -q \
    -m "e2e or live"
else
  printf -- '- live-telegram: SKIP (Telethon session/API env incomplete)\n' >> "$SUMMARY"
  if [[ "$REQUIRE_LIVE" == "1" || "$RUN_LIVE" == "1" || "$RUN_LIVE" == "true" ]]; then
    echo "ERROR: live Telegram QA required, but Telethon credentials are incomplete."
    exit 20
  fi
fi

cat "$SUMMARY"
printf '\nArtifacts: %s\n' "$ARTIFACT_DIR"
