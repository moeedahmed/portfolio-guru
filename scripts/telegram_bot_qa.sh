#!/usr/bin/env bash
set -euo pipefail

ROOT="${PORTFOLIO_GURU_APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BACKEND="${ROOT}/backend"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
ARTIFACT_ROOT="${TELEGRAM_BOT_QA_ARTIFACT_ROOT:-${ROOT}/.artifacts/telegram-bot-qa}"
ARTIFACT_DIR="${ARTIFACT_ROOT}/${STAMP}"
RUN_LIVE="${RUN_LIVE_TELEGRAM:-auto}"
REQUIRE_LIVE="${REQUIRE_TELEGRAM_LIVE:-0}"
LIVE_APPROVAL_VALUE="portfolio-guru-live-qa-approved"
DEFAULT_LIVE_ALLOWLIST="portfolio_guru_bot"
FOCUSED_RELEASE=0

# Captured read-only before backend/.env is read. A release live proof is
# approved for one exact bot and one frozen singleton allowlist; the environment
# file loaded below could otherwise export TELEGRAM_BOT_USERNAME and
# TELEGRAM_LIVE_ALLOWED_BOTS, so the approved values are held here as readonly
# shell variables, the dotenv loader is refused the names that could redirect
# them, and the environment actually in force is checked again afterwards
# rather than read at send time.
APPROVED_LIVE_TARGET="${RELEASE_LIVE_TARGET:-}"
APPROVED_LIVE_TARGET="${APPROVED_LIVE_TARGET#@}"
readonly APPROVED_LIVE_TARGET
APPROVED_LIVE_ALLOWLIST="${RELEASE_LIVE_ALLOWLIST:-$APPROVED_LIVE_TARGET}"
readonly APPROVED_LIVE_ALLOWLIST
TARGET_REFUSED_EXIT=21
readonly TARGET_REFUSED_EXIT
# Names backend/.env may never set during a release live proof: the approved
# target, its allowlist, the live guard, the release values themselves, and the
# tool search path this script resolves its interpreter from.
DOTENV_PROTECTED_NAMES="TELEGRAM_BOT_USERNAME TELEGRAM_LIVE_ALLOWED_BOTS TELEGRAM_LIVE_APPROVED RELEASE_LIVE_TARGET RELEASE_LIVE_ALLOWLIST PATH"
readonly DOTENV_PROTECTED_NAMES

while [[ $# -gt 0 ]]; do
  case "$1" in
    --focused-release) FOCUSED_RELEASE=1 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 64 ;;
  esac
  shift
done

mkdir -p "$ARTIFACT_DIR"

cd "$BACKEND"

if [[ -x "venv/bin/python3" ]]; then
  PY="venv/bin/python3"
elif [[ -x ".venv/bin/python3" ]]; then
  PY=".venv/bin/python3"
elif [[ -x "../.venv/bin/python3" ]]; then
  PY="../.venv/bin/python3"
else
  PY="python3"
fi

if [[ -f ".env" ]]; then
  # During a release live proof (RELEASE_LIVE_TARGET set) the protected names
  # are dropped from the dotenv load rather than exported; they are reported so
  # a misconfigured .env is visible, but they can no longer move the target.
  PROTECTED_NAMES_IN_FORCE=""
  if [[ -n "$APPROVED_LIVE_TARGET" ]]; then PROTECTED_NAMES_IN_FORCE="$DOTENV_PROTECTED_NAMES"; fi
  eval "$(PROTECTED_NAMES="$PROTECTED_NAMES_IN_FORCE" FROZEN_TARGET="$APPROVED_LIVE_TARGET" "$PY" - <<'PY'
from pathlib import Path
import os
import re
import shlex
import sys

protected = set(os.environ.get("PROTECTED_NAMES", "").split())
for raw_line in Path(".env").read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("export "):
        line = line[len("export "):].strip()
    if "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    target = os.environ.get("FROZEN_TARGET", "")
    if key in protected:
        mismatch = False
        if key == "TELEGRAM_BOT_USERNAME":
            mismatch = value.lstrip("@") != target
            message = "changed the live Telegram target to @" + value.lstrip("@")
        elif key == "TELEGRAM_LIVE_ALLOWED_BOTS":
            mismatch = target not in [v.strip().lstrip("@") for v in value.split(",")]
            message = "approved target is not on the allowlist"
        if mismatch:
            print("printf '%s\\n' " + shlex.quote("ERROR: " + message + ". Nothing was sent.") + " >&2; exit 21")
            break
        print(f"dotenv: ignored protected name {key} during a release live proof", file=sys.stderr)
        continue
    if target and (key.startswith("APPROVED_") or key in {
        "PY", "ROOT", "BACKEND", "FOCUSED_RELEASE", "RUN_LIVE", "REQUIRE_LIVE",
        "DEFAULT_LIVE_ALLOWLIST", "LIVE_APPROVAL_VALUE", "TARGET_REFUSED_EXIT",
        "DOTENV_PROTECTED_NAMES", "PROTECTED_NAMES_IN_FORCE", "BASH_ENV", "ENV",
        "SHELLOPTS", "BASHOPTS", "IFS", "CDPATH", "PYTHONPATH", "PYTHONHOME",
    }):
        print("printf '%s\\n' 'ERROR: dotenv attempted to replace a release control. Nothing was sent.' >&2; exit 21")
        break
    print(f"export {key}={shlex.quote(value)}")
PY
)"
fi

