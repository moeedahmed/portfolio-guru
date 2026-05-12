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

collect_app_pids() {
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    if lsof -a -p "$pid" -d cwd 2>/dev/null | grep -q "${APP_DIR}/backend"; then
      echo "$pid"
    fi
  done < <(pgrep -f "bot.py|webhook_server:app" || true)
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    if ps -p "$pid" -o command= | grep -q "${APP_DIR}"; then
      echo "$pid"
    fi
  done < <(pgrep -f "start-bot.sh|start_bot.sh|run_local.sh" || true)
}

app_pids="$(collect_app_pids | sort -u | tr '\n' ' ')"
if [[ -n "$app_pids" ]]; then
  # shellcheck disable=SC2086
  kill $app_pids 2>/dev/null || true
fi
sleep 5
app_pids="$(collect_app_pids | sort -u | tr '\n' ' ')"
if [[ -n "$app_pids" ]]; then
  # shellcheck disable=SC2086
  kill -9 $app_pids 2>/dev/null || true
fi
sleep 1

launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/${SERVICE_LABEL}" 2>/dev/null || true

sleep 3

service_pid="$(launchctl print "gui/$(id -u)/${SERVICE_LABEL}" | awk '/pid =/ {print $3; exit}')"
while read -r pid; do
  [[ -z "$pid" || "$pid" == "$service_pid" ]] && continue
  if lsof -a -p "$pid" -d cwd 2>/dev/null | grep -q "${APP_DIR}/backend"; then
    kill "$pid" 2>/dev/null || true
  fi
done < <(pgrep -f "bot.py" || true)
sleep 2

echo "launchd service:"
launchctl print "gui/$(id -u)/${SERVICE_LABEL}" | sed -n '1,25p'

echo "Running Portfolio Guru processes:"
pgrep -fl "/portfolio-guru/backend|webhook_server:app|bot.py" || true

echo "Deploy complete: $(git -C "$APP_DIR" rev-parse --short HEAD)"
