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

## Off-device protection (LIVE)

Every nightly backup is **gpg-encrypted (AES256)** and uploaded to an EU bucket
**`gs://portfolio-guru-eu-backups`** (London, `portfolio-guru-eu` project, 90-day
lifecycle). The local copy stays unencrypted for quick restore; the off-device
copy is always encrypted. The encryption passphrase lives in BWS
(`PG_BACKUP_GPG_PASSPHRASE`) — it is NOT on disk and NOT in the backup, so the
bucket alone is useless to anyone without it.

### Restore from the off-device (encrypted) backup

If the Mac is gone, restore from the bucket:

```
# 1. fetch the passphrase from BWS (on a machine with the BWS token)
export BWS_ACCESS_TOKEN=$(cat ~/.openclaw/.bws-token)
PASS=$(bws secret list --output json | python3 -c "import json,sys;print(next(s['value'] for s in json.load(sys.stdin) if s['key']=='PG_BACKUP_GPG_PASSPHRASE'))")

# 2. download the newest encrypted archive
LATEST=$(gcloud storage ls gs://portfolio-guru-eu-backups/ | tail -1)
gcloud storage cp "$LATEST" /tmp/restore.tar.gz.gpg

# 3. decrypt, then follow the restore steps above with /tmp/restore.tar.gz
printf '%s' "$PASS" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 -o /tmp/restore.tar.gz -d /tmp/restore.tar.gz.gpg
```

> ⚠️ The `PG_BACKUP_GPG_PASSPHRASE` BWS secret is the single key to every
> off-device backup. If BWS is lost, the bucket backups cannot be decrypted —
> keep a sealed offline copy of that passphrase somewhere safe.

### Future (full cloud deployment)

When the data layer moves to managed cloud Postgres (Supabase), provider-managed
daily backups + point-in-time recovery replace this script; the bucket can remain
as an extra archival layer.
