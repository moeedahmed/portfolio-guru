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
# Configure (env):
#   PORTFOLIO_GURU_DATA_DIR  source data dir (default ~/.openclaw/data/portfolio-guru)
#   PG_BACKUP_DIR            local archive dir (default ~/.openclaw/backups/portfolio-guru)
#   PG_BACKUP_REMOTE         off-device target — REQUIRED for real protection.
#                            Either an rclone remote ("gdrive:pg-backups") or an
#                            rsync/path target ("/Volumes/Backup/pg" or "user@host:/path").
#   PG_BACKUP_RETAIN_DAYS    local retention (default 30)
#
# Restore: see scripts/restore_db.md
#
set -euo pipefail

DATA_DIR="${PORTFOLIO_GURU_DATA_DIR:-$HOME/.openclaw/data/portfolio-guru}"
LOCAL_DEST="${PG_BACKUP_DIR:-$HOME/.openclaw/backups/portfolio-guru}"
REMOTE_DEST="${PG_BACKUP_REMOTE:-}"
RETAIN_DAYS="${PG_BACKUP_RETAIN_DAYS:-30}"

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

# 4) Off-device copy — the part that actually survives a disk failure.
if [ -n "$REMOTE_DEST" ]; then
  if command -v rclone >/dev/null 2>&1 && [[ "$REMOTE_DEST" == *:* && "$REMOTE_DEST" != /* && "$REMOTE_DEST" != *@*:* ]]; then
    rclone copy "$LOCAL_DEST/$ARCHIVE" "$REMOTE_DEST" && echo "Copied off-device via rclone -> $REMOTE_DEST"
  else
    rsync -a "$LOCAL_DEST/$ARCHIVE" "$REMOTE_DEST/" && echo "Copied off-device via rsync -> $REMOTE_DEST"
  fi
else
  echo "WARNING: PG_BACKUP_REMOTE is not set — this backup is ON-DEVICE ONLY and will NOT"
  echo "         survive a disk failure. Set PG_BACKUP_REMOTE to an off-device target"
  echo "         (external disk path, iCloud path, rsync host, or rclone remote)."
fi

# 5) Prune old local archives.
find "$LOCAL_DEST" -name 'portfolio-guru-backup-*.tar.gz' -mtime +"$RETAIN_DAYS" -delete 2>/dev/null || true

echo "Backup complete."
