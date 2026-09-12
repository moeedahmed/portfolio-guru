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
# Shell-local only. The Vertex service-account secret is fetched by the
# Python runtime itself (vertex_credentials.py), which reads the BWS access
# token from PG_VERTEX_BWS_TOKEN_PATH at call time — never from this
# process's environment. Every other secret below is resolved here, in the
# shell, and only the resolved value is exported.
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

get_secret_by_key() {
  # Look up a BWS secret by its KEY name (not id). Non-fatal: prints empty if
  # absent. Used for the Vertex secrets whose ids aren't known until created.
  local key="$1"
  BWS_ACCESS_TOKEN=$BWS_ACCESS_TOKEN "$BWS_BIN" secret list --output json 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(next((s['value'] for s in d if s.get('key')=='$key'), ''))" 2>/dev/null || true
}

pg_is_truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
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
# Clinical extraction runs on Vertex AI (europe-west2). This label is what the
# startup banner reports; it must not name an off-region provider.
export PORTFOLIO_GURU_EXTRACTOR_PROVIDER="vertex-gemini-eu"
export GEMINI_3_5_FLASH_MODEL="${GEMINI_3_5_FLASH_MODEL:-gemini-3.5-flash}"
export PG_GATHERING_MODE="${PG_GATHERING_MODE:-1}"
echo "Model: extractor=$PORTFOLIO_GURU_EXTRACTOR_PROVIDER fallback=$GEMINI_3_5_FLASH_MODEL"

# --- Vertex AI (EU) routing for clinical extraction -----------------------
# Inert until the GCP secrets exist in BWS. Credential provisioning is BWS's
# job, not this launcher's: no service-account key is fetched, materialised,
# or written to disk here. The Python runtime (vertex_credentials.py, via
# gemini_client.make_client()) fetches the service-account secret from BWS
# directly into process memory, reading the BWS access token itself from
# PG_VERTEX_BWS_TOKEN_PATH — never inherited from this shell — and never
# falls back to Application Default Credentials. Missing configuration
# fails the launcher closed rather than silently falling back to the
# (off-region) developer API.
GCP_PROJECT_ID="$(get_secret_by_key GCP_PROJECT_ID)"
PG_USE_VERTEX="$(get_secret_by_key PG_USE_VERTEX)"

# Checked before any project-scoped lookup below: a truthy flag with no
# project must fail here, not fall through silently to the developer API.
if pg_is_truthy "${PG_USE_VERTEX:-}" && [ -z "$GCP_PROJECT_ID" ]; then
  echo "PG_USE_VERTEX is enabled but GCP_PROJECT_ID is not set; refusing to start (no fallback to the developer API)." >&2
  exit 1
fi

if [ -n "$GCP_PROJECT_ID" ]; then
  export GCP_PROJECT_ID
  export PG_USE_VERTEX
  GCP_VERTEX_LOCATION="$(get_secret_by_key GCP_VERTEX_LOCATION)"
  export GCP_VERTEX_LOCATION="${GCP_VERTEX_LOCATION:-europe-west2}"
  # Optional model override (empty -> gemini_client default gemini-3.5-flash).
  export GEMINI_VERTEX_MODEL="$(get_secret_by_key GEMINI_VERTEX_MODEL)"
  export PG_VERTEX_SA_SECRET_ID="${PG_VERTEX_SA_SECRET_ID:-}"
  # Nonsecret expected identity pin — the exact existing service-account
  # email, so a re-pointed secret id in the same project cannot silently
  # authenticate as a different account.
  export PG_VERTEX_SA_CLIENT_EMAIL="${PG_VERTEX_SA_CLIENT_EMAIL:-}"
  # Nonsecret absolute path to the SAME existing beta BWS token file this
  # launcher already reads above (e.g. ~/.openclaw/.bws-token or the
  # canonical ~/.hermes/.bws-token route) — the operator sets this to the
  # already-authorised route; this launcher does not choose or default it.
  export PG_VERTEX_BWS_TOKEN_PATH="${PG_VERTEX_BWS_TOKEN_PATH:-}"

  if pg_is_truthy "${PG_USE_VERTEX:-}"; then
    if [ -z "${PG_VERTEX_SA_SECRET_ID:-}" ]; then
      echo "PG_USE_VERTEX is enabled but PG_VERTEX_SA_SECRET_ID is not set; refusing to start (no fallback to the developer API)." >&2
      exit 1
    fi
    if [ -z "${PG_VERTEX_SA_CLIENT_EMAIL:-}" ]; then
      echo "PG_USE_VERTEX is enabled but PG_VERTEX_SA_CLIENT_EMAIL is not set; refusing to start." >&2
      exit 1
    fi
    if [ -z "${PG_VERTEX_BWS_TOKEN_PATH:-}" ]; then
      echo "PG_USE_VERTEX is enabled but PG_VERTEX_BWS_TOKEN_PATH is not set; refusing to start." >&2
      exit 1
    fi
    echo "Vertex AI (EU) configured: project=$GCP_PROJECT_ID location=$GCP_VERTEX_LOCATION (service-account credential fetched from BWS into process memory at first use)"
  else
    echo "Vertex AI (EU) configured but PG_USE_VERTEX not enabled: project=$GCP_PROJECT_ID"
  fi
