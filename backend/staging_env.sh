# Sourced only for PG_ENV=staging, before any log/lock/store is opened.
# This is also used by start-bot.sh so its log rotation cannot touch live logs.
if [ "${PG_ENV:-}" = staging ]; then
  export PORTFOLIO_GURU_DATA_DIR="${PORTFOLIO_GURU_DATA_DIR-$HOME/.openclaw/data/portfolio-guru-staging}"
  export PORTFOLIO_GURU_BOT_LOCK="${PORTFOLIO_GURU_BOT_LOCK-/tmp/portfolio-guru-staging-bot.lock}"
  export PORTFOLIO_GURU_RUNTIME_IDENTITY="${PORTFOLIO_GURU_RUNTIME_IDENTITY-/tmp/portfolio-guru-staging-runtime.json}"
  export PORTFOLIO_GURU_BOT_LOG="${PORTFOLIO_GURU_BOT_LOG-$HOME/.openclaw/logs/portfolio-guru-staging/bot.log}"
  export PG_ALLOWED_USER_IDS="${PG_ALLOWED_USER_IDS-6912896590}"
  python3 - <<'PY' || exit 1
import os
from pathlib import Path
home = Path.home()
checks = {
    'PORTFOLIO_GURU_DATA_DIR': home / '.openclaw/data/portfolio-guru',
    'PORTFOLIO_GURU_BOT_LOG': home / '.openclaw/logs/portfolio-guru',
    'PORTFOLIO_GURU_BOT_LOCK': Path('/tmp/portfolio-guru-bot.lock'),
    'PORTFOLIO_GURU_RUNTIME_IDENTITY': Path('/tmp/portfolio-guru-runtime.json'),
}
live_paths = [p.resolve() for p in checks.values()] + [
    (home / 'projects/portfolio-guru-live').resolve(),
    (home / '.openclaw/.bws-token').resolve(),
]
for name in checks:
    value = os.environ[name]
    path = Path(value).expanduser().resolve()
    if not value or not Path(value).is_absolute():
        raise SystemExit(f'TEST BOT refuses unsafe {name}: live resources must stay isolated')
    # Every staging path is checked against every live resource: a lock path
    # pointing at the live data dir would otherwise be rm -rf'd as a stale lock.
    for live in live_paths:
        if path == live or live in path.parents or path in live.parents:
            raise SystemExit(f'TEST BOT refuses unsafe {name}: live resources must stay isolated')
lock = Path(os.environ['PORTFOLIO_GURU_BOT_LOCK']).expanduser().resolve()
if 'staging' not in lock.name:
    raise SystemExit('TEST BOT refuses a lock path without "staging" in its name')
ids = os.environ['PG_ALLOWED_USER_IDS'].split(',')
if not ids or any(not i.strip().isdigit() or int(i.strip()) <= 0 for i in ids):
    raise SystemExit('TEST BOT refuses empty or invalid PG_ALLOWED_USER_IDS')
PY
  # Never inherit a production per-store override or secret from a parent shell.
  for staging_name in $(compgen -v); do
    case "$staging_name" in
      SUPABASE_*|STRIPE_*|OPENCLAW_*|PORTFOLIO_INBOUND_*|PORTFOLIO_OUTBOUND_*|PG_HEARTBEAT_URL|PG_SIGNOFF_CHASE_HEALTHCHECK_URL|DATABASE_URL|USAGE_DB_PATH|KAIZEN_USER|KAIZEN_PASS|BU_CDP_WS|BWS_ACCESS_TOKEN)
        unset "$staging_name" ;;
      PORTFOLIO_GURU_*_PATH|PORTFOLIO_GURU_*_DIR)
        case "$staging_name" in PORTFOLIO_GURU_DATA_DIR|PORTFOLIO_GURU_STAGING_DIR) ;; *) unset "$staging_name" ;; esac ;;
    esac
  done
  export PYTHON_DOTENV_DISABLED=1 PG_KAIZEN_OFFLINE=1 KAIZEN_USE_CDP=0 PG_PAYMENTS_ENABLED=0
  export PG_ENABLE_SIGNOFF_CHASE="" PG_SIGNOFF_CHASE_USER_IDS=""
  export PG_ENABLE_PROACTIVE="" PG_PROACTIVE_USER_IDS="" PG_PROACTIVE_DRY_RUN=1
  export PG_ENABLE_PASSWORDLESS_CONNECT="" PG_PASSWORDLESS_ALLOWLIST=""
  export PG_ENABLE_BROWSER_USE_FALLBACK=0
  export PG_OPERATOR_CHAT_ID=6912896590
fi
