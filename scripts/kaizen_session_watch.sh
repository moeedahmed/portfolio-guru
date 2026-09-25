#!/usr/bin/env bash
# Measure how long a passwordless Kaizen session survives. Silent: it sends no
# messages to anyone, it only records results.
#
# Run by launchd every 4 hours (com.moeed.portfolio-guru-kaizen-session-watch).
# Each run appends one line via `kaizen_passwordless_proof.py status` to
# ~/.openclaw/data/portfolio-guru/mobile-handoff/session-lifetime.jsonl. The
# first time the session is found expired, a marker is written and later runs
# stop checking. Delete the marker to measure a fresh session.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USER_ID="${PG_WATCH_USER_ID:-6912896590}"
RUNTIME_DIR="$HOME/.openclaw/data/portfolio-guru/mobile-handoff"
MARKER="$RUNTIME_DIR/session-expired"
LOG="$RUNTIME_DIR/session-watch.log"

mkdir -p "$RUNTIME_DIR"
[ -f "$MARKER" ] && exit 0

"$REPO_ROOT/backend/venv/bin/python3" "$REPO_ROOT/scripts/kaizen_passwordless_proof.py" \
  status --user-id "$USER_ID" >>"$LOG" 2>&1
result=$?
echo "$(date '+%Y-%m-%d %H:%M:%S') status exit=$result" >>"$LOG"
if [ "$result" -eq 2 ]; then
  date '+%Y-%m-%d %H:%M:%S' >"$MARKER"
fi
exit 0