fi

FERNET_SECRET_KEY="$(get_secret 9e653679-9a33-4c23-a15c-b405015713de)"
export FERNET_SECRET_KEY

# Liveness heartbeat target (Healthchecks.io ping URL). bot.py already schedules
# the 5-minute ping; without this the whole mechanism is a silent no-op, which
# is exactly how it sat unused until 2026-08-18. Non-fatal when absent so a
# missing secret degrades to "no monitoring", never "bot won't start".
PG_HEARTBEAT_URL="$(get_secret_by_key PG_HEARTBEAT_URL)"
export PG_HEARTBEAT_URL
if [ -n "$PG_HEARTBEAT_URL" ]; then
  echo "Liveness heartbeat: configured"
else
  echo "Liveness heartbeat: NOT configured (set PG_HEARTBEAT_URL in BWS to enable)"
fi

# Weekly Portfolio Health sign-off chase. The chase itself is OFF unless
# PG_ENABLE_SIGNOFF_CHASE is set — it messages doctors unprompted, so it stays
# opt-in. The ping URL is loaded regardless so that enabling the chase is a
# one-line change and never ships an unmonitored job: this feature's success
# signal is silence, so a dead job and a clean portfolio look identical
# without it.
PG_SIGNOFF_CHASE_HEALTHCHECK_URL="$(get_secret_by_key PG_SIGNOFF_CHASE_HEALTHCHECK_URL)"
export PG_SIGNOFF_CHASE_HEALTHCHECK_URL
# Enabled 2026-08-26 for the whole beta cohort, once the chase stopped
# re-listing and began reporting only what moved. Set to empty to disable.
export PG_ENABLE_SIGNOFF_CHASE="${PG_ENABLE_SIGNOFF_CHASE:-1}"
export PG_SIGNOFF_CHASE_USER_IDS="${PG_SIGNOFF_CHASE_USER_IDS:-}"
if [ -n "$PG_ENABLE_SIGNOFF_CHASE" ]; then
  echo "Sign-off chase: ENABLED${PG_SIGNOFF_CHASE_USER_IDS:+ (users: $PG_SIGNOFF_CHASE_USER_IDS)}"
else
  echo "Sign-off chase: off (set PG_ENABLE_SIGNOFF_CHASE=1 to enable)"
fi
# DEEPSEEK_API_KEY is deliberately NOT exported. Clinical extraction runs on
# Vertex AI in europe-west2; DeepSeek is a Chinese endpoint with no UK adequacy
# decision and no DPA, and having the key present meant one unset PG_USE_VERTEX
# away from routing Art. 9 health data off-region. extractor._select_providers
# now refuses to start in that state rather than falling back. Bake-off scripts
# that legitimately need it load it themselves.
#   BWS secret: c5d82503-3d1d-427b-9be1-b44e01564203 (DEEPSEEK_API_KEY_PORTFOLIO)

