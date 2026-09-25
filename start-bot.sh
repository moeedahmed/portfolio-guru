#!/bin/bash
# Portfolio Guru bot launcher.
# Restarts are handled by launchd; this script runs one foreground instance.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="${PORTFOLIO_GURU_BOT_LOG:-$HOME/.openclaw/logs/portfolio-guru/bot.log}"
MAX_LOG_BYTES="${PORTFOLIO_GURU_MAX_LOG_BYTES:-10485760}"  # 10 MB

mkdir -p "$(dirname "$LOG")"

# Rotate on size, not on every start: launchd KeepAlive restarts a crash-looping
# bot repeatedly, and rotating each time would discard the very logs explaining
# the crash.
#
# Seven generations, not one. Diagnosing a slow-burn failure (the off-device
# backup died for 53 nights before anyone noticed) needs history that outlives
# a single 10 MB window. Bounded at ~8x the threshold, on a persistent disk —
# the log used to live in /tmp, which macOS purges, so evidence disappeared
# exactly when a reboot made it most interesting.
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG" 2>/dev/null || echo 0)" -gt "$MAX_LOG_BYTES" ]; then
  for i in 6 5 4 3 2 1; do
    [ -f "${LOG}.${i}" ] && mv -f "${LOG}.${i}" "${LOG}.$((i + 1))"
  done
  mv -f "$LOG" "${LOG}.1"
fi

echo "[$(date)] Starting Portfolio Guru bot..." >> "$LOG"
cd "$SCRIPT_DIR"
exec bash backend/run_local.sh >> "$LOG" 2>&1
