#!/bin/bash
# Portfolio Guru bot launcher.
# Restarts are handled by launchd; this script runs one foreground instance.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="${PORTFOLIO_GURU_BOT_LOG:-/tmp/portfolio-guru-bot.log}"
MAX_LOG_BYTES="${PORTFOLIO_GURU_MAX_LOG_BYTES:-10485760}"  # 10 MB

# Rotate on size, not on every start: launchd KeepAlive restarts a crash-looping
# bot repeatedly, and rotating each time would discard the very logs explaining
# the crash. One generation is kept, so the log is bounded at ~2x the threshold.
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt "$MAX_LOG_BYTES" ]; then
  mv -f "$LOG" "${LOG}.1"
fi

echo "[$(date)] Starting Portfolio Guru bot..." >> "$LOG"
cd "$SCRIPT_DIR"
exec bash backend/run_local.sh >> "$LOG" 2>&1
