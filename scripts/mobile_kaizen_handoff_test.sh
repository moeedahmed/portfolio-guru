#!/usr/bin/env bash
set -euo pipefail

# Test-only launcher for the mobile Kaizen handoff. It owns no Telegram token
# and does not start, stop, or reconfigure either Telegram bot.

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="${PORTFOLIO_GURU_HANDOFF_RUNTIME_DIR:-/Users/moeedahmed/.openclaw/data/portfolio-guru/mobile-handoff}"
ARCHIVE_ROOT="$RUNTIME_DIR/_archived"
SERVER_PID_FILE="$RUNTIME_DIR/server.pid"
TUNNEL_PID_FILE="$RUNTIME_DIR/tunnel.pid"
PUBLIC_URL_FILE="$RUNTIME_DIR/public-url"
SERVER_LOG="$RUNTIME_DIR/server.log"
TUNNEL_LOG="$RUNTIME_DIR/tunnel.log"
PYTHON_BIN="$REPO_ROOT/backend/venv/bin/python3"
CLOUDFLARED_BIN="/opt/homebrew/bin/cloudflared"
LOCAL_URL="http://127.0.0.1:8100"

mkdir -p "$RUNTIME_DIR" "$ARCHIVE_ROOT"
chmod 700 "$RUNTIME_DIR" "$ARCHIVE_ROOT"

read_pid() {
  local file="$1"
  if [ -f "$file" ]; then
    tr -cd '0-9' < "$file"
  fi
}

pid_matches() {
  local pid="$1"
  local marker="$2"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  ps -p "$pid" -o command= 2>/dev/null | rg -F "$marker" >/dev/null
}

archive_runtime_files() {
  local stamp archive
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  archive="$ARCHIVE_ROOT/$stamp"
  mkdir -p "$archive"
  local file
  for file in "$SERVER_PID_FILE" "$TUNNEL_PID_FILE" "$PUBLIC_URL_FILE" "$SERVER_LOG" "$TUNNEL_LOG"; do
    if [ -e "$file" ]; then
      mv "$file" "$archive/"
    fi
  done
}

stop_one() {
  local pid="$1"
  local marker="$2"
  if pid_matches "$pid" "$marker"; then
    kill -TERM "$pid"
    local attempt
    for attempt in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || return 0
      sleep 0.25
    done
    echo "Process $pid did not stop after SIGTERM; leaving it for manual review." >&2
    return 1
  fi
}

status() {
  local server_pid tunnel_pid
  server_pid="$(read_pid "$SERVER_PID_FILE")"
  tunnel_pid="$(read_pid "$TUNNEL_PID_FILE")"
  if ! pid_matches "$server_pid" "mobile_kaizen_handoff:create_default_app"; then
    echo "STOPPED mobile handoff server"
    return 1
  fi
  if ! pid_matches "$tunnel_pid" "$LOCAL_URL"; then
    echo "DEGRADED server is local-only; public tunnel is not running"
    return 1
  fi
  if ! curl -fsS --max-time 4 "$LOCAL_URL/health" >/dev/null; then
    echo "DEGRADED local health check failed"
    return 1
  fi
  if [ ! -s "$PUBLIC_URL_FILE" ]; then
    echo "DEGRADED public URL is unavailable"
    return 1
  fi
  echo "READY mobile Kaizen handoff test: $(<"$PUBLIC_URL_FILE")"
}

public_health() {
  local public_url="$1"
  if curl -fsS --max-time 5 "$public_url/health" >/dev/null 2>&1; then
    return 0
  fi
  # A freshly-created trycloudflare hostname can reach authoritative DNS
  # before the Mac's system resolver cache sees it. Verify the same TLS host
  # against Cloudflare's public resolver rather than declaring the tunnel dead.
  local host edge_ip
  host="${public_url#https://}"
  edge_ip="$(dig +short "$host" @1.1.1.1 | rg '^[0-9]+(\.[0-9]+){3}$' | head -1 || true)"
  [ -n "$edge_ip" ] || return 1
  curl -fsS --max-time 5 \
    --resolve "$host:443:$edge_ip" \
    "$public_url/health" >/dev/null 2>&1
}

