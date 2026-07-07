#!/bin/bash
# ensure_chrome.sh — make sure Chrome is running with CDP on port 18800 for the
# Kaizen filer. Idempotent: if Chrome is already up, exits 0 silently.
#
# Usage:
#   ensure_chrome.sh             # check/start headless, silently
#   ensure_chrome.sh --verbose   # say what happened
#   ensure_chrome.sh --visible   # start visible Chrome for login/debug
#   ensure_chrome.sh --headless  # force headless even if env asks visible
#
# Chrome is spawned detached, so it keeps running after this script exits.
# Cookies persist in $CHROME_PROFILE across restarts — you log in to
# kaizenep.com once (via Chrome Remote Desktop) and the session sticks.
#
# First-time setup:
#   1. Run this script once with --visible.
#   2. Open the Mac Mini via Chrome Remote Desktop.
#   3. A Chrome window is visible — go to kaizenep.com and log in.
#   4. Close the Remote Desktop session; Chrome stays running.
#   5. Restart the managed browser normally; subsequent product automation
#      runs headless and reuses the logged-in profile.

set -eu

CDP_URL="${KAIZEN_CDP_URL:-http://localhost:18800}"
CHROME_PROFILE="${KAIZEN_CHROME_PROFILE:-$HOME/.kaizen-chrome-profile}"
CHROME_APP="${KAIZEN_CHROME_APP:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"

VERBOSE=0
VISIBLE=0
DRY_RUN=0

case "${KAIZEN_CHROME_VISIBLE:-}" in
    1|true|TRUE|yes|YES) VISIBLE=1 ;;
esac

while [ "$#" -gt 0 ]; do
    case "$1" in
        --verbose)
            VERBOSE=1
            ;;
        --visible)
            VISIBLE=1
            ;;
        --headless)
            VISIBLE=0
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        *)
            echo "ensure_chrome.sh: unknown option $1" >&2
            exit 2
            ;;
    esac
    shift
done

log() {
    [ "$VERBOSE" -eq 1 ] && echo "$@" >&2
    return 0
}

# Probe CDP: a healthy Chrome with --remote-debugging-port responds to /json/version
probe_cdp() {
    curl -sfL --max-time 2 "$CDP_URL/json/version" > /dev/null 2>&1
}

if probe_cdp; then
    log "Chrome already responding on $CDP_URL"
    exit 0
fi

log "Chrome not running on $CDP_URL — starting..."

if [ ! -x "$CHROME_APP" ]; then
    echo "ensure_chrome.sh: Chrome not found at $CHROME_APP" >&2
    exit 1
fi

mkdir -p "$CHROME_PROFILE"

# Port parsing: extract port number from CDP_URL (e.g. http://localhost:18800 -> 18800)
PORT="${CDP_URL##*:}"
PORT="${PORT%%/*}"

CHROME_ARGS=(
    "--remote-debugging-port=$PORT"
    "--user-data-dir=$CHROME_PROFILE"
    "--no-first-run"
    "--no-default-browser-check"
    "--password-store=basic"
)

if [ "$VISIBLE" -eq 0 ]; then
    CHROME_ARGS+=(
        "--headless=new"
        "--disable-gpu"
        "--window-size=1440,1100"
    )
fi

if [ "$DRY_RUN" -eq 1 ]; then
    printf '%q' "$CHROME_APP"
    printf ' %q' "${CHROME_ARGS[@]}"
    printf '\n'
    exit 0
fi

# Spawn Chrome detached — survives this script exiting. Headless is the product
# default; --visible is reserved for user-present login and debugging.
nohup "$CHROME_APP" "${CHROME_ARGS[@]}" > /dev/null 2>&1 &
disown

# Wait up to 30s for CDP endpoint to come up
for i in $(seq 1 30); do
    if probe_cdp; then
        log "Chrome started (~${i}s) on $CDP_URL — profile at $CHROME_PROFILE"
        if [ ! -s "$CHROME_PROFILE/Default/Cookies" ]; then
            log "No cookies yet — log in to kaizenep.com via Chrome Remote Desktop to persist the session."
        fi
        exit 0
    fi
    sleep 1
done

echo "ensure_chrome.sh: Chrome failed to bind to $CDP_URL within 30s" >&2
exit 1
