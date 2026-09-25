#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${PORTFOLIO_GURU_APP_DIR:-/Users/moeedahmed/projects/portfolio-guru-live}"
SERVICE_LABEL="${PORTFOLIO_GURU_SERVICE_LABEL:-com.portfolioguru.bot}"
PLIST_PATH="${HOME}/Library/LaunchAgents/${SERVICE_LABEL}.plist"
# start-bot.sh redirects the whole service (secrets, Chrome, webhook, bot) into
# this one file, so launchd must point here too. Pointing launchd at a separate
# path created a file that never received a byte — and a debugger tailing it got
# months-old errors that read as current.
BOT_LOG="${PORTFOLIO_GURU_BOT_LOG:-$HOME/.openclaw/logs/portfolio-guru/bot.log}"

mkdir -p "$(dirname "$PLIST_PATH")"

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
  <string>${BOT_LOG}</string>

  <key>StandardErrorPath</key>
  <string>${BOT_LOG}</string>

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
