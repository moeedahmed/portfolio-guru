# Restoring a Portfolio Guru backup

Backups are produced by `scripts/backup_db.sh` (scheduled daily via
`scripts/com.portfolioguru.backup.plist`). Each archive is a timestamped
`portfolio-guru-backup-YYYYMMDD-HHMMSS.tar.gz` containing consistent SQLite
snapshots plus the pickle/JSON state.

## What's inside

- `portfolio_guru.db` — Fernet-encrypted Kaizen credentials.
- `usage.db` — usage / tier / billing state.
- `bot_persistence` — PicklePersistence (drafts, conversation/flow state).
- `health_profiles.json`, `filing_coverage.json` — state files.
- `drafts/` — in-progress case drafts.

## Restore steps

1. **Stop the bot** so nothing writes while you restore:
   ```
   launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.portfolioguru.bot.plist
   ```
2. **Pick the archive** (newest by default):
   ```
   ARCHIVE=$(ls -t ~/.openclaw/backups/portfolio-guru/portfolio-guru-backup-*.tar.gz | head -1)
   ```
   (or a specific off-device copy from `PG_BACKUP_REMOTE`.)
3. **Extract into the data dir** (back up the current state first):
   ```
   DATA=~/.openclaw/data/portfolio-guru
   mv "$DATA" "${DATA}.pre-restore-$(date +%Y%m%d-%H%M%S)"   # keep current state, never rm
   mkdir -p "$DATA"
   tar -xzf "$ARCHIVE" -C "$DATA"
   ```
4. **Verify the SQLite dbs** before starting:
   ```
   sqlite3 "$DATA/portfolio_guru.db" "PRAGMA integrity_check;"   # expect: ok
   sqlite3 "$DATA/usage.db"          "PRAGMA integrity_check;"   # expect: ok
   ```
5. **Confirm the Fernet key matches** the one the bot runs with (from BWS /
   `FERNET_SECRET_KEY`). The credentials in `portfolio_guru.db` are encrypted —
   a different key means they won't decrypt. The key is NOT in the backup by
   design; it lives in BWS.
6. **Restart the bot:**
   ```
   launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.portfolioguru.bot.plist
   ```

## Off-device protection (do this)

On-device backups don't survive a disk failure. Set `PG_BACKUP_REMOTE` in the
launchd plist (`EnvironmentVariables`) to an off-device target — an external
disk path, an iCloud Drive path, an `rsync` host (`user@host:/path`), or an
`rclone` remote (`gdrive:pg-backups`) — then reload the agent:

```
launchctl bootout  "gui/$(id -u)" ~/Library/LaunchAgents/com.portfolioguru.backup.plist 2>/dev/null || true
cp scripts/com.portfolioguru.backup.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.portfolioguru.backup.plist
```
