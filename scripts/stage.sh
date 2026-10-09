#!/usr/bin/env bash
# The single, explicit entrypoint for deploying and proving the test bot.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ACTION="${1:-}"
[[ $# -gt 0 ]] && shift
SHA=""
NOTE=""
TARGET="portfolio_guru_test_bot"
WIDER=0
JOURNEY_OPTIONS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) SHA="${2:?missing SHA}"; shift 2 ;;
    --note) NOTE="${2:?missing note}"; shift 2 ;;
    --target) TARGET="${2:?missing target}"; shift 2 ;;
    --wider) WIDER=1; shift ;;
    --only) JOURNEY_OPTIONS+=(--only "${2:?missing journey ids}"); shift 2 ;;
    --changed|--full) JOURNEY_OPTIONS+=("$1"); shift ;;
    *) echo "Unknown stage option: $1" >&2; exit 64 ;;
  esac
done
case "$ACTION" in deploy|smoke|approve|status) ;; *) echo "Usage: scripts/stage.sh deploy|smoke|approve|status [--sha <40hex>] [--note <line>] [--wider] [--only <ids>|--changed|--full (smoke only)]"; exit 64 ;; esac
if [[ "$WIDER" == 1 && "$ACTION" != smoke ]]; then echo "--wider is only valid for smoke" >&2; exit 64; fi
if [[ ${#JOURNEY_OPTIONS[@]} -gt 0 && "$ACTION" != smoke ]]; then echo "Journey selection is only valid for smoke" >&2; exit 64; fi
if [[ ${#JOURNEY_OPTIONS[@]} -gt 2 || ( ${#JOURNEY_OPTIONS[@]} == 2 && "${JOURNEY_OPTIONS[0]}" != --only ) ]]; then
  echo "--only, --changed and --full are mutually exclusive" >&2; exit 64
fi
# deploy, smoke and status default to HEAD. approve never does: the approval must
# name the exact SHA Moeed tried, and HEAD may have moved since.
if [[ "$ACTION" == approve && -z "$SHA" ]]; then echo "approve needs --sha <40hex>: the SHA Moeed tried on the test bot" >&2; exit 64; fi
SHA="${SHA:-$(git -C "$ROOT" rev-parse HEAD)}"
[[ "$SHA" =~ ^[0-9a-fA-F]{40}$ ]] || { echo "Full 40-hex SHA required" >&2; exit 64; }
SHA="$(printf '%s' "$SHA" | tr '[:upper:]' '[:lower:]')"
PROOF_TOOL="$ROOT/scripts/staging_proof.py"
STAGING_DIR="${PORTFOLIO_GURU_STAGING_DIR:-$HOME/projects/portfolio-guru-staging}"

case "$ACTION" in
  deploy)
    [[ -z "$(git -C "$ROOT" status --porcelain)" ]] || { echo "Staging deploy requires a clean tree" >&2; exit 1; }
    head="$(git -C "$ROOT" rev-parse HEAD)"
    [[ "$SHA" == "$head" ]] || { echo "Deploy the checked-out HEAD so the clean tree is the tested candidate" >&2; exit 1; }
    branch="$(git -C "$ROOT" branch --show-current)"
    [[ -n "$branch" ]] || { echo "Staging deploy requires a branch" >&2; exit 1; }
    [[ "$branch" != main ]] || { echo "Staging deploy refuses main: work on a feature branch" >&2; exit 1; }
    git -C "$ROOT" fetch --prune origin '+refs/heads/*:refs/remotes/origin/*'
    if [[ -z "$(git -C "$ROOT" for-each-ref --format='%(refname)' --contains "$SHA" refs/remotes/origin/)" ]]; then
      git -C "$ROOT" push -u origin "$branch"
    fi
    exec bash "$ROOT/scripts/deploy_staging.sh" "$SHA"
    ;;
  smoke)
    # Refuse redirection before touching services, receipts, credentials or QA.
    for name in TELEGRAM_BOT_USERNAME RELEASE_LIVE_TARGET; do
      value="${!name:-portfolio_guru_test_bot}"
      [[ "${value#@}" == portfolio_guru_test_bot ]] || { echo "Staging smoke refuses any target other than portfolio_guru_test_bot" >&2; exit 21; }
    done
    [[ "$TARGET" == portfolio_guru_test_bot ]] || { echo "Staging smoke refuses any target other than portfolio_guru_test_bot" >&2; exit 21; }
    for name in TELEGRAM_LIVE_ALLOWED_BOTS RELEASE_LIVE_ALLOWLIST; do
      value="${!name:-portfolio_guru_test_bot}"
      [[ "$value" == portfolio_guru_test_bot ]] || { echo "Staging smoke requires the singleton test-bot allowlist" >&2; exit 21; }
    done
    STAGING_DIR="$(python3 - "$STAGING_DIR" "$ROOT" "$(git -C "$ROOT" rev-parse --git-common-dir)" <<'PY'
import sys
from pathlib import Path
target, root, common = map(Path, sys.argv[1:])
root = root.resolve()
common = (root / common).resolve() if not common.is_absolute() else common.resolve()
target = target.expanduser().resolve()
for forbidden in (Path.home() / 'projects/portfolio-guru-live', common.parent, root):
    forbidden = forbidden.resolve()
    if target == forbidden or forbidden in target.parents or target in forbidden.parents:
        raise SystemExit('Refusing staging smoke in live or development checkout')
if not (target / '.git').is_dir() or (target / '.git').is_symlink():
    raise SystemExit('Staging smoke requires its own plain clone')
print(target)
PY
)"
    python3 "$PROOF_TOOL" status --sha "$SHA" >/dev/null
    LOCK="${PORTFOLIO_GURU_STAGING_DEPLOY_LOCK:-/tmp/portfolio-guru-staging-deploy.lock}"
    mkdir "$LOCK" 2>/dev/null || { echo "Another staging deploy/proof is running" >&2; exit 1; }
    trap 'rmdir "$LOCK"' EXIT
    [[ "$(git -C "$STAGING_DIR" rev-parse HEAD)" == "$SHA" && -z "$(git -C "$STAGING_DIR" status --porcelain --untracked-files=no)" ]] || { echo "Staging checkout does not match clean SHA $SHA" >&2; exit 1; }
    verify_staging() {
      python3 "$ROOT/scripts/verify_live_runtime.py" --service-label com.portfolioguru.staging-bot \
        --root "$STAGING_DIR" --identity /tmp/portfolio-guru-staging-runtime.json --expected-sha "$SHA"
    }
    # Clear prior passing smoke/approval before trying: a failed or interrupted
    # rerun must not leave a passing receipt usable for promotion.
    python3 "$PROOF_TOOL" automated --sha "$SHA" --result fail
    verify_staging
    # Moeed's own Telethon session, fetched from BWS into this child only.
    if [[ -z "${TELETHON_SESSION:-}" && -f "$HOME/.openclaw/.bws-token" ]]; then
      bws_value() {
        BWS_ACCESS_TOKEN="$(cat "$HOME/.openclaw/.bws-token")" "${BWS_BIN:-$(command -v bws || echo "$HOME/.cargo/bin/bws")}" \
          secret get "$1" --output json | python3 -c "import json,sys; print(json.load(sys.stdin)['value'])"
      }
      TELETHON_SESSION="$(bws_value 75a32db8-ca75-4b1e-be13-b4560177a6b6)"
      TELETHON_API_ID="$(bws_value 6c7d0f75-0470-49cc-8d0c-b41201441b61)"
      TELETHON_API_HASH="$(bws_value c12e7352-2756-4d91-af4e-b41201443d74)"
      export TELETHON_SESSION TELETHON_API_ID TELETHON_API_HASH
    fi
    # Routine smoke reuses unchanged proof. --full forces all sixteen sends.
    COVERAGE="$STAGING_DIR/.artifacts/telegram-bot-qa/staging-coverage.json"
    rm -f "$COVERAGE"
    if [[ ${#JOURNEY_OPTIONS[@]} == 0 ]]; then JOURNEY_OPTIONS=(--changed); fi
    if env -u PORTFOLIO_GURU_APP_DIR RELEASE_LIVE_TARGET=portfolio_guru_test_bot RELEASE_LIVE_ALLOWLIST=portfolio_guru_test_bot \
      TELEGRAM_BOT_USERNAME=portfolio_guru_test_bot TELEGRAM_LIVE_ALLOWED_BOTS=portfolio_guru_test_bot \
      TELEGRAM_LIVE_APPROVED=portfolio-guru-live-qa-approved RUN_LIVE_TELEGRAM=1 \
      JOURNEY_RUNTIME_SHA="$SHA" TELEGRAM_JOURNEY_COVERAGE_REPORT="$COVERAGE" \
      bash "$STAGING_DIR/scripts/telegram_bot_qa.sh" --wider-journeys "${JOURNEY_OPTIONS[@]}"; then
      verify_staging
      python3 "$PROOF_TOOL" automated --sha "$SHA" --result pass --coverage "$COVERAGE" --root "$STAGING_DIR"
    else
      echo "Staging automated smoke failed; receipt remains failed" >&2
      exit 1
    fi
    ;;
  approve) python3 "$PROOF_TOOL" approve --sha "$SHA" --note "$NOTE" --root "$ROOT" ;;
  status) python3 "$PROOF_TOOL" status --sha "$SHA" ;;
esac
