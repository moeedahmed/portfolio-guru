#!/usr/bin/env bash
# filing-fix-cron.sh — Safety-gated autonomous fix loop for Portfolio Guru
#
# Reads the latest unhandled filing result and applies FORM_FIELD_MAP fixes
# for deterministic DOM/gap issues.
#
# Safety rules (self-enforced):
#   - Hard timeout: 60 seconds (KILL_TIMEOUT_SEC)
#   - Max one form type per run (the auto_fix module caps at 3 gaps)
#   - Emergency ceiling: module refuses fixes if >10 applied today
#   - Always --dry-run first unless FORCE=1 is set
#   - Logs everything to stderr
#   - Non-zero exit if anything went wrong
#
# Usage:
#   ./filing-fix-cron.sh              # safe mode: dry-run first, then apply
#   FORCE=1 ./filing-fix-cron.sh      # skip dry-run, apply directly
#   DRY_ONLY=1 ./filing-fix-cron.sh   # only dry-run, never apply
#
# Designed to be called from OpenClaw cron (agentTurn, isolated session).

set -uo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly BACKEND_DIR="$SCRIPT_DIR/../backend"
readonly VENV_DIR="$BACKEND_DIR/venv"
readonly KILL_TIMEOUT_SEC=60
readonly PYTHON_BIN="$VENV_DIR/bin/python3"

# ── Self-kill timer ──
(
  sleep "$KILL_TIMEOUT_SEC"
  echo "[filing-fix-cron] FATAL: exceeded ${KILL_TIMEOUT_SEC}s timeout — killing."
  kill "$$" 2>/dev/null
) &
KILLER_PID=$!

cleanup() {
  kill "$KILLER_PID" 2>/dev/null || true
}
trap cleanup EXIT

# ── Guard: cd to backend ──
cd "$BACKEND_DIR" || {
  echo "[filing-fix-cron] ERROR: cannot cd to $BACKEND_DIR" >&2
  exit 1
}

# ── Guard: venv exists ──
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[filing-fix-cron] ERROR: python3 not found at $PYTHON_BIN" >&2
  exit 1
fi

# ── Dry-run first (unless FORCE=1) ──
DRY_RUN_FLAG="--dry-run"
if [[ "${FORCE:-}" == "1" ]]; then
  DRY_RUN_FLAG=""
fi

if [[ "${DRY_ONLY:-}" == "1" ]]; then
  echo "[filing-fix-cron] DRY-RUN only (DRY_ONLY=1)"
  "$PYTHON_BIN" -m auto_fix_form_map --dry-run
  DRY_EXIT=$?
  exit "$DRY_EXIT"
fi

# Dry-run
echo "[filing-fix-cron] Dry-run phase..."
"$PYTHON_BIN" -m auto_fix_form_map --dry-run
DRY_EXIT=$?

if [[ $DRY_EXIT -ne 0 ]]; then
  echo "[filing-fix-cron] Dry-run failed (exit $DRY_EXIT) — not applying fixes." >&2
  exit $DRY_EXIT
fi

# Apply (if gaps were found)
if [[ -n "$DRY_RUN_FLAG" ]]; then
  echo "[filing-fix-cron] Apply phase..."
  "$PYTHON_BIN" -m auto_fix_form_map
  APPLY_EXIT=$?
else
  echo "[filing-fix-cron] FORCE=1 — skipping dry-run, applying directly..."
  "$PYTHON_BIN" -m auto_fix_form_map
  APPLY_EXIT=$?
fi

if [[ $APPLY_EXIT -eq 0 ]]; then
  echo "[filing-fix-cron] Complete."
else
  echo "[filing-fix-cron] Apply failed (exit $APPLY_EXIT)." >&2
fi

exit $APPLY_EXIT
