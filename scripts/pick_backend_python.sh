#!/usr/bin/env bash
#
# scripts/pick_backend_python.sh
#
# Prints the Python interpreter the offline gates should use. Sourced or
# called by verify_changed.sh / verify_release.sh so both agree.
#
# Order: this checkout's backend venv, then the main checkout's venv (git
# worktrees don't get their own), then bare python3 (how CI runs, where deps
# are installed into the job's interpreter).
#
# Usage: PY="$(bash scripts/pick_backend_python.sh)"

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"

for candidate in \
  "$ROOT/backend/venv/bin/python3" \
  "$ROOT/backend/.venv/bin/python3"
do
  if [[ -x "$candidate" ]]; then
    echo "$candidate"
    exit 0
  fi
done

# In a worktree, --git-common-dir points at the main checkout's .git
MAIN_ROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
if [[ "$MAIN_ROOT" != "$ROOT" ]]; then
  for candidate in \
    "$MAIN_ROOT/backend/venv/bin/python3" \
    "$MAIN_ROOT/backend/.venv/bin/python3"
  do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      exit 0
    fi
  done
fi

echo "python3"
