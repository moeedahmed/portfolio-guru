#!/usr/bin/env bash
set -euo pipefail

# The live deployment has its own checkout. It used to share the dev
# workspace, which meant a deploy ran "git checkout main" on a directory
# agents were actively editing — switching branches under them — and a
# dirty dev tree could only ever block the deploy or boot unreviewed code.
APP_DIR="${PORTFOLIO_GURU_APP_DIR:-/Users/moeedahmed/projects/portfolio-guru-live}"
SERVICE_LABEL="${PORTFOLIO_GURU_SERVICE_LABEL:-com.portfolioguru.bot}"
PLIST_PATH="${HOME}/Library/LaunchAgents/${SERVICE_LABEL}.plist"
LOCK_DIR="${PORTFOLIO_GURU_DEPLOY_LOCK:-/tmp/portfolio-guru-deploy.lock}"
DEPLOY_EXPECTED_SHA="${DEPLOY_EXPECTED_SHA:-}"

if [[ ! "$DEPLOY_EXPECTED_SHA" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "ERROR: DEPLOY_EXPECTED_SHA must be a full 40-character hexadecimal SHA."
  exit 64
fi

# Resolve the existing service before any checkout/process mutation. Headless
# sessions can own it in user/<uid>; deployment must keep that same domain.
DEPLOY_SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHD_DOMAIN="$(python3 "$DEPLOY_SCRIPT_ROOT/verify_live_runtime.py" --service-domain)"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [[ -f "$LOCK_DIR/pid" ]] && ! kill -0 "$(cat "$LOCK_DIR/pid")" 2>/dev/null; then
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR"
  else
    echo "ERROR: another Portfolio Guru deploy is already running."
    exit 1
  fi
fi
echo "$$" > "$LOCK_DIR/pid"
cleanup_lock() {
  rm -rf "$LOCK_DIR"
}
trap cleanup_lock EXIT

cd "$APP_DIR"

echo "Deploying Portfolio Guru from $APP_DIR"
echo "Current commit: $(git rev-parse --short HEAD)"

if [[ -n "$(git status --porcelain -- backend/filing_coverage.json)" ]]; then
  echo "Resetting generated filing coverage before deploy."
  git restore -- backend/filing_coverage.json
fi

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "ERROR: deployment checkout has local changes. Refusing to deploy."
  git status --short --untracked-files=no
  exit 1
fi

git fetch origin main
ORIGIN_MAIN="$(git rev-parse origin/main)"
if [[ "$ORIGIN_MAIN" != "$DEPLOY_EXPECTED_SHA" ]]; then
  echo "ERROR: origin/main=$ORIGIN_MAIN does not equal DEPLOY_EXPECTED_SHA=$DEPLOY_EXPECTED_SHA."
  exit 1
fi
git checkout main
# Capture the currently-deployed main commit as the rollback target BEFORE we move.
PREV_COMMIT="$(git rev-parse HEAD)"
echo "Last known-good commit (rollback target): $(git rev-parse --short "$PREV_COMMIT")"
if ! git merge-base --is-ancestor HEAD "$DEPLOY_EXPECTED_SHA"; then
  echo "ERROR: expected SHA is not a safe fast-forward from current HEAD."
  exit 1
fi
git merge --ff-only "$DEPLOY_EXPECTED_SHA"
if [[ "$(git rev-parse HEAD)" != "$DEPLOY_EXPECTED_SHA" ]]; then
  echo "ERROR: post-update HEAD does not equal DEPLOY_EXPECTED_SHA."
  exit 1
fi

echo "Updated commit: $(git rev-parse --short HEAD)"
echo "DEPLOYED_SHA=$DEPLOY_EXPECTED_SHA"

cd "$APP_DIR/backend"
PYTHON=""
if [[ -x "./.venv/bin/python3" ]]; then
  PYTHON="./.venv/bin/python3"
elif [[ -x "./venv/bin/python3" ]]; then
  PYTHON="./venv/bin/python3"
else
  echo "ERROR: Python venv not found (expected backend/.venv or backend/venv)."
  exit 1
fi

"$PYTHON" -m pip install -q -r requirements.txt
"$PYTHON" -m py_compile bot.py

if [[ ! -f "$PLIST_PATH" ]]; then
  echo "ERROR: launchd plist not installed at $PLIST_PATH"
  echo "Run: scripts/install_launchd.sh"
  exit 1
fi

# Give launchd room to let an in-flight Kaizen save finish on SIGTERM (older
# installs predate ExitTimeOut in install_launchd.sh).
plutil -replace ExitTimeOut -integer 330 "$PLIST_PATH" 2>/dev/null || true
launchctl bootout "$LAUNCHD_DOMAIN" "$PLIST_PATH" 2>/dev/null || true

collect_app_pids() {
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    if lsof -a -p "$pid" -d cwd 2>/dev/null | grep -q "${APP_DIR}/backend"; then
      echo "$pid"
    fi
  done < <(pgrep -f "bot.py|webhook_server:app" || true)
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    if ps -p "$pid" -o command= | grep -q "${APP_DIR}"; then
      echo "$pid"
    fi
  done < <(pgrep -f "start-bot.sh|start_bot.sh|run_local.sh" || true)
}

app_pids="$(collect_app_pids | sort -u | tr '\n' ' ')"
if [[ -n "$app_pids" ]]; then
  # shellcheck disable=SC2086
  kill $app_pids 2>/dev/null || true
fi
webhook_port_pids="$(lsof -tiTCP:8099 -sTCP:LISTEN 2>/dev/null | tr '\n' ' ' || true)"
if [[ -n "$webhook_port_pids" ]]; then
  # shellcheck disable=SC2086
  kill $webhook_port_pids 2>/dev/null || true
fi
# SIGTERM makes the bot drain running updates before exiting; killing it after
# a fixed 5 s cut Kaizen saves off mid-way. Wait for it, then force stragglers.
stop_grace="${PORTFOLIO_GURU_STOP_GRACE_SECONDS:-330}"
waited=0
while [[ -n "$(collect_app_pids)" && "$waited" -lt "$stop_grace" ]]; do
  sleep 1
  waited=$((waited + 1))
done
app_pids="$(collect_app_pids | sort -u | tr '\n' ' ')"
if [[ -n "$app_pids" ]]; then
  # shellcheck disable=SC2086
  kill -9 $app_pids 2>/dev/null || true
fi
webhook_port_pids="$(lsof -tiTCP:8099 -sTCP:LISTEN 2>/dev/null | tr '\n' ' ' || true)"
if [[ -n "$webhook_port_pids" ]]; then
  # shellcheck disable=SC2086
  kill -9 $webhook_port_pids 2>/dev/null || true
fi
sleep 1

start_service() {
  launchctl bootstrap "$LAUNCHD_DOMAIN" "$PLIST_PATH" || return 1
  launchctl enable "${LAUNCHD_DOMAIN}/${SERVICE_LABEL}" 2>/dev/null || true

  sleep 3 || return 1

  for _ in 1 2 3 4 5; do
    service_pid="$(launchctl print "${LAUNCHD_DOMAIN}/${SERVICE_LABEL}" | awk '/pid =/ {print $3; exit}')" || return 1
    orphan_pids=""
    while read -r pid; do
      [[ -z "$pid" || "$pid" == "$service_pid" ]] && continue
      if lsof -a -p "$pid" -d cwd 2>/dev/null | grep -q "${APP_DIR}/backend"; then
        orphan_pids="${orphan_pids} ${pid}"
      fi
    done < <(pgrep -f "bot.py" || true)

    [[ -z "$orphan_pids" ]] && break
    # shellcheck disable=SC2086
    kill $orphan_pids 2>/dev/null || true
    sleep 2
    # shellcheck disable=SC2086
    kill -9 $orphan_pids 2>/dev/null || true
    sleep 2
  done

  echo "launchd service:"
  launchctl print "${LAUNCHD_DOMAIN}/${SERVICE_LABEL}" | sed -n '1,25p' || return 1

  echo "Running Portfolio Guru processes:"
  pgrep -fl "/portfolio-guru/backend|webhook_server:app|bot.py" || true
}

# -------------------------------------------------------------------------
# Post-deploy smoke + automatic rollback.
# A green compile is not a green runtime: if the freshly-started service has
# no live process, or it dies within the settle window (crash-loop), revert
# to the last known-good commit, restart, and fail the deploy so CI is red.
# -------------------------------------------------------------------------
service_pid_now() {
  launchctl print "${LAUNCHD_DOMAIN}/${SERVICE_LABEL}" 2>/dev/null | awk '/pid =/ {print $3; exit}'
}

smoke_ok() {
  sleep 20  # let the service boot and clear any immediate crash-loop
  local pid; pid="$(service_pid_now)" || return 1
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "SMOKE FAIL: launchd service has no live pid after start"; return 1
  fi
  sleep 8   # confirm it stays up (didn't crash-loop right after boot)
  local pid2; pid2="$(service_pid_now)" || return 1
  if [[ -z "$pid2" ]] || ! kill -0 "$pid2" 2>/dev/null; then
    echo "SMOKE FAIL: service died after start (crash-loop)"; return 1
  fi
  if ! lsof -tiTCP:8099 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "SMOKE WARN: webhook server not listening on 8099 (billing webhook down)"
  fi
  if ! "$APP_DIR/scripts/verify_live_runtime.py"; then
    echo "SMOKE FAIL: runtime identity verification failed"; return 1
  fi
  echo "SMOKE OK: service pid ${pid2} stable"; return 0
}

if ! start_service || ! smoke_ok; then
  echo "Post-deploy smoke FAILED — rolling back to ${PREV_COMMIT}"
  git -C "$APP_DIR" reset --hard "$PREV_COMMIT"
  ( cd "$APP_DIR/backend" && "$PYTHON" -m pip install -q -r requirements.txt && "$PYTHON" -m py_compile bot.py )
  launchctl bootout "$LAUNCHD_DOMAIN" "$PLIST_PATH" 2>/dev/null || true
  sleep 3
  launchctl bootstrap "$LAUNCHD_DOMAIN" "$PLIST_PATH"
  launchctl enable "${LAUNCHD_DOMAIN}/${SERVICE_LABEL}" 2>/dev/null || true
  echo "ROLLED BACK to $(git -C "$APP_DIR" rev-parse --short HEAD). Deploy marked failed."
  exit 1
fi

echo "Deploy complete: $(git -C "$APP_DIR" rev-parse --short HEAD)"
