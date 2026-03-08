#!/bin/bash
# Persistent bot launcher — run this directly in a terminal or via systemd
# Keeps the bot alive; restarts on crash with 5s delay

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG=/tmp/portfolio-guru-bot.log

echo "[$(date)] Starting Portfolio Guru bot..." >> $LOG

while true; do
    cd "$SCRIPT_DIR"
    bash backend/run_local.sh >> $LOG 2>&1
    EXIT=$?
    echo "[$(date)] Bot exited (code $EXIT). Restarting in 5s..." >> $LOG
    sleep 5
done