start() {
  local stay_running="${1:-0}"
  if status >/dev/null 2>&1; then
    status
    return 0
  fi

  local stale_server stale_tunnel
  stale_server="$(read_pid "$SERVER_PID_FILE")"
  stale_tunnel="$(read_pid "$TUNNEL_PID_FILE")"
  if pid_matches "$stale_server" "mobile_kaizen_handoff:create_default_app" || \
     pid_matches "$stale_tunnel" "$LOCAL_URL"; then
    echo "A partial handoff runtime is still active; run '$0 stop' first." >&2
    return 1
  fi
  archive_runtime_files

  if [ ! -x "$PYTHON_BIN" ]; then
    echo "Portfolio Guru virtualenv is unavailable: $PYTHON_BIN" >&2
    return 1
  fi
  if [ ! -x "$CLOUDFLARED_BIN" ]; then
    echo "cloudflared is unavailable: $CLOUDFLARED_BIN" >&2
    return 1
  fi

  umask 077
  PYTHONPATH="$REPO_ROOT/backend" \
    PG_MOBILE_HANDOFF_PUBLIC_URL_FILE="$PUBLIC_URL_FILE" \
    PG_MOBILE_HANDOFF_INTERNAL_KEY_FILE="$RUNTIME_DIR/internal.key" \
    nohup "$PYTHON_BIN" -m uvicorn \
      mobile_kaizen_handoff:create_default_app \
      --factory \
      --host 127.0.0.1 \
      --port 8100 \
      --log-level warning \
      --no-access-log \
      >"$SERVER_LOG" 2>&1 &
  local server_pid=$!
  echo "$server_pid" > "$SERVER_PID_FILE"

  local attempt
  for attempt in $(seq 1 40); do
    if curl -fsS --max-time 2 "$LOCAL_URL/health" >/dev/null 2>&1; then
      break
    fi
    if ! kill -0 "$server_pid" 2>/dev/null; then
      echo "Mobile handoff server stopped during startup." >&2
      return 1
    fi
    sleep 0.25
  done
  if ! curl -fsS --max-time 2 "$LOCAL_URL/health" >/dev/null; then
    stop_one "$server_pid" "mobile_kaizen_handoff:create_default_app" || true
    echo "Mobile handoff server did not become healthy." >&2
    return 1
  fi

  nohup "$CLOUDFLARED_BIN" tunnel \
    --config /dev/null \
    --no-autoupdate \
    --loglevel info \
    --url "$LOCAL_URL" \
    >"$TUNNEL_LOG" 2>&1 &
  local tunnel_pid=$!
  echo "$tunnel_pid" > "$TUNNEL_PID_FILE"

  local public_url=""
  for attempt in $(seq 1 60); do
    public_url="$(rg -o 'https://[A-Za-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)"
    [ -n "$public_url" ] && break
    if ! kill -0 "$tunnel_pid" 2>/dev/null; then
      break
    fi
    sleep 0.5
  done
  if [ -z "$public_url" ]; then
    stop_one "$tunnel_pid" "$LOCAL_URL" || true
    stop_one "$server_pid" "mobile_kaizen_handoff:create_default_app" || true
    echo "Temporary public tunnel did not produce a URL." >&2
    return 1
  fi
  echo "$public_url" > "$PUBLIC_URL_FILE"
  chmod 600 "$PUBLIC_URL_FILE"

  for attempt in $(seq 1 30); do
    if public_health "$public_url"; then
      status
      if [ "$stay_running" != "1" ]; then
        return 0
      fi
      trap 'stop_one "$tunnel_pid" "$LOCAL_URL" || true; stop_one "$server_pid" "mobile_kaizen_handoff:create_default_app" || true; exit 0' TERM INT HUP
      while kill -0 "$server_pid" 2>/dev/null && kill -0 "$tunnel_pid" 2>/dev/null; do
        sleep 2
      done
      stop_one "$tunnel_pid" "$LOCAL_URL" || true
      stop_one "$server_pid" "mobile_kaizen_handoff:create_default_app" || true
      echo "Mobile handoff child process stopped unexpectedly." >&2
      return 1
    fi
    sleep 0.5
  done
  stop_one "$tunnel_pid" "$LOCAL_URL" || true
  stop_one "$server_pid" "mobile_kaizen_handoff:create_default_app" || true
  echo "Temporary public tunnel failed its health check." >&2
  return 1
}

stop() {
  local server_pid tunnel_pid failed=0
  server_pid="$(read_pid "$SERVER_PID_FILE")"
  tunnel_pid="$(read_pid "$TUNNEL_PID_FILE")"
  stop_one "$tunnel_pid" "$LOCAL_URL" || failed=1
  stop_one "$server_pid" "mobile_kaizen_handoff:create_default_app" || failed=1
  if [ "$failed" -ne 0 ]; then
    return 1
  fi
  archive_runtime_files
  echo "STOPPED mobile Kaizen handoff test; runtime evidence archived."
}

case "${1:-status}" in
  start) start ;;
  run) start 1 ;;
  stop) stop ;;
  status) status ;;
  *) echo "Usage: $0 start|run|stop|status" >&2; exit 2 ;;
esac
