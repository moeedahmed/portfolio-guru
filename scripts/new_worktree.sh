#!/usr/bin/env bash
#
# Create a ready-to-work worktree for Portfolio Guru.
#
# Why this exists
# ---------------
# Git branch state is per-directory, so two people (or two agent sessions)
# cannot work in one checkout without fighting over HEAD. Worktrees solve that,
# but a fresh worktree could not run anything: backend/venv and backend/.env are
# both gitignored, so tests failed and the bot would not start until someone
# bootstrapped them by hand. That friction is what pushed everyone back into a
# single shared directory, which is how three sessions ended up colliding on
# 2026-08-25 and an auto-sync robot committed half-written files.
#
# This script removes the friction: one command, a working checkout.
#
#   scripts/new_worktree.sh health-report            # branch off origin/main
#   scripts/new_worktree.sh hotfix some/existing     # check out an existing branch
#
# Not for the live deployment. /Users/moeedahmed/projects/portfolio-guru-live
# keeps its own private venv on purpose: a shared environment means one worktree
# upgrading a dependency can break production without a deploy.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKTREE_PARENT="${PG_WORKTREE_PARENT:-$HOME/projects}"
SHARED_STATE="${PG_SHARED_STATE:-$HOME/.local/share/portfolio-guru}"
SHARED_VENV="$SHARED_STATE/venv"
SHARED_ENV="$SHARED_STATE/backend.env"

name="${1:-}"
branch="${2:-}"
if [[ -z "$name" ]]; then
  echo "usage: scripts/new_worktree.sh <short-name> [existing-branch]" >&2
  exit 64
fi

target="$WORKTREE_PARENT/portfolio-guru-$name"
if [[ -e "$target" ]]; then
  echo "ERROR: $target already exists." >&2
  exit 1
fi

mkdir -p "$SHARED_STATE"
chmod 700 "$SHARED_STATE"

# ── Shared environment file ──────────────────────────────────────────────────
# Seeded once from whichever checkout already has it. Kept outside every
# worktree so a `git clean` or a deleted worktree cannot destroy it.
if [[ ! -f "$SHARED_ENV" ]]; then
  if [[ -f "$REPO_ROOT/backend/.env" ]]; then
    cp "$REPO_ROOT/backend/.env" "$SHARED_ENV"
    chmod 600 "$SHARED_ENV"
    echo "Seeded shared backend/.env from $REPO_ROOT"
  else
    echo "WARNING: no backend/.env found to seed. Tests needing FERNET_SECRET_KEY will fail." >&2
  fi
fi

# ── Shared virtualenv ────────────────────────────────────────────────────────
# One environment for all dev worktrees. A per-worktree venv is ~630 MB and
# several minutes each, which is enough friction that people skip worktrees
# altogether. Refresh it with: PG_REFRESH_VENV=1 scripts/new_worktree.sh ...
if [[ ! -x "$SHARED_VENV/bin/python3" ]]; then
  echo "Creating shared virtualenv at $SHARED_VENV (first run only)…"
  python3 -m venv "$SHARED_VENV"
  "$SHARED_VENV/bin/python3" -m pip install --quiet --upgrade pip
  PG_REFRESH_VENV=1
fi
if [[ -n "${PG_REFRESH_VENV:-}" ]]; then
  echo "Installing dependencies…"
  "$SHARED_VENV/bin/python3" -m pip install --quiet -r "$REPO_ROOT/backend/requirements.txt"
fi

# ── The worktree itself ──────────────────────────────────────────────────────
git -C "$REPO_ROOT" fetch --quiet origin main || true
if [[ -n "$branch" ]]; then
  git -C "$REPO_ROOT" worktree add "$target" "$branch"
else
  branch="work/$name"
  git -C "$REPO_ROOT" worktree add -b "$branch" "$target" origin/main
fi

ln -s "$SHARED_VENV" "$target/backend/venv"
[[ -f "$SHARED_ENV" ]] && ln -s "$SHARED_ENV" "$target/backend/.env"

echo
echo "Ready: $target  (branch $branch)"
echo "  cd $target && bash scripts/verify_changed.sh"
echo
echo "When finished:  git -C $REPO_ROOT worktree remove $target"
