# Disaster recovery

What to do when Portfolio Guru is down. Written to be followed by someone who
is not Moeed, on a phone, without reading any other document first.

**Read the top section, do what it says, stop.** The rest is reference.

---

## Is it actually broken?

Send the bot a message on Telegram. If it replies, it is up.

If it does not reply within a minute, work down this list. Each step is
independent — do them in order and stop at the first one that fixes it.

### 1. Is the bot process running?

```bash
launchctl print "gui/$(id -u)/com.portfolioguru.bot" | grep -E "state|pid"
```

`state = running` with a pid means the process is alive. If it says anything
else, or there is no pid:

```bash
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.portfolioguru.bot.plist 2>/dev/null
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.portfolioguru.bot.plist
```

Wait 30 seconds, then message the bot again.

### 2. What does the log say?

```bash
tail -50 /tmp/portfolio-guru-bot.log
```

Most failures name themselves here. A repeated crash every few seconds means
launchd is restarting a bot that dies on startup — usually a missing secret or
a bad deploy. Go to step 3.

### 3. Undo the last deploy

If it broke right after a deploy, roll back to the previous commit:

```bash
cd /Users/moeedahmed/projects/portfolio-guru
git log --oneline -5          # find the commit before the bad one
git reset --hard <that-commit>
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.portfolioguru.bot.plist 2>/dev/null
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.portfolioguru.bot.plist
```

`scripts/deploy_mac.sh` normally does this automatically when its post-deploy
smoke check fails. Doing it by hand is for when it did not.

### 4. Is the machine itself gone?

Power cut, dead disk, stolen, drowned. Go to **Total rebuild** below.

---

## What being down actually costs

Reassuring, and worth knowing before anyone panics:

- **No messages are lost.** The bot uses Telegram polling, not webhooks.
  Telegram queues undelivered updates and the bot collects them on restart.
- **No payments are lost.** Stripe retries failed webhooks for days, and
  `stripe_handler.py` reconciles on top of that.
- **No filed evidence is lost.** Kaizen drafts are saved on Kaizen, not here.

What is genuinely at risk is only what lives on this one disk: stored Kaizen
credentials, usage and billing state, and in-progress drafts. That is what the
nightly backup protects, and why the restore drill matters.

---

## Total rebuild (the Mac Mini is gone)

Everything needed is off the machine already: code in GitHub, secrets in BWS,
encrypted backups in an EU bucket. Nothing here depends on recovering the
hardware.

**You need:** a Mac, the BWS access token, and the GitHub account.

1. **Install prerequisites** — Homebrew, `git`, `gpg`, `sqlite3`, Google
   Chrome, the `bws` CLI, and `gcloud` (authenticated as the account with
   access to `portfolio-guru-eu`).

2. **Restore the BWS token** to `~/.openclaw/.bws-token`. This is the master
   key to everything else — without it nothing below works. Keep a sealed
   offline copy somewhere physical.

3. **Clone the repo** to `/Users/<you>/projects/portfolio-guru` and create the
   Python virtualenv at `backend/venv`, then
   `venv/bin/python3 -m pip install -r backend/requirements.txt`.

4. **Restore the data** — follow `scripts/restore_db.md`, which covers pulling
   the newest encrypted archive from `gs://portfolio-guru-eu-backups`,
   decrypting it with the BWS passphrase, and proving the Fernet key still
   decrypts the credentials. **Do not skip the decrypt proof.** An archive that
   restores but will not decrypt is not a recovery.

5. **Log in to Kaizen once** if you need the debug browser profile:
   `bash backend/ensure_chrome.sh --visible`. Normal filing does not depend on
   this — each filing logs in with the user's own stored credentials — so treat
   it as optional.

6. **Install the services:**

   ```bash
   bash scripts/install_launchd.sh
   cp scripts/com.portfolioguru.backup.plist ~/Library/LaunchAgents/
   launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.portfolioguru.backup.plist
   ```

7. **Prove it works** before telling anyone it is back:
   ```bash
   bash scripts/verify_changed.sh     # expect: verify:changed PASSED
   bash scripts/backup_db.sh          # expect: off-device copy verified
   ```
   Then send the bot a real case on Telegram and confirm it drafts.

**Drill status: never timed on clean hardware.** The data-restore half was
proven on 2026-08-18 (see `scripts/restore_db.md`); the full machine rebuild has
not been rehearsed, so treat the recovery time as unknown rather than short.

---

## What breaks silently, and what watches it

The two worst failures here have both been failures of _silence_, not of
crashing. Anything added to this system should be judged on whether it fails
loudly.

| Risk                               | What watches it                                      | Alerts via                                          |
| ---------------------------------- | ---------------------------------------------------- | --------------------------------------------------- |
| Bot process dies                   | launchd `KeepAlive` restarts it                      | — (automatic)                                       |
| Bot alive but wedged (not polling) | 5-min heartbeat ping from `bot.py`                   | Healthchecks.io, if `PG_HEARTBEAT_URL` set          |
| Bad deploy                         | post-deploy smoke + auto-rollback in `deploy_mac.sh` | CI turns red                                        |
| Off-device backup fails            | upload verified + non-zero exit                      | Telegram, and Healthchecks.io `/fail`               |
| Backup never runs at all           | absence of a ping                                    | Healthchecks.io, if `PG_BACKUP_HEALTHCHECK_URL_PRODUCTION` set |
| Whole machine offline              | nothing on the machine can report this               | Healthchecks.io absence alerts                      |

The last row is the point of an external monitor: no check that runs _on_ the
Mac Mini can tell you the Mac Mini is gone.

### Enabling the external monitor

Both hooks are wired and inert until given URLs. To turn them on, create two
checks at healthchecks.io (free tier covers 20) and store the ping URLs in BWS:

- `PG_HEARTBEAT_URL` — period 5 min, grace 15 min. Pinged by the bot.
- `PG_BACKUP_HEALTHCHECK_URL_PRODUCTION` — production-only cron `30 3 * * *`,
  grace 2 h. Pinged by the nightly backup, with `/fail` on failure. Tests must
  set `PG_BACKUP_DISABLE_ALERTS=1` and must never resolve this BWS secret.

Point both at Moeed's phone. Restart the bot after adding the secrets.

---

## Known single points of failure

Honest list, so nobody discovers these during an incident:

1. **The BWS access token.** Every secret depends on it. Losing it makes the
   off-device backups undecryptable. Keep a sealed offline copy.
2. **One machine, one home.** Power, internet, and hardware are unmitigated by
   design — see `docs/adr-hosting-2026-08.md` for why that was accepted and the
   named conditions that would change it.
3. **Kaizen is third-party.** Their UI changes break filing selectors without
   notice. Not recoverable from here; it needs a code fix.
4. **Only Moeed has done this.** Steps above are written for someone else, but
   nobody else has actually executed them.
