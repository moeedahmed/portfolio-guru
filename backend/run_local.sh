#!/bin/bash
# Run Portfolio Guru locally in polling mode
# Loads secrets from BWS

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
LOCK_DIR="${PORTFOLIO_GURU_BOT_LOCK:-/tmp/portfolio-guru-bot.lock}"

while ! mkdir "$LOCK_DIR" 2>/dev/null; do
  EXISTING_PID="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [ -n "$EXISTING_PID" ] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    EXISTING_CWD="$(lsof -a -p "$EXISTING_PID" -d cwd 2>/dev/null | awk 'NR==2 {print $NF}')"
    if [ "$EXISTING_CWD" = "$SCRIPT_DIR" ]; then
      echo "Portfolio Guru bot already running as PID $EXISTING_PID; holding launcher open"
      while kill -0 "$EXISTING_PID" 2>/dev/null; do
        sleep 60
      done
      exit 0
    fi
  fi
  rm -rf "$LOCK_DIR"
done
echo "$$" > "$LOCK_DIR/pid"

echo "Loading secrets from BWS..."
BWS_ACCESS_TOKEN=$(cat ~/.openclaw/.bws-token)
BWS_BIN=$(command -v bws 2>/dev/null || echo "/Users/moeedahmed/.cargo/bin/bws")

if [ ! -x "$BWS_BIN" ]; then
  echo "bws not found — install Bitwarden Secrets Manager CLI" >&2
  exit 1
fi

get_secret() {
  local id="$1"
  BWS_ACCESS_TOKEN=$BWS_ACCESS_TOKEN "$BWS_BIN" secret get "$id" --output json | python3 -c "import json,sys; print(json.load(sys.stdin)['value'])"
}

try_secret() {
  local id="$1"
  BWS_ACCESS_TOKEN=$BWS_ACCESS_TOKEN "$BWS_BIN" secret get "$id" --output json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['value'])" 2>/dev/null || true
}

get_mapped_secret() {
  local key="$1"
  local map_path="${OPENCLAW_SECRETS_MAP:-$HOME/.openclaw/workspace/secrets.json}"
  local id
  id="$(python3 - "$map_path" "$key" <<'PY'
import json
import sys
path, key = sys.argv[1], sys.argv[2]
entry = json.load(open(path))["credentials"][key]
print(entry.get("bwsId") or entry.get("bws_secret_id") or "")
PY
)"
  if [ -z "$id" ]; then
    echo "No BWS id mapped for $key" >&2
    exit 1
  fi
  get_secret "$id"
}

TELEGRAM_BOT_TOKEN="$(get_secret af553b7d-5c05-418a-b80e-b405015708ed)"
export TELEGRAM_BOT_TOKEN
GOOGLE_API_KEY="$(get_secret af6579a0-2cbe-4cef-94b3-b405017b48fe)"
export GOOGLE_API_KEY
echo "Google key loaded for OCR/voice utilities (last4): ${GOOGLE_API_KEY: -4}"
export PORTFOLIO_GURU_EXTRACTOR_PROVIDER="deepseek-v4-flash"
export GEMINI_3_5_FLASH_MODEL="${GEMINI_3_5_FLASH_MODEL:-gemini-3.5-flash}"
export PG_GATHERING_MODE="${PG_GATHERING_MODE:-1}"
echo "Model: extractor=$PORTFOLIO_GURU_EXTRACTOR_PROVIDER fallback=$GEMINI_3_5_FLASH_MODEL"
FERNET_SECRET_KEY="$(get_secret 9e653679-9a33-4c23-a15c-b405015713de)"
export FERNET_SECRET_KEY
# OpenAI keys not in use — extractor uses DeepSeek V4 Flash
# DEEPSEEK_API_KEY_PORTFOLIO is loaded below
DEEPSEEK_API_KEY="$(get_secret c5d82503-3d1d-427b-9be1-b44e01564203)"
export DEEPSEEK_API_KEY

# OpenAI keys — NOT loaded unless explicitly requested
# if [ -n "$OPENAI_API_KEY" ]; then
#   export OPENAI_API_KEY
# fi
# Stripe (Portfolio Guru account)
STRIPE_SECRET_KEY="$(get_secret 4450d6ac-f7a2-4802-a27a-b428006488c9)"
export STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET="$(get_secret 3ffc5e11-f4d6-4ff8-872f-b428006e7126)"
export STRIPE_WEBHOOK_SECRET
export STRIPE_PRO_PRICE_ID="price_1TKY11FtxKHU39UdHFXn1yur"
export STRIPE_PRO_PLUS_PRICE_ID="price_1TKY12FtxKHU39UdTQZY8rOq"
PORTFOLIO_INBOUND_SECRET="${PORTFOLIO_INBOUND_SECRET:-$(get_mapped_secret PORTFOLIO_INBOUND_SECRET)}"
export PORTFOLIO_INBOUND_SECRET

# Persistent browser for Kaizen filing (login once, reuse session)
export KAIZEN_USE_CDP="${KAIZEN_USE_CDP:-1}"
export KAIZEN_CDP_URL="${KAIZEN_CDP_URL:-http://localhost:18800}"
"$SCRIPT_DIR/ensure_chrome.sh" --verbose

echo "Secrets loaded. Starting bot + webhook server..."

PYTHON=""
if [ -x "./.venv/bin/python3" ]; then
  PYTHON="./.venv/bin/python3"
elif [ -x "./venv/bin/python3" ]; then
  PYTHON="./venv/bin/python3"
else
  echo "Python venv not found (expected backend/venv or backend/.venv)." >&2
  exit 1
fi

# Start Stripe webhook server in background (port 8099)
WEBHOOK_PORT_PIDS="$(lsof -tiTCP:8099 -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$WEBHOOK_PORT_PIDS" ]; then
  kill $WEBHOOK_PORT_PIDS 2>/dev/null || true
  sleep 1
  kill -9 $WEBHOOK_PORT_PIDS 2>/dev/null || true
fi
$PYTHON -m uvicorn webhook_server:app --port 8099 --log-level warning &
WEBHOOK_PID=$!
echo "Webhook server started (PID $WEBHOOK_PID, port 8099)"

# Clean up webhook server when bot exits
trap "kill $WEBHOOK_PID 2>/dev/null" EXIT

# Start bot (foreground)
exec $PYTHON bot.py
