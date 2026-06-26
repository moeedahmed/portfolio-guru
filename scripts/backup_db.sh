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

# Off-device encryption passphrase: env first, else BWS.
GPG_PASS="${PG_BACKUP_GPG_PASSPHRASE:-}"
if [ -z "$GPG_PASS" ] && [ -f "$HOME/.openclaw/.bws-token" ] && [ -x "$BWS_BIN" ]; then
  GPG_PASS="$(BWS_ACCESS_TOKEN="$(cat "$HOME/.openclaw/.bws-token")" "$BWS_BIN" secret list --output json 2>/dev/null \
    | python3 -c "import json,sys;print(next((s['value'] for s in json.load(sys.stdin) if s.get('key')=='PG_BACKUP_GPG_PASSPHRASE'),''))" 2>/dev/null || true)"
fi

if [ ! -d "$DATA_DIR" ]; then
  echo "ERROR: data dir not found: $DATA_DIR" >&2
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
if [ -n "$REMOTE_DEST" ]; then
  if [ -z "$GPG_PASS" ]; then
    echo "ERROR: off-device target set but no encryption passphrase available" >&2
    echo "       (PG_BACKUP_GPG_PASSPHRASE env or BWS secret). Refusing to send" >&2
    echo "       clinical data off-device in the clear. Local backup is intact." >&2
  else
    ENC="$LOCAL_DEST/${ARCHIVE}.gpg"
    printf '%s' "$GPG_PASS" | "$GPG" --batch --yes --quiet --pinentry-mode loopback \
      --passphrase-fd 0 --symmetric --cipher-algo AES256 -o "$ENC" "$LOCAL_DEST/$ARCHIVE"
    if [[ "$REMOTE_DEST" == gs://* ]]; then
      "$GCLOUD" storage cp "$ENC" "$REMOTE_DEST/" >/dev/null 2>&1 \
        && echo "Uploaded off-device (encrypted) -> $REMOTE_DEST/${ARCHIVE}.gpg" \
        || echo "WARNING: off-device upload to $REMOTE_DEST failed (local backup intact)."
    elif command -v rclone >/dev/null 2>&1 && [[ "$REMOTE_DEST" == *:* && "$REMOTE_DEST" != /* && "$REMOTE_DEST" != *@*:* ]]; then
      rclone copy "$ENC" "$REMOTE_DEST" && echo "Copied off-device (encrypted) via rclone -> $REMOTE_DEST"
    else
      rsync -a "$ENC" "$REMOTE_DEST/" && echo "Copied off-device (encrypted) via rsync -> $REMOTE_DEST"
    fi
    rm -f "$ENC"  # local keeps the plain archive for restore; off-device keeps the encrypted one
  fi
else
  echo "WARNING: PG_BACKUP_REMOTE is empty — ON-DEVICE ONLY (won't survive disk failure)."
fi

# 5) Prune old local archives.
find "$LOCAL_DEST" -name 'portfolio-guru-backup-*.tar.gz' -mtime +"$RETAIN_DAYS" -delete 2>/dev/null || true

echo "Backup complete."
