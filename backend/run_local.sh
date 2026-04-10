#!/bin/bash
# Run Portfolio Guru locally in polling mode
# Loads secrets from BWS

set -e

echo "Loading secrets from BWS..."
BWS_ACCESS_TOKEN=$(cat ~/.openclaw/.bws-token)
BWS_BIN=$(command -v bws)

if [ -z "$BWS_BIN" ]; then
  echo "bws not found on PATH — install Bitwarden Secrets Manager CLI or fix PATH" >&2
  exit 1
fi

get_secret() {
  local id="$1"
  BWS_ACCESS_TOKEN=$BWS_ACCESS_TOKEN "$BWS_BIN" secret get "$id" --output json | python3 -c "import json,sys; print(json.load(sys.stdin)['value'])"
}

export TELEGRAM_BOT_TOKEN=$(get_secret af553b7d-5c05-418a-b80e-b405015708ed)
export GOOGLE_API_KEY=$(get_secret af6579a0-2cbe-4cef-94b3-b405017b48fe)
echo "Gemini key in use (last4): ${GOOGLE_API_KEY: -4}"
export FERNET_SECRET_KEY=$(get_secret 9e653679-9a33-4c23-a15c-b405015713de)
export OPENAI_API_KEY=$(get_secret 2772c5c3-b357-4015-8252-b3ea00939469)
export DEEPSEEK_API_KEY=$(get_secret 1628cc03-0446-4455-b801-b3eb014c82fb)
# Stripe — set STRIPE_BWS_ID in env or add to credentials-map.json once created in Stripe dashboard
export STRIPE_SECRET_KEY="${STRIPE_SECRET_KEY:-$(get_secret "${STRIPE_BWS_ID:-placeholder}" 2>/dev/null || echo "")}"
export STRIPE_PRO_PRICE_ID="${STRIPE_PRO_PRICE_ID:-price_placeholder_pro}"
export STRIPE_PRO_PLUS_PRICE_ID="${STRIPE_PRO_PLUS_PRICE_ID:-price_placeholder_pro_plus}"

# Optional: persistent browser for faster filing (login once, reuse session)
# Requires Chrome running with: google-chrome --remote-debugging-port=18800 --user-data-dir=/tmp/kaizen-profile
# export KAIZEN_USE_CDP=1
# export KAIZEN_CDP_URL=http://localhost:18800

echo "Secrets loaded. Starting bot in polling mode..."
cd "$(dirname "$0")"

if [ -x "./.venv/bin/python3" ]; then
  exec .venv/bin/python3 bot.py
elif [ -x "./venv/bin/python3" ]; then
  exec venv/bin/python3 bot.py
else
  echo "Python venv not found (expected backend/venv or backend/.venv)." >&2
  exit 1
fi