allowlist_names_target() {
  local target="$1" list="$2" entry saved_ifs="$IFS"
  IFS=','
  for entry in $list; do
    entry="${entry//[[:space:]]/}"
    entry="${entry#@}"
    if [[ -n "$entry" && "$entry" == "$target" ]]; then
      IFS="$saved_ifs"
      return 0
    fi
  done
  IFS="$saved_ifs"
  return 1
}

# Revalidate the approved target against the environment that is actually in
# force now, after the dotenv load. Nothing has been sent at this point, so a
# mismatch exits before any live step rather than messaging the wrong bot.
if [[ -n "$APPROVED_LIVE_TARGET" ]]; then
  EFFECTIVE_TARGET="${TELEGRAM_BOT_USERNAME:-}"
  EFFECTIVE_TARGET="${EFFECTIVE_TARGET#@}"
  if [[ -n "$EFFECTIVE_TARGET" && "$EFFECTIVE_TARGET" != "$APPROVED_LIVE_TARGET" ]]; then
    echo "ERROR: the environment load changed the live Telegram target from @${APPROVED_LIVE_TARGET} to @${EFFECTIVE_TARGET}." >&2
    echo "Refusing to run a live proof against a bot the approval never named. Nothing was sent." >&2
    exit "$TARGET_REFUSED_EXIT"
  fi
  EFFECTIVE_ALLOWLIST="${TELEGRAM_LIVE_ALLOWED_BOTS:-${APPROVED_LIVE_TARGET:-$DEFAULT_LIVE_ALLOWLIST}}"
  if ! allowlist_names_target "$APPROVED_LIVE_TARGET" "$EFFECTIVE_ALLOWLIST"; then
    echo "ERROR: live Telegram target @${APPROVED_LIVE_TARGET} is not on the allowlist in force after the environment load." >&2
    echo "Refusing to run a live proof outside the allowlist. Nothing was sent." >&2
    exit "$TARGET_REFUSED_EXIT"
  fi
  # The card froze a singleton allowlist. It must name the approved target and
  # nothing else, and it — not the wider environment — is what the live child
  # sees, so the only recipient a release proof can reach is the card's.
  if [[ -n "$APPROVED_LIVE_ALLOWLIST" ]]; then
    FROZEN_ALLOWLIST="${APPROVED_LIVE_ALLOWLIST//[[:space:]]/}"
    FROZEN_ALLOWLIST="${FROZEN_ALLOWLIST#@}"
    if [[ "$FROZEN_ALLOWLIST" != "$APPROVED_LIVE_TARGET" ]]; then
      echo "ERROR: the frozen release allowlist '${APPROVED_LIVE_ALLOWLIST}' is not exactly the approved target @${APPROVED_LIVE_TARGET}." >&2
      echo "Refusing to run a live proof with a recipient set the card never froze. Nothing was sent." >&2
      exit "$TARGET_REFUSED_EXIT"
    fi
    EFFECTIVE_ALLOWLIST="$FROZEN_ALLOWLIST"
  fi
  export TELEGRAM_BOT_USERNAME="$APPROVED_LIVE_TARGET"
  export TELEGRAM_LIVE_ALLOWED_BOTS="$EFFECTIVE_ALLOWLIST"
