#!/bin/bash
# Run Portfolio Guru locally in polling mode
# Loads secrets from BWS

set -e

echo "Loading secrets from BWS..."
BWS_ACCESS_TOKEN=$(cat ~/.openclaw/.bws-token)

export TELEGRAM_BOT_TOKEN=$(BWS_ACCESS_TOKEN=$BWS_ACCESS_TOKEN /usr/local/bin/bws secret get af553b7d-5c05-418a-b80e-b405015708ed --output json | python3 -c "import json,sys; print(json.load(sys.stdin)['value'])")
export GOOGLE_API_KEY=$(BWS_ACCESS_TOKEN=$BWS_ACCESS_TOKEN /usr/local/bin/bws secret get af6579a0-2cbe-4cef-94b3-b405017b48fe --output json | python3 -c "import json,sys; print(json.load(sys.stdin)['value'])")
export FERNET_SECRET_KEY=$(BWS_ACCESS_TOKEN=$BWS_ACCESS_TOKEN /usr/local/bin/bws secret get 9e653679-9a33-4c23-a15c-b405015713de --output json | python3 -c "import json,sys; print(json.load(sys.stdin)['value'])")
export OPENAI_API_KEY=$(BWS_ACCESS_TOKEN=$BWS_ACCESS_TOKEN /usr/local/bin/bws secret get 2772c5c3-b357-4015-8252-b3ea00939469 --output json | python3 -c "import json,sys; print(json.load(sys.stdin)['value'])")

echo "Secrets loaded. Starting bot in polling mode..."
cd "$(dirname "$0")"
.venv/bin/python3 bot.py
