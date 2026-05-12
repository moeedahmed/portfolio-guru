#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
cd "$ROOT"

branch="$(git branch --show-current)"

printf 'Repo: %s\n' "$ROOT"
printf 'Branch: %s\n' "$branch"

if [[ -z "$branch" ]]; then
  echo "ERROR: detached HEAD. Create or switch to a branch before working."
  exit 1
fi

if [[ "$branch" == "main" ]]; then
  echo "ERROR: you are on main. Create a task branch before editing."
  echo "Example: git checkout -b fix/short-task-name"
  exit 1
fi

git fetch --quiet origin

upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
if [[ -n "$upstream" ]]; then
  read -r ahead behind < <(git rev-list --left-right --count HEAD..."$upstream")
  printf 'Upstream: %s, ahead=%s, behind=%s\n' "$upstream" "$ahead" "$behind"
  if [[ "$behind" != "0" ]]; then
    echo "ERROR: branch is behind upstream. Pull/rebase before continuing."
    exit 1
  fi
else
  echo "No upstream set yet. This is OK for a new local branch."
fi

tracked_changes="$(git status --short --untracked-files=no)"
if [[ -n "$tracked_changes" ]]; then
  echo
  echo "Tracked changes present:"
  echo "$tracked_changes"
else
  echo "No tracked local changes."
fi

untracked_count="$(git ls-files --others --exclude-standard | wc -l | tr -d ' ')"
printf 'Untracked files: %s\n' "$untracked_count"
if [[ "$untracked_count" != "0" ]]; then
  echo "Review untracked files before committing. Do not commit private/ticket/backup artefacts by accident."
  git ls-files --others --exclude-standard | sed 's/^/  - /' | head -50
  if [[ "$untracked_count" -gt 50 ]]; then
    echo "  ..."
  fi
fi

echo
if [[ -d backend ]]; then
  cd backend
  if [[ -x ../.venv/bin/python ]]; then
    PY="../.venv/bin/python"
  elif [[ -x .venv/bin/python ]]; then
    PY=".venv/bin/python"
  else
    PY="python3"
  fi
  echo "Running backend offline tests with $PY"
  "$PY" -m pytest tests/ -q \
    --ignore=tests/test_e2e.py \
    --ignore=tests/test_e2e_live.py \
    --ignore=tests/test_kaizen_integration.py
else
  echo "No backend directory found; skipping backend tests."
fi

echo
 echo "Preflight passed."
