#!/usr/bin/env bash
#
# Off-device backup of Portfolio Guru's canonical state.
#
# The bot's source of truth lives on a single Mac Mini disk: encrypted SQLite
# (credentials + usage/billing), the PicklePersistence file (drafts/flow
# state), and a few JSON state files. With no backup, a disk failure is total
# user-data loss — the #1 operational gap from the production-readiness audit.
#
# This script makes CONSISTENT snapshots (sqlite .backup, never a raw copy of a
# live db), archives them with a timestamp, copies them OFF-DEVICE if a remote
# is configured, and prunes old archives.
#
# Off-device copies are ALWAYS gpg-encrypted (clinical data must not leave the
# box in the clear). The local copy stays unencrypted for easy restore.
#
# Configure (env):
#   PORTFOLIO_GURU_DATA_DIR  source data dir (default ~/.openclaw/data/portfolio-guru)
#   PG_BACKUP_DIR            local archive dir (default ~/.openclaw/backups/portfolio-guru)
#   PG_BACKUP_REMOTE         off-device target (default gs://portfolio-guru-eu-backups).
#                            One of: a GCS bucket ("gs://bucket"), an rclone remote
#                            ("gdrive:pg"), or an rsync/path ("/Volumes/Backup/pg").
#   PG_BACKUP_GPG_PASSPHRASE symmetric passphrase for off-device encryption. If unset,
#                            fetched from BWS (key PG_BACKUP_GPG_PASSPHRASE).
#   PG_BACKUP_RETAIN_DAYS    local retention (default 30; the GCS bucket has its own
#                            90-day lifecycle rule).
#
# Restore: see scripts/restore_db.md
#
set -euo pipefail

DATA_DIR="${PORTFOLIO_GURU_DATA_DIR:-$HOME/.openclaw/data/portfolio-guru}"
LOCAL_DEST="${PG_BACKUP_DIR:-$HOME/.openclaw/backups/portfolio-guru}"
REMOTE_DEST="${PG_BACKUP_REMOTE:-gs://portfolio-guru-eu-backups}"
RETAIN_DAYS="${PG_BACKUP_RETAIN_DAYS:-30}"

# Resolve tool paths (launchd runs with a minimal PATH).
GCLOUD="$(command -v gcloud || echo /opt/homebrew/bin/gcloud)"
GPG="$(command -v gpg || echo /opt/homebrew/bin/gpg)"
BWS_BIN="$(command -v bws || echo "$HOME/.cargo/bin/bws")"

# gcloud needs Python >= 3.10. Under launchd's minimal PATH it otherwise picks
# macOS's system Python 3.9 and dies with CommandLoadFailure — which is exactly
# how every off-device upload silently failed between 2026-06-26 and 2026-08-17.
# Pin an interpreter we have verified, rather than trusting PATH order.
if [ -z "${CLOUDSDK_PYTHON:-}" ]; then
  for _py in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.13 \
             /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3 /usr/bin/python3; do
    if [ -x "$_py" ] && "$_py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      CLOUDSDK_PYTHON="$_py"
      break
    fi
  done
fi
export CLOUDSDK_PYTHON

# Off-device encryption passphrase: env first, else BWS.
# One BWS round trip, then read whatever keys we need out of it.
_BWS_CACHE=""
bws_key() {
  [ -f "$HOME/.openclaw/.bws-token" ] && [ -x "$BWS_BIN" ] || return 0
  if [ -z "$_BWS_CACHE" ]; then
    _BWS_CACHE="$(BWS_ACCESS_TOKEN="$(cat "$HOME/.openclaw/.bws-token")" "$BWS_BIN" secret list --output json 2>/dev/null || true)"
  fi
  [ -n "$_BWS_CACHE" ] || return 0
  printf '%s' "$_BWS_CACHE" | python3 -c "
import json,sys
try: print(next((s['value'] for s in json.load(sys.stdin) if s.get('key')=='$1'), ''))
except Exception: print('')
" 2>/dev/null || true
}

GPG_PASS="${PG_BACKUP_GPG_PASSPHRASE:-}"
[ -z "$GPG_PASS" ] && GPG_PASS="$(bws_key PG_BACKUP_GPG_PASSPHRASE)"

