#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${PORTFOLIO_GURU_APP_DIR:-/Users/moeedahmed/projects/portfolio-guru}"
SERVICE_LABEL="${PORTFOLIO_GURU_SERVICE_LABEL:-com.portfolioguru.bot}"
PLIST_PATH="${HOME}/Library/LaunchAgents/${SERVICE_LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs/portfolio-guru"

mkdir -p "$(dirname "$PLIST_PATH")" "$LOG_DIR"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${SERVICE_LABEL}</string>

  <key>WorkingDirectory</key>
  <string>${APP_DIR}</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${APP_DIR}/start-bot.sh</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>${LOG_DIR}/launchd.out.log</string>

  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/launchd.err.log</string>

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
launchctl kickstart -k "gui/$(id -u)/${SERVICE_LABEL}"

echo "Installed and started ${SERVICE_LABEL}"
echo "Plist: ${PLIST_PATH}"
launchctl print "gui/$(id -u)/${SERVICE_LABEL}" | sed -n '1,25p'
