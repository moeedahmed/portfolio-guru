#!/usr/bin/env bash
# Render staging only. Loading is a separate, explicit --load option.
set -euo pipefail
[[ $# == 0 || ( $# == 1 && "$1" == --load ) ]] || { echo "Usage: scripts/install_staging.sh [--load]" >&2; exit 64; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.portfolioguru.staging-bot.plist"
python3 - "$ROOT/scripts/com.portfolioguru.staging-bot.plist" "$PLIST" <<'PY'
import os, plistlib, sys
from pathlib import Path
template = Path(sys.argv[1])
root = Path(os.environ.get('PORTFOLIO_GURU_STAGING_DIR', str(Path.home() / 'projects/portfolio-guru-staging'))).expanduser().resolve()
dev = template.parent.parent.resolve()
if root == dev or dev in root.parents or root == (Path.home() / 'projects/portfolio-guru-live').resolve():
    raise SystemExit('Refusing live/development staging directory')
log = Path.home() / '.openclaw/logs/portfolio-guru-staging/bot.log'
p = plistlib.loads(template.read_bytes())
values = {'__STAGING_DIR__': str(root), '__BOT_LOG__': str(log), '__HOME__': str(Path.home())}
for key in ('PG_VERTEX_SA_SECRET_ID', 'PG_VERTEX_SA_CLIENT_EMAIL', 'PG_VERTEX_BWS_TOKEN_PATH'):
    value = os.environ.get(key, '')
    if not value:
        raise SystemExit(f'{key} must name the same existing authorised Vertex setting as live')
    values['__' + key + '__'] = value
def render(value):
    if isinstance(value, dict): return {k: render(v) for k,v in value.items()}
    if isinstance(value, list): return [render(v) for v in value]
    if isinstance(value, str):
        for old, new in values.items(): value = value.replace(old, new)
    return value
output = Path(sys.argv[2])
output.parent.mkdir(parents=True, exist_ok=True)
log.parent.mkdir(parents=True, exist_ok=True)
output.write_bytes(plistlib.dumps(render(p)))
print('Rendered staging LaunchAgent; not loaded')
PY
if [[ "${1:-}" == --load ]]; then
  STAGING_DIR="${PORTFOLIO_GURU_STAGING_DIR:-$HOME/projects/portfolio-guru-staging}"
  [[ -f "$STAGING_DIR/backend/staging_env.sh" ]] || { echo "Refusing to load a checkout without staging isolation; run stage.sh deploy first" >&2; exit 1; }
  DOMAIN="$(python3 "$ROOT/scripts/verify_live_runtime.py" --service-domain --service-label com.portfolioguru.staging-bot --allow-unregistered)"
  launchctl bootout "$DOMAIN" "$PLIST" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$PLIST"
  launchctl enable "$DOMAIN/com.portfolioguru.staging-bot"
  echo "Loaded staging service"
fi