# Dead-man's-switch ping URL (Healthchecks.io). Optional; absent = no-op.
# Skipped entirely when notifications are disabled: the BWS lookup is itself a
# network call, and this script runs inside the OFFLINE verification gates.
if [ "${PG_BACKUP_DISABLE_ALERTS:-0}" = "1" ]; then
  PG_BACKUP_HEALTHCHECK_URL=""
else
  PG_BACKUP_HEALTHCHECK_URL="${PG_BACKUP_HEALTHCHECK_URL:-$(bws_key PG_BACKUP_HEALTHCHECK_URL)}"
fi

# Alert the operator on Telegram. A backup that fails quietly is worse than no
# backup, because it also buys false confidence — so every failure path below
# routes through here as well as the log.
alert_operator() {
  local text="$1"
  # Tests and dry runs must never page the operator or reach for BWS.
  [ "${PG_BACKUP_DISABLE_ALERTS:-0}" = "1" ] && return 0
  local token="${TELEGRAM_BOT_TOKEN:-}"
  local chat_id="${PG_OPERATOR_CHAT_ID:-6912896590}"
  if [ -z "$token" ] && [ -f "$HOME/.openclaw/.bws-token" ] && [ -x "$BWS_BIN" ]; then
    token="$(BWS_ACCESS_TOKEN="$(cat "$HOME/.openclaw/.bws-token")" "$BWS_BIN" secret get af553b7d-5c05-418a-b80e-b405015708ed --output json 2>/dev/null \
      | python3 -c "import json,sys;print(json.load(sys.stdin)['value'])" 2>/dev/null || true)"
  fi
  [ -z "$token" ] && return 0
  curl -sS --max-time 10 -X POST "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat_id}" \
    --data-urlencode "text=🚨 Portfolio Guru backup: ${text}" >/dev/null 2>&1 || true
}

if [ ! -d "$DATA_DIR" ]; then
  echo "ERROR: data dir not found: $DATA_DIR" >&2
  alert_operator "data dir not found: $DATA_DIR — no backup was taken."
  exit 1
fi

TS="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="portfolio-guru-backup-${TS}.tar.gz"
STAGE_DIR="$(mktemp -d)"
cleanup() { rm -rf "$STAGE_DIR"; }
trap cleanup EXIT

mkdir -p "$LOCAL_DEST"

# 1) Consistent SQLite snapshots. .backup is safe against a live, in-use db.
for db in portfolio_guru.db usage.db; do
  if [ -f "$DATA_DIR/$db" ]; then
    sqlite3 "$DATA_DIR/$db" ".backup '$STAGE_DIR/$db'"
  fi
done

# 2) Flat-file state (pickle + JSON). copy -p preserves timestamps.
for f in bot_persistence health_profiles.json chase_log.json filing_coverage.json; do
  [ -f "$DATA_DIR/$f" ] && cp -p "$DATA_DIR/$f" "$STAGE_DIR/"
done

# 3) In-progress drafts (small, user-visible work).
[ -d "$DATA_DIR/drafts" ] && cp -Rp "$DATA_DIR/drafts" "$STAGE_DIR/drafts"

tar -czf "$LOCAL_DEST/$ARCHIVE" -C "$STAGE_DIR" .
SIZE="$(du -h "$LOCAL_DEST/$ARCHIVE" | cut -f1)"
echo "Backup written: $LOCAL_DEST/$ARCHIVE ($SIZE)"

# 4) Off-device copy — the part that survives a disk failure. Encrypt first
#    (gpg symmetric, AES256), then upload only the encrypted .gpg.
OFFDEVICE_OK=0
OFFDEVICE_ERR=""