fi

SUMMARY="${ARTIFACT_DIR}/summary.md"
{
  printf '# Telegram bot QA\n\n'
  printf 'Started: %s\n' "$STAMP"
  printf 'Repo: Portfolio Guru\n'
  printf 'Branch: %s\n' "$(git -C "$ROOT" branch --show-current 2>/dev/null || echo unknown)"
  printf 'Commit: %s\n' "$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  printf 'Python: %s\n\n' "$PY"
} > "$SUMMARY"

run_step() {
  local name="$1"
  shift
  local log="${ARTIFACT_DIR}/${name}.log"
  printf 'Running %s...\n' "$name"
  if "$@" >"$log" 2>&1; then
    printf -- '- %s: PASS\n' "$name" >> "$SUMMARY"
  else
    printf -- '- %s: FAIL\n' "$name" >> "$SUMMARY"
    tail -80 "$log"
    exit 1
  fi
}

run_step collect-live-tests "$PY" -m pytest tests/test_e2e.py tests/test_e2e_live.py --collect-only -q -m "e2e or live"

run_step offline-bot-gate "$PY" -m pytest \
  tests/test_smoke.py \
  tests/test_flow_walker.py \
  tests/test_e2e_offline.py \
  tests/test_snapshots.py \
  tests/test_source_grounding.py \
  -q

HAS_TELETHON_ENV="$("$PY" - <<'PY'
from tests.telegram_live_harness import has_telethon_env
print("1" if has_telethon_env() else "0")
PY
)"

if [[ "$RUN_LIVE" == "0" || "$RUN_LIVE" == "false" ]]; then
  printf -- '- live-telegram: SKIP (disabled by RUN_LIVE_TELEGRAM)\n' >> "$SUMMARY"
elif [[ "$HAS_TELETHON_ENV" == "1" ]]; then
  printf 'Live Telegram QA approved for target: %s\n' "${TELEGRAM_BOT_USERNAME:-portfolio_guru_bot}" >> "$SUMMARY"
  if [[ "$FOCUSED_RELEASE" == "1" ]]; then
    TELEGRAM_E2E_ARTIFACT_DIR="$ARTIFACT_DIR" run_step live-telegram-focused "$PY" -m pytest \
      tests/test_e2e.py::test_e2e_case_text_enters_draft_flow \
      -q \
      -m e2e
  else
    TELEGRAM_E2E_ARTIFACT_DIR="$ARTIFACT_DIR" run_step live-telegram "$PY" -m pytest \
      tests/test_e2e.py \
      tests/test_e2e_live.py \
      -q \
      -m "e2e or live"
  fi
else
  if [[ -n "${TELETHON_SESSION:-}" && -n "${TELEGRAM_API_ID:-${TELETHON_API_ID:-}}" && -n "${TELEGRAM_API_HASH:-${TELETHON_API_HASH:-}}" && "${TELEGRAM_LIVE_APPROVED:-}" != "$LIVE_APPROVAL_VALUE" ]]; then
    printf -- '- live-telegram: SKIP (explicit approval missing)\n' >> "$SUMMARY"
    printf '  Set TELEGRAM_LIVE_APPROVED=%s only after Moeed approves this exact live run.\n' "$LIVE_APPROVAL_VALUE" >> "$SUMMARY"
  else
    printf -- '- live-telegram: SKIP (Telethon session/API env incomplete)\n' >> "$SUMMARY"
  fi
  if [[ "$REQUIRE_LIVE" == "1" || "$RUN_LIVE" == "1" || "$RUN_LIVE" == "true" ]]; then
    echo "ERROR: live Telegram QA required, but approval/credentials/target allowlist are incomplete."
    exit 20
  fi
fi

cat "$SUMMARY"
printf '\nArtifacts: %s\n' "$ARTIFACT_DIR"
