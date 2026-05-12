#!/bin/bash
# Run Portfolio Guru locally in polling mode
# Loads secrets from BWS

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK_FILE="${PORTFOLIO_GURU_BOT_LOCK:-/tmp/portfolio-guru-bot.pid}"

if [ -f "$LOCK_FILE" ]; then
  EXISTING_PID="$(cat "$LOCK_FILE" 2>/dev/null || true)"
  if [ -n "$EXISTING_PID" ] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    EXISTING_CWD="$(lsof -a -p "$EXISTING_PID" -d cwd 2>/dev/null | awk 'NR==2 {print $NF}')"
    EXISTING_CMD="$(ps -p "$EXISTING_PID" -o command= 2>/dev/null || true)"
    if [ "$EXISTING_CWD" = "$SCRIPT_DIR" ] && echo "$EXISTING_CMD" | grep -q "bot.py"; then
      echo "Portfolio Guru bot already running as PID $EXISTING_PID"
      exit 0
    fi
  fi
fi
echo "$$" > "$LOCK_FILE"

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

TELEGRAM_BOT_TOKEN="$(get_secret af553b7d-5c05-418a-b80e-b405015708ed)"
export TELEGRAM_BOT_TOKEN
GOOGLE_API_KEY="$(get_secret af6579a0-2cbe-4cef-94b3-b405017b48fe)"
export GOOGLE_API_KEY
echo "Gemini key in use (last4): ${GOOGLE_API_KEY: -4}"
FERNET_SECRET_KEY="$(get_secret 9e653679-9a33-4c23-a15c-b405015713de)"
export FERNET_SECRET_KEY
OPENAI_API_KEY="$(try_secret 2772c5c3-b357-4015-8252-b3ea00939469)"
if [ -n "$OPENAI_API_KEY" ]; then
  export OPENAI_API_KEY
fi
DEEPSEEK_API_KEY="$(get_secret 1628cc03-0446-4455-b801-b3eb014c82fb)"
export DEEPSEEK_API_KEY
# Stripe (Portfolio Guru account)
STRIPE_SECRET_KEY="$(get_secret 4450d6ac-f7a2-4802-a27a-b428006488c9)"
export STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET="$(get_secret 3ffc5e11-f4d6-4ff8-872f-b428006e7126)"
export STRIPE_WEBHOOK_SECRET
export STRIPE_PRO_PRICE_ID="price_1TKY11FtxKHU39UdHFXn1yur"
export STRIPE_PRO_PLUS_PRICE_ID="price_1TKY12FtxKHU39UdTQZY8rOq"

# Optional: persistent browser for faster filing (login once, reuse session)
# Requires Chrome running with: google-chrome --remote-debugging-port=18800 --user-data-dir=/tmp/kaizen-profile
# export KAIZEN_USE_CDP=1
# export KAIZEN_CDP_URL=http://localhost:18800

echo "Secrets loaded. Starting bot + webhook server..."
cd "$SCRIPT_DIR"

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
$PYTHON -m uvicorn webhook_server:app --port 8099 --log-level warning &
WEBHOOK_PID=$!
echo "Webhook server started (PID $WEBHOOK_PID, port 8099)"

# Clean up webhook server when bot exits
trap "kill $WEBHOOK_PID 2>/dev/null" EXIT

# Start bot (foreground)
exec $PYTHON bot.py
