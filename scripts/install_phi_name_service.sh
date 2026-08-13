#!/usr/bin/env bash
# Install the local name-detection sidecar as a launchd service.
#
# Mirrors scripts/install_launchd.sh. Kept separate from the bot service so the
# model can be restarted, upgraded, or stopped without touching filing.
set -euo pipefail

APP_DIR="${PORTFOLIO_GURU_APP_DIR:-/Users/moeedahmed/projects/portfolio-guru}"
SERVICE_LABEL="${PHI_NAME_SERVICE_LABEL:-com.portfolioguru.phiname}"
PLIST_PATH="${HOME}/Library/LaunchAgents/${SERVICE_LABEL}.plist"
SERVICE_DIR="${APP_DIR}/services/phi-name"
LOG="${PHI_NAME_LOG:-/tmp/portfolio-guru-phi-name.log}"
PORT="${PHI_NAME_PORT:-18810}"

if [[ ! -d "$SERVICE_DIR" ]]; then
  echo "ERROR: $SERVICE_DIR not found."
  exit 1
fi

# Own venv. These dependencies must never reach backend/requirements.txt.
if [[ ! -x "${SERVICE_DIR}/.venv/bin/python3" ]]; then
  echo "Creating venv for the name service (this pulls torch — a few minutes)."
  python3 -m venv "${SERVICE_DIR}/.venv"
fi
"${SERVICE_DIR}/.venv/bin/python3" -m pip install -q --upgrade pip
"${SERVICE_DIR}/.venv/bin/python3" -m pip install -q -r "${SERVICE_DIR}/requirements.txt"

mkdir -p "$(dirname "$PLIST_PATH")"

# Bound to 127.0.0.1 only. This service sees clinical text before
# de-identification completes; it must never be reachable off-box.
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${SERVICE_LABEL}</string>

  <key>WorkingDirectory</key>
  <string>${SERVICE_DIR}</string>

  <key>ProgramArguments</key>
  <array>
    <string>${SERVICE_DIR}/.venv/bin/python3</string>
    <string>-m</string>
    <string>uvicorn</string>
    <string>app:app</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>${PORT}</string>
    <string>--log-level</string>
    <string>warning</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>${LOG}</string>

  <key>StandardErrorPath</key>
  <string>${LOG}</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/${SERVICE_LABEL}" 2>/dev/null || true

echo "Installed ${SERVICE_LABEL} on 127.0.0.1:${PORT}"
echo "Log: ${LOG}"
echo
echo "The bot ignores this service until PG_ENABLE_MODEL_NAME_SCRUB=1 is set."
echo "Check it with: curl -s http://127.0.0.1:${PORT}/health"