if [ -n "$REMOTE_DEST" ]; then
  if [ -z "$GPG_PASS" ]; then
    OFFDEVICE_ERR="no encryption passphrase available (PG_BACKUP_GPG_PASSPHRASE env or BWS secret); refusing to send clinical data off-device in the clear"
    echo "ERROR: $OFFDEVICE_ERR" >&2
  else
    ENC="$LOCAL_DEST/${ARCHIVE}.gpg"
    printf '%s' "$GPG_PASS" | "$GPG" --batch --yes --quiet --pinentry-mode loopback \
      --passphrase-fd 0 --symmetric --cipher-algo AES256 -o "$ENC" "$LOCAL_DEST/$ARCHIVE"
    # Never discard the transport error: the whole 53-night silent failure was a
    # `>/dev/null 2>&1` hiding a gcloud CommandLoadFailure.
    UPLOAD_LOG="$(mktemp)"
    if [[ "$REMOTE_DEST" == gs://* ]]; then
      if "$GCLOUD" storage cp "$ENC" "$REMOTE_DEST/" >"$UPLOAD_LOG" 2>&1; then
        # Upload exiting 0 is a claim, not proof. Confirm the object is listable
        # before reporting the backup as protected.
        if "$GCLOUD" storage ls "$REMOTE_DEST/${ARCHIVE}.gpg" >/dev/null 2>&1; then
          OFFDEVICE_OK=1
          echo "Uploaded off-device (encrypted) -> $REMOTE_DEST/${ARCHIVE}.gpg"
        else
          OFFDEVICE_ERR="upload reported success but $REMOTE_DEST/${ARCHIVE}.gpg is not listable"
        fi
      else
        OFFDEVICE_ERR="$(tail -5 "$UPLOAD_LOG" | tr '\n' ' ')"
      fi
    elif command -v rclone >/dev/null 2>&1 && [[ "$REMOTE_DEST" == *:* && "$REMOTE_DEST" != /* && "$REMOTE_DEST" != *@*:* ]]; then
      if rclone copy "$ENC" "$REMOTE_DEST" >"$UPLOAD_LOG" 2>&1; then
        OFFDEVICE_OK=1
        echo "Copied off-device (encrypted) via rclone -> $REMOTE_DEST"
      else
        OFFDEVICE_ERR="$(tail -5 "$UPLOAD_LOG" | tr '\n' ' ')"
      fi
    else
      if rsync -a "$ENC" "$REMOTE_DEST/" >"$UPLOAD_LOG" 2>&1; then
        OFFDEVICE_OK=1
        echo "Copied off-device (encrypted) via rsync -> $REMOTE_DEST"
      else
        OFFDEVICE_ERR="$(tail -5 "$UPLOAD_LOG" | tr '\n' ' ')"
      fi
    fi
    rm -f "$UPLOAD_LOG"
    rm -f "$ENC"  # local keeps the plain archive for restore; off-device keeps the encrypted one
  fi
else
  OFFDEVICE_ERR="PG_BACKUP_REMOTE is empty — ON-DEVICE ONLY (won't survive disk failure)"
  echo "WARNING: $OFFDEVICE_ERR"
fi

# 5) Prune old local archives.
find "$LOCAL_DEST" -name 'portfolio-guru-backup-*.tar.gz' -mtime +"$RETAIN_DAYS" -delete 2>/dev/null || true

# 6) Report honestly. A local-only backup does not survive the disk it is
#    guarding, so off-device failure is a FAILED backup run, not a warning:
#    alert the operator and exit non-zero so the failure is visible.
# Dead-man's switch. Telegram alerts only fire when the script RUNS and fails;
# they cannot report a run that never happened (launchd unloaded, Mac asleep,
# disk full). Healthchecks.io alerts on the ABSENCE of a ping, which covers the
# case no in-script check ever can. No-op when unset.
ping_healthcheck() {
  if [ "${PG_BACKUP_DISABLE_ALERTS:-0}" = "1" ]; then
    echo "healthcheck: skipped (alerts disabled)"
    return 0
  fi
  [ -n "${PG_BACKUP_HEALTHCHECK_URL:-}" ] || return 0
  curl -sS -m 10 --retry 3 "${PG_BACKUP_HEALTHCHECK_URL}${1:-}" >/dev/null 2>&1 || true
}

if [ "$OFFDEVICE_OK" -eq 1 ]; then
  echo "Backup complete (off-device copy verified)."
  ping_healthcheck
else
  echo "BACKUP FAILED off-device: $OFFDEVICE_ERR" >&2
  echo "Local archive is intact at $LOCAL_DEST/$ARCHIVE but is NOT disaster-proof." >&2
  alert_operator "off-device copy FAILED — $OFFDEVICE_ERR. Local backup intact but not disaster-proof."
  ping_healthcheck "/fail"
  exit 1
fi
