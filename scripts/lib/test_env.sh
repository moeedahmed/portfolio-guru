# shellcheck shell=bash
#
# scripts/lib/test_env.sh — shared setup for the offline test gates.
#
# Source this from inside backend/ before running pytest:
#
#   . "$ROOT/scripts/lib/test_env.sh"
#   pg_setup_test_env
#   "$PY" -m pytest ...
#
# It exists because two environment problems used to surface as a wall of
# unrelated test failures rather than as themselves:
#
#   1. FERNET_SECRET_KEY unset -> 14 failures across test_health_bot.py,
#      test_kaizen_login_reliability.py and test_flow_walker.py, all raised
#      from backend/credentials.py as "FERNET_SECRET_KEY env var not set".
#      The variable normally arrives via backend/run_local.sh (from BWS), so
#      anyone running pytest directly does not get it. CI sets its own
#      throwaway key, so CI stayed green and the gap went unnoticed.
#
#   2. No backend/venv -> the gates silently fell back to system python3 and
#      died during collection on a missing `telegram` module. Git worktrees
#      under .claude/worktrees/ hit this every time, because the virtualenv
#      lives in the main checkout only.
#
# Both now fail fast, or are handled, with one line that names the cause.

pg_resolve_python() {
  if [[ -x venv/bin/python3 ]]; then
    PY="venv/bin/python3"
  elif [[ -x venv/bin/python ]]; then
    PY="venv/bin/python"
  elif [[ -x .venv/bin/python3 ]]; then
    PY=".venv/bin/python3"
  elif [[ -x .venv/bin/python ]]; then
    PY=".venv/bin/python"
  elif [[ -x ../.venv/bin/python ]]; then
    PY="../.venv/bin/python"
  else
    PY="python3"
  fi
  export PY
}

# Fail before pytest rather than during collection, and say what to do about it.
pg_require_test_deps() {
  local missing
  missing="$("$PY" - <<'PY' 2>/dev/null || true
import importlib.util
print(",".join(m for m in ("telegram", "cryptography", "pytest")
                if not importlib.util.find_spec(m)))
PY
)"
  if [[ -z "$missing" ]]; then
    return 0
  fi

  echo "ERROR: $PY cannot import: $missing" >&2
  echo "       The backend virtualenv was not found, so the gate fell back to system python." >&2

  # In a worktree the virtualenv lives in the main checkout, not here.
  local common_dir main_checkout
  common_dir="$(git rev-parse --git-common-dir 2>/dev/null || true)"
  if [[ -n "$common_dir" && "$common_dir" != "$(git rev-parse --git-dir 2>/dev/null)" ]]; then
    main_checkout="$(cd "$(dirname "$common_dir")" && pwd)"
    echo >&2
    echo "       This is a git worktree. Link the main checkout's virtualenv:" >&2
    echo "         ln -sfn $main_checkout/backend/venv $(pwd)/venv" >&2
  else
    echo >&2
    echo "       Create it with: python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
  fi
  return 1
}

# credentials.py reads FERNET_SECRET_KEY at import time, so it must be exported
# before pytest starts. A real key is used when present; otherwise the offline
# gates get a fresh per-run key, which is all they need — these tests encrypt
# and decrypt within the same process and never touch stored credentials.
pg_ensure_fernet_key() {
  if [[ -n "${FERNET_SECRET_KEY:-}" ]]; then
    return 0
  fi
  FERNET_SECRET_KEY="$("$PY" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  export FERNET_SECRET_KEY
  echo "FERNET_SECRET_KEY was unset — generated an ephemeral key for this test run."
  echo "  (For the real key, run via backend/run_local.sh, which loads FERNET_SECRET_KEY_PORTFOLIO from BWS.)"
}

pg_setup_test_env() {
  pg_resolve_python
  pg_require_test_deps || return 1
  pg_ensure_fernet_key
}
