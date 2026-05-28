#!/usr/bin/env bash
# Local runner for the private vNext test bot.
#
# Usage:
#   PG_VNEXT_BOT_TOKEN=<token> scripts/run_vnext_local.sh
#
# Or load the token from Bitwarden Secrets Manager at runtime:
#   PG_VNEXT_BOT_TOKEN_BWS_ID=<secret-id> scripts/run_vnext_local.sh
#
# The BWS secret ID is never hardcoded here — pass it as an env var or
# set it in your shell profile. The script never touches the public bot
# token, Kaizen credentials, launchd, or the deploy pipeline.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")/backend"

# ------------------------------------------------------------------
# Token resolution — PG_VNEXT_BOT_TOKEN wins; fall back to BWS fetch.
# ------------------------------------------------------------------
if [ -z "${PG_VNEXT_BOT_TOKEN:-}" ] && [ -n "${PG_VNEXT_BOT_TOKEN_BWS_ID:-}" ]; then
    BWS_BIN="$(command -v bws 2>/dev/null || echo "/Users/moeedahmed/.cargo/bin/bws")"
    if [ ! -x "$BWS_BIN" ]; then
        echo "[run_vnext_local] bws not found — install Bitwarden Secrets Manager CLI." >&2
        exit 1
    fi
    BWS_ACCESS_TOKEN="$(cat ~/.openclaw/.bws-token 2>/dev/null || true)"
    if [ -z "$BWS_ACCESS_TOKEN" ]; then
        echo "[run_vnext_local] ~/.openclaw/.bws-token not found; cannot fetch from BWS." >&2
        exit 1
    fi
    PG_VNEXT_BOT_TOKEN="$(
        BWS_ACCESS_TOKEN="$BWS_ACCESS_TOKEN" "$BWS_BIN" secret get "$PG_VNEXT_BOT_TOKEN_BWS_ID" --output json \
        | python3 -c "import json,sys; print(json.load(sys.stdin)['value'])"
    )"
    export PG_VNEXT_BOT_TOKEN
fi

if [ -z "${PG_VNEXT_BOT_TOKEN:-}" ]; then
    echo "[run_vnext_local] PG_VNEXT_BOT_TOKEN is not set." >&2
    echo "[run_vnext_local] Set it directly or set PG_VNEXT_BOT_TOKEN_BWS_ID to load from BWS." >&2
    exit 1
fi

# ------------------------------------------------------------------
# Python venv detection
# ------------------------------------------------------------------
PYTHON=""
if [ -x "$BACKEND_DIR/venv/bin/python3" ]; then
    PYTHON="$BACKEND_DIR/venv/bin/python3"
elif [ -x "$BACKEND_DIR/.venv/bin/python3" ]; then
    PYTHON="$BACKEND_DIR/.venv/bin/python3"
else
    echo "[run_vnext_local] Python venv not found under $BACKEND_DIR." >&2
    exit 1
fi

echo "[run_vnext_local] Starting vNext private test bot..."
exec "$PYTHON" "$BACKEND_DIR/vnext_runner.py"
