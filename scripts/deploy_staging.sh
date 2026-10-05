#!/usr/bin/env bash
# Local deployment of an origin commit to the isolated staging service only.
set -Eeuo pipefail
SHA="${1:-}"
[[ $# == 1 && "$SHA" =~ ^[0-9a-fA-F]{40}$ ]] || { echo "Usage: scripts/deploy_staging.sh <40-hex-sha>" >&2; exit 64; }
SHA="$(printf '%s' "$SHA" | tr '[:upper:]' '[:lower:]')"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${PORTFOLIO_GURU_STAGING_DIR:-$HOME/projects/portfolio-guru-staging}"
SERVICE_LABEL=com.portfolioguru.staging-bot
PLIST_PATH="$HOME/Library/LaunchAgents/$SERVICE_LABEL.plist"
IDENTITY=/tmp/portfolio-guru-staging-runtime.json
PROOF_TOOL="$ROOT/scripts/staging_proof.py"
ORIGIN="$(git -C "$ROOT" remote get-url origin)"
APP_DIR="$(python3 - "$APP_DIR" "$ROOT" "$(git -C "$ROOT" rev-parse --git-common-dir)" <<'PY'
import sys
from pathlib import Path
target, root, common = map(Path, sys.argv[1:])
root = root.resolve()
common = (root / common).resolve() if not common.is_absolute() else common.resolve()
target = target.expanduser().resolve()
for forbidden in (Path.home() / 'projects/portfolio-guru-live', common.parent, root):
    forbidden = forbidden.resolve()
    if target == forbidden or forbidden in target.parents or target in forbidden.parents:
        raise SystemExit('Refusing staging deploy into live or development checkout')
print(target)
PY
)"
[[ -f "$PLIST_PATH" ]] || { echo "Install staging plist first: scripts/install_staging.sh" >&2; exit 1; }
# Validate the rendered service before launchctl ever sees it.
python3 - "$PLIST_PATH" "$APP_DIR" <<'PY'
import plistlib, sys
from pathlib import Path
p = plistlib.loads(Path(sys.argv[1]).read_bytes())
root = sys.argv[2]
env = p.get('EnvironmentVariables', {})
if p.get('Label') != 'com.portfolioguru.staging-bot' or p.get('WorkingDirectory') != root or p.get('ProgramArguments') != ['/bin/bash', root + '/start-bot.sh'] or env.get('PG_ENV') != 'staging':
    raise SystemExit('Refusing unexpected staging plist: service, checkout and PG_ENV must match')
PY

# Serialise staging deployments and proofs. No shared/live lock is used.
LOCK="${PORTFOLIO_GURU_STAGING_DEPLOY_LOCK:-/tmp/portfolio-guru-staging-deploy.lock}"
mkdir "$LOCK" 2>/dev/null || { echo "Another staging deploy/proof is running" >&2; exit 1; }
trap 'rmdir "$LOCK"' EXIT
PREV=""
if [[ ! -e "$APP_DIR" ]]; then
  git clone --no-checkout "$ORIGIN" "$APP_DIR"
else
  [[ -d "$APP_DIR/.git" && ! -L "$APP_DIR/.git" ]] || { echo "Staging must be a plain clone" >&2; exit 1; }
  [[ "$(git -C "$APP_DIR" remote get-url origin)" == "$ORIGIN" ]] || { echo "Staging origin does not match this repo" >&2; exit 1; }
  [[ -z "$(git -C "$APP_DIR" status --porcelain --untracked-files=no)" ]] || { echo "Staging clone has local changes" >&2; exit 1; }
  PREV="$(git -C "$APP_DIR" rev-parse HEAD)"
fi
git -C "$APP_DIR" fetch --prune origin '+refs/heads/*:refs/remotes/origin/*'
git -C "$APP_DIR" cat-file -e "$SHA^{commit}"
[[ -n "$(git -C "$APP_DIR" for-each-ref --format='%(refname)' --contains "$SHA" refs/remotes/origin/)" ]] || { echo "SHA is not on an origin branch" >&2; exit 1; }
git -C "$APP_DIR" cat-file -e "$SHA:backend/staging_env.sh" || { echo "SHA has no staging isolation" >&2; exit 1; }
# A pre-staging checkout cannot be safely restarted on rollback.
if [[ -n "$PREV" ]] && ! git -C "$APP_DIR" cat-file -e "$PREV:backend/staging_env.sh" 2>/dev/null; then PREV=""; fi
DOMAIN="$(python3 "$ROOT/scripts/verify_live_runtime.py" --service-domain --service-label "$SERVICE_LABEL" --allow-unregistered)"

stop_staging() {
  launchctl bootout "$DOMAIN" "$PLIST_PATH" 2>/dev/null || true
  # Match exact cwd, never names alone or ports belonging to another service.
  local pid cwd
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' || true)"
    if [[ "$cwd" == "$APP_DIR/backend" ]]; then
      kill "$pid" 2>/dev/null || true
      local waited=0
      while kill -0 "$pid" 2>/dev/null && [[ "$waited" -lt 330 ]]; do sleep 1; waited=$((waited + 1)); done
      if kill -0 "$pid" 2>/dev/null; then
        cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' || true)"
        [[ "$cwd" != "$APP_DIR/backend" ]] || kill -9 "$pid" 2>/dev/null || true
      fi
    fi
  done < <(pgrep -f 'bot.py|run_local.sh' || true)
}

install_candidate() {
  [[ ! -L "$APP_DIR/backend/venv" && ! -L "$APP_DIR/backend/.venv" ]] || { echo "Staging cannot use a shared venv" >&2; return 1; }
  [[ ! -e "$APP_DIR/backend/.venv" ]] || { echo "Staging uses backend/venv exclusively" >&2; return 1; }
  [[ -x "$APP_DIR/backend/venv/bin/python3" ]] || python3 -m venv "$APP_DIR/backend/venv" || return 1
  "$APP_DIR/backend/venv/bin/python3" -m pip install -q -r "$APP_DIR/backend/requirements-dev.txt" || return 1
  "$APP_DIR/backend/venv/bin/python3" -m py_compile "$APP_DIR/backend/bot.py"
}

start_staging() {
  launchctl bootstrap "$DOMAIN" "$PLIST_PATH" || return 1
  launchctl enable "$DOMAIN/$SERVICE_LABEL" || return 1
}

service_pid() { launchctl print "$DOMAIN/$SERVICE_LABEL" | awk '/pid =/ {print $3; exit}'; }
smoke() {
  local pid next
  sleep 3
  pid="$(service_pid)" || return 1
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null || return 1
  sleep 30
  next="$(service_pid)" || return 1
  [[ "$pid" == "$next" ]] && kill -0 "$next" 2>/dev/null || return 1
  python3 "$ROOT/scripts/verify_live_runtime.py" --service-label "$SERVICE_LABEL" \
    --root "$APP_DIR" --identity "$IDENTITY" --expected-sha "$1"
}

rollback_staging() {
  trap - ERR
  echo "Staging failed; rolling back staging only to ${PREV:-stopped (no previous staging SHA)}" >&2
  stop_staging
  if [[ -n "$PREV" ]]; then
    if git -C "$APP_DIR" checkout --detach "$PREV" && install_candidate && start_staging && smoke "$PREV"; then
      echo "Staging rollback verified" >&2
    else
      echo "Staging rollback failed; service stopped" >&2
      stop_staging
    fi
  fi
  exit 1
}

# Invalidate stale proof before moving checkout or restarting the staging bot.
python3 "$PROOF_TOOL" deploy --sha "$SHA" --result fail
trap rollback_staging ERR
stop_staging
git -C "$APP_DIR" checkout --detach "$SHA"
install_candidate
start_staging
smoke "$SHA"
python3 "$PROOF_TOOL" deploy --sha "$SHA" --result pass
trap - ERR
echo "Staging deployed and runtime verified: $SHA"