# OpenAI keys — NOT loaded unless explicitly requested
# if [ -n "$OPENAI_API_KEY" ]; then
#   export OPENAI_API_KEY
# fi
# Stripe (Portfolio Guru account)
STRIPE_SECRET_KEY="$(get_secret 4450d6ac-f7a2-4802-a27a-b428006488c9)"
export STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET="$(get_secret 3ffc5e11-f4d6-4ff8-872f-b428006e7126)"
export STRIPE_WEBHOOK_SECRET
# Price IDs are BWS-overridable so going live is a secrets-only change (no code
# edit). Falls back to the current test-mode prices when the BWS key is unset.
# IMPORTANT: live secret key + live webhook secret MUST be paired with LIVE
# price IDs, or the webhook can't map the price and the customer is charged but
# not upgraded (log_stripe_mode() fails startup on a mismatch).
export STRIPE_PRO_PRICE_ID="$(get_secret_by_key STRIPE_PRO_PRICE_ID)"
export STRIPE_PRO_PRICE_ID="${STRIPE_PRO_PRICE_ID:-price_1TKY11FtxKHU39UdHFXn1yur}"
export STRIPE_PRO_PLUS_PRICE_ID="$(get_secret_by_key STRIPE_PRO_PLUS_PRICE_ID)"
export STRIPE_PRO_PLUS_PRICE_ID="${STRIPE_PRO_PLUS_PRICE_ID:-price_1TKY12FtxKHU39UdTQZY8rOq}"
PORTFOLIO_INBOUND_SECRET="${PORTFOLIO_INBOUND_SECRET:-$(get_mapped_secret PORTFOLIO_INBOUND_SECRET)}"
export PORTFOLIO_INBOUND_SECRET
# The outbound path must point at the OpenClaw gateway, not this webhook server.
# Older shells can carry a stale self-referential value from local smoke tests.
if [ -z "${PORTFOLIO_OUTBOUND_URL:-}" ] || [ "$PORTFOLIO_OUTBOUND_URL" = "http://127.0.0.1:8099" ]; then
  PORTFOLIO_OUTBOUND_URL="http://127.0.0.1:18789"
fi
export PORTFOLIO_OUTBOUND_URL
PORTFOLIO_OUTBOUND_ACCOUNT_ID="${PORTFOLIO_OUTBOUND_ACCOUNT_ID:-portfolio-guru}"
export PORTFOLIO_OUTBOUND_ACCOUNT_ID
PORTFOLIO_OUTBOUND_SECRET="${PORTFOLIO_OUTBOUND_SECRET:-$(get_mapped_secret PORTFOLIO_BRIDGE_SECRET)}"
export PORTFOLIO_OUTBOUND_SECRET
if [ -z "${OPENCLAW_GATEWAY_TOKEN:-}" ] && [ -z "${OPENCLAW_GATEWAY_AUTH_TOKEN:-}" ]; then
  OPENCLAW_GATEWAY_AUTH_TOKEN="$(get_mapped_secret OPENCLAW_GATEWAY_AUTH_TOKEN)"
  export OPENCLAW_GATEWAY_AUTH_TOKEN
fi
if [ -z "${OPENCLAW_GATEWAY_TOKEN:-}" ] && [ -n "${OPENCLAW_GATEWAY_AUTH_TOKEN:-}" ]; then
  OPENCLAW_GATEWAY_TOKEN="$OPENCLAW_GATEWAY_AUTH_TOKEN"
  export OPENCLAW_GATEWAY_TOKEN
fi
PORTFOLIO_OUTBOUND_GATEWAY_TOKEN="${PORTFOLIO_OUTBOUND_GATEWAY_TOKEN:-${OPENCLAW_GATEWAY_TOKEN:-}}"
export PORTFOLIO_OUTBOUND_GATEWAY_TOKEN

PYTHON=""
if [ -x "./.venv/bin/python3" ]; then
  PYTHON="./.venv/bin/python3"
elif [ -x "./venv/bin/python3" ]; then
  PYTHON="./venv/bin/python3"
else
  echo "Python venv not found (expected backend/venv or backend/.venv)." >&2
  exit 1
fi

# Eager Vertex credential preflight — fetch + refresh against the real
# Google token endpoint now, before the webhook server, before Telegram
# polling, and before any readiness/health signal. A broken credential
# aborts the launcher here instead of surfacing on the first clinical
# request. No-op when PG_USE_VERTEX is not enabled.
if pg_is_truthy "${PG_USE_VERTEX:-}"; then
  echo "Vertex AI (EU): running credential preflight (fetch + refresh, before any traffic)..."
  if ! "$PYTHON" -m vertex_preflight; then
    echo "Vertex AI (EU) credential preflight failed; refusing to start (no ADC fallback, no traffic admitted)." >&2
    exit 1
  fi
fi

# Playwright package upgrades can leave the local browser cache one revision
# behind. Install is idempotent when the expected Chromium build is already
# present, and prevents Kaizen filing from failing with the raw Playwright
# "please run playwright install" message.
"$PYTHON" -m playwright install chromium >/dev/null

# Persistent browser for Kaizen filing (login once, reuse session)
export KAIZEN_USE_CDP="${KAIZEN_USE_CDP:-1}"
export KAIZEN_CDP_URL="${KAIZEN_CDP_URL:-http://localhost:18800}"
"$SCRIPT_DIR/ensure_chrome.sh" --verbose

echo "Secrets loaded. Starting bot + webhook server..."

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
