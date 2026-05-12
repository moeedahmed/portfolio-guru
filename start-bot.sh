#!/bin/bash
# Portfolio Guru bot launcher.
# Restarts are handled by launchd; this script runs one foreground instance.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG=/tmp/portfolio-guru-bot.log

echo "[$(date)] Starting Portfolio Guru bot..." >> "$LOG"
cd "$SCRIPT_DIR"
exec bash backend/run_local.sh >> "$LOG" 2>&1
