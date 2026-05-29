# Beta Fixes Deployed + Remaining Gaps

**Date:** 2026-05-29
**Live commit:** `8ff8b41`
**Tests:** 826 passed, 9 deselected, 3 snapshots

## What's Now Live

| Fix | Commit | Description |
|---|---|---|
| SAS/CESR portfolio detection | `ec1b2a4` | Auto-detects SAS/CESR/non-trainee from Kaizen dashboard; SAS option in settings |
| Gathering mode enabled | `08d3b95` | `PG_GATHERING_MODE=1` by default; old vNext bot killed |
| CDP re-login on session expiry | `cd619cc` | Re-authenticates in persistent Chrome profile before headless fallback |
| Reconnect Kaizen button | `8ff8b41` | "🔑 Reconnect Kaizen" / "🔄 Try Again" / "🆕 Start fresh" on login failure |

## Remaining Gaps (Not Yet Fixed)

### 1. Draft recovery after filing failure (Post-filing — HIGH PRIORITY)
When DOPS/KC filing fails mid-way, the user is shown "Filing didn't complete" and the draft is preserved in the `AWAIT_APPROVAL` state (returned from `handle_approval_approve`). But if the user taps "File another case" instead of "Try again", the draft context is lost and they have to re-type everything. The fix: if filing fails, the "File another case" button should save the current draft to the user's pending cases list so they can resume it later, or simply redirect back to the draft instead of starting fresh.

### 2. Kaizen session expiry detection (Detection — MEDIUM PRIORITY)
The CDP re-login fires *after* the user sees "Login failed". Better: check session health before starting the filing attempt, so users get a "Your session has expired, tap to reconnect" before the filing attempt even starts. Could be done as a periodic heartbeat check on the CDP page's Kaizen session cookie.

### 3. DOPS specific: quality gate errors vs infrastructure errors (Error communication — MEDIUM)
When DOPS quality gate blocks (`dops_quality_gate` in `dops_filing.py`), the user sees "DOPS draft is missing required Kaizen fields: ..." — that's clear. But if the infrastructure fails (login, CDP connection, Chrome crash), the error is more generic. Could differentiate between "infrastructure" and "data quality" failures in the user message.

### 4. SAS detection: verify with real SAS doctor (Validation — LOW PRIORITY)
The SAS detection looks for "sas", "cesr", "non-trainee" in Kaizen body text — but I'm not 100% sure Kaizen renders those labels on a SAS doctor's dashboard. Dr Sana Zehra's screenshot didn't show us the actual Kaizen page (it showed the Telegram bot). Need to log in as a SAS account and check the body text that `detect_portfolio_type` actually reads. If Kaizen doesn't render SAS indicators, auto-detection won't work and all SAS users will need to manually pick their level in `/settings` — which is acceptable but not ideal.

### 5. Multi-turn gather mode recovery (UX - LOW PRIORITY)
If gathering mode is active and a user sends a case over multiple messages, then filing fails — the multi-turn context is in `context.user_data[_GATHERING_CASE_KEY]` and should be preserved for retry. Currently it's cleared on filing attempt.
