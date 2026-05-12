#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${PORTFOLIO_GURU_APP_DIR:-/Users/moeedahmed/projects/portfolio-guru}"
SERVICE_LABEL="${PORTFOLIO_GURU_SERVICE_LABEL:-com.portfolioguru.bot}"
PLIST_PATH="${HOME}/Library/LaunchAgents/${SERVICE_LABEL}.plist"

cd "$APP_DIR"

echo "Deploying Portfolio Guru from $APP_DIR"
echo "Current commit: $(git rev-parse --short HEAD)"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: deployment checkout has local changes. Refusing to deploy."
  git status --short
  exit 1
fi

git fetch origin main
git checkout main
git pull --ff-only origin main

echo "Updated commit: $(git rev-parse --short HEAD)"

cd "$APP_DIR/backend"
PYTHON=""
if [[ -x "./.venv/bin/python3" ]]; then
  PYTHON="./.venv/bin/python3"
elif [[ -x "./venv/bin/python3" ]]; then
  PYTHON="./venv/bin/python3"
else
  echo "ERROR: Python venv not found (expected backend/.venv or backend/venv)."
  exit 1
fi

"$PYTHON" -m pip install -q -r requirements.txt
"$PYTHON" -m py_compile bot.py

if [[ ! -f "$PLIST_PATH" ]]; then
  echo "ERROR: launchd plist not installed at $PLIST_PATH"
  echo "Run: scripts/install_launchd.sh"
  exit 1
fi

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
pkill -f "${APP_DIR}/backend/.*bot.py" 2>/dev/null || true
pkill -f "${APP_DIR}/backend/.*webhook_server:app" 2>/dev/null || true
pkill -f "cd ${APP_DIR}.*start_bot.sh" 2>/dev/null || true
pkill -f "${APP_DIR}/start-bot.sh" 2>/dev/null || true
pkill -f "${APP_DIR}/start_bot.sh" 2>/dev/null || true
sleep 2
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/${SERVICE_LABEL}" 2>/dev/null || true
launchctl kickstart -k "gui/$(id -u)/${SERVICE_LABEL}"

sleep 3

echo "launchd service:"
launchctl print "gui/$(id -u)/${SERVICE_LABEL}" | sed -n '1,25p'

echo "Running Portfolio Guru processes:"
pgrep -fl "/portfolio-guru/backend|webhook_server:app|bot.py" || true

echo "Deploy complete: $(git -C "$APP_DIR" rev-parse --short HEAD)"
