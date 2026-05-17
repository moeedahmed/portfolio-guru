# Portfolio Guru — Workflow Overhaul Plan

## Goal
Smooth, auto-detecting, role-aware setup that works for any Kaizen portfolio type (HST, ACCS, Intermediate, Assessor, Non-Trainee). No confusing manual steps, no Playwright leftover, no stale config.

## Phase 1: Auto-detect during setup ✅
Replace the manual training level picker with automatic detection. When user enters credentials, test them via the engine, read the dashboard title, set the role automatically. Only fall back to manual picker if detection fails.

**Done:**
- `_test_kaizen_login()` → uses `engine.KaizenProvider.connect()` + `portfolio_type`
- Returns `bool | str` (role string or False)
- `setup_password()` → stores detected role via `store_training_level()`
- Shows "detected as Higher Specialist Trainee / ACCS / Clinical Supervisor"
- Fallback manual picker if detection fails
- ASSESSOR added to `TRAINING_LEVEL_LABELS` and `TRAINING_LEVEL_FORMS`

## Phase 2: Credential verification via engine ✅
`_test_kaizen_login()` uses `engine.KaizenProvider` instead of Playwright headless Chromium.

**Done:**
- Old Playwright login code removed from bot.py
- Uses real Chrome CDP via browser-harness
- Auto-discovers CDP WebSocket URL from `localhost:9222`

## Phase 3: /start setup nudge ✅ (pre-existing)
If user has no stored credentials, `/start` shows "🔗 Connect Kaizen" button.

**Done:** `_BTN_SETUP` shown when `not has_credentials()`

## Phase 4: Multi-curriculum support ❌
Store all curricula detected during setup. Add curriculum switching in settings.

**Not yet done.** Would need:
- `store_curricula()` / `get_curricula()` in profile_store.py
- Detect from dashboard Goals page
- Curriculum switch in /settings

## Phase 5: Old Playwright code cleanup ❌
`kaizen_form_filer.py` still has old Playwright code. Should be archived.

---

## Bugs encountered

1. **ACP edit collision** — acpx session overwrote manual edits to bot.py because uncommitted changes weren't stashed before spawning.
   *Fix:* `git stash` before ACP, `git stash pop` after.

2. **Variable name mismatch** — auto-detect code used `login_result` but try/except stored to `login_ok`.
   *Fix:* Changed to consistent variable name.

3. **Rogue bot process** — old launchd instance (PID 23015) survived and held Telegram connection, causing 409 Conflicts for new instances.
   *Fix:* Kill all bot processes before restarting.

4. **Chrome CDP not running** — engine couldn't connect because Chrome with `--remote-debugging-port=9222` wasn't started.
   *Fix:* Start Chrome with remote debugging before bot starts.
   *Better fix:* KaizenProvider auto-discovers CDP URL from `localhost:9222/json/version`.

5. **Stale .env TELEGRAM_BOT_TOKEN** — portfolio .env had career-guru token under TELEGRAM_BOT_TOKEN, causing token conflict.
   *Fix:* Removed TELEGRAM_BOT_TOKEN from portfolio .env; using PORTFOLIO_GURU_TOKEN instead.

## Known issues

- Clearing Telegram chat history does NOT clear stored credentials (stored in PicklePersistence on server)
- Bot doesn't auto-restart Chrome if it crashes
- Career-guru bot's launchd may restart and conflict if using same token (separate launchd jobs, should use different tokens)
