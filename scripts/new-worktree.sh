#!/usr/bin/env bash
#
# Create a ready-to-use worktree for portfolio-guru development.
#
# The live checkout at ~/projects/portfolio-guru is what the bot serves users
# from. deploy_mac.sh refuses to deploy it dirty, so any uncommitted work left
# there blocks everyone's releases — and a launchd crash-restart can serve
# whatever is on disk. With ~11 concurrent agent sessions on this Mac, that is
# a matter of when, not if.
#
# The reason people skip worktrees is friction: a fresh worktree has no
# virtualenv and no backend/.env, so every test touching FERNET_SECRET_KEY
# fails in a way that reads as broken code rather than a missing file. This
# script removes that excuse.
#
# Usage:  scripts/new-worktree.sh <branch-name> [base]
#
set -euo pipefail

BRANCH="${1:?usage: new-worktree.sh <branch-name> [base]}"
BASE="${2:-origin/main}"
LIVE="${PORTFOLIO_GURU_APP_DIR:-$HOME/projects/portfolio-guru}"
DEST="${PG_WORKTREE_DIR:-$HOME/projects/pg-worktrees}/$BRANCH"

if [ -e "$DEST" ]; then
  echo "ERROR: $DEST already exists. Pick another name or remove it:" >&2
  echo "  git -C '$LIVE' worktree remove '$DEST'" >&2
  exit 1
fi

git -C "$LIVE" fetch origin --quiet
mkdir -p "$(dirname "$DEST")"
git -C "$LIVE" worktree add -b "$BRANCH" "$DEST" "$BASE"

# Symlink, never copy: backend/.env holds FERNET_SECRET_KEY and must not be
# duplicated around the disk.
ln -sfn "$LIVE/backend/venv" "$DEST/backend/venv"
[ -f "$LIVE/backend/.env" ] && ln -sfn "$LIVE/backend/.env" "$DEST/backend/.env"

cat <<MSG

Worktree ready: $DEST
  branch: $BRANCH  (from $BASE)
  linked: backend/venv, backend/.env

  cd "$DEST"
  bash scripts/verify_changed.sh

When finished (after pushing):
  git -C "$LIVE" worktree remove "$DEST"
MSG
