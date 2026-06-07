#!/usr/bin/env bash
# Portfolio Guru — private-beta dogfood smoke checklist.
#
# Default mode is a manual checklist. This script does NOT call Telegram,
# Kaizen, the LLM, or the live filer. It guides the operator through each
# check and records pass/fail/skip + a free-text note to a timestamped
# artefact under docs/continuity/dogfood/.
#
# Live Kaizen filing is intentionally NOT automated here — beta dogfood
# uses real credentials and real tickets, so the operator runs the live
# leg by hand inside Telegram while this script captures the outcome.
#
# Usage:
#   bash scripts/dogfood_smoke.sh
#   bash scripts/dogfood_smoke.sh --no-record   # print checklist only
#
# Exits non-zero if any check is recorded as FAIL.

set -euo pipefail

RECORD=1
if [[ "${1:-}" == "--no-record" ]]; then
  RECORD=0
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTEFACT_DIR="${ROOT}/docs/continuity/dogfood"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
ARTEFACT="${ARTEFACT_DIR}/smoke-${STAMP}.md"

if [[ "$RECORD" == "1" ]]; then
  mkdir -p "$ARTEFACT_DIR"
  {
    printf '# Dogfood smoke — %s\n\n' "$STAMP"
    printf 'Branch: %s\n' "$(git -C "$ROOT" branch --show-current 2>/dev/null || echo unknown)"
    printf 'Commit: %s\n\n' "$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    printf '| # | Check | Result | Note |\n'
    printf '|---|-------|--------|------|\n'
  } > "$ARTEFACT"
fi

PASS=0
FAIL=0
SKIP=0

ask() {
  local num="$1"
  local title="$2"
  local body="$3"

  printf '\n--- %s. %s ---\n' "$num" "$title"
  printf '%s\n' "$body"

  if [[ "$RECORD" != "1" ]]; then
    return 0
  fi

  local choice=""
  while [[ -z "$choice" ]]; do
    printf 'Result [p=pass / f=fail / s=skip]: '
    read -r choice </dev/tty
    case "$choice" in
      p|P) choice=pass ;;
      f|F) choice=fail ;;
      s|S) choice=skip ;;
      *) choice="" ;;
    esac
  done

  printf 'Note (one line, optional): '
  local note=""
  read -r note </dev/tty

  case "$choice" in
    pass) PASS=$((PASS+1)) ;;
    fail) FAIL=$((FAIL+1)) ;;
    skip) SKIP=$((SKIP+1)) ;;
  esac

  local safe_note="${note//|/\\|}"
  printf '| %s | %s | %s | %s |\n' "$num" "$title" "$choice" "$safe_note" >> "$ARTEFACT"
}

cat <<'INTRO'
Portfolio Guru — private-beta dogfood smoke checklist.

This is a guided checklist. It does not touch Telegram, Kaizen, the LLM, or
the live filer. Run the bot actions yourself in Telegram against the live
@portfolio_guru_bot (or your dev bot) using your own account, then come back
to this terminal and record pass/fail/skip for each step.

Hard rules for this run:
  - Use your own Telegram account and your own Kaizen credentials.
  - Do not submit / sign / send drafts from Kaizen during the smoke. Open
    them, eyeball them, leave them as drafts.
  - For the supervisor save-draft check, only use a disposable / unfilled
    CBD ticket you control. Skip the check otherwise.

INTRO

ask 1 "launchd service is up" \
"On the Mac Mini, run:
  launchctl print gui/\$(id -u)/com.portfolioguru.bot | head -25
Expect: a recent pid, no last-exit-code loop, log paths reachable.
Pass if the service is running and not crash-looping."

ask 2 "logs reachable and clean" \
"Run:
  tail -n 50 /tmp/portfolio-guru-bot.log
  tail -n 50 ~/Library/Logs/portfolio-guru/launchd.err.log
Expect: startup commit/branch line, PTB poll started, no Traceback /
unexpected ERROR since last restart.
Pass if logs are clean."

ask 3 "/start replies for connected operator" \
"In Telegram, send /start to the bot from your operator account.
Expect: welcome message, Connect Kaizen / What is this? / File a case
keyboard, no errors.
Pass if welcome bubble arrives within a few seconds."

ask 4 "text case → recommendation → draft" \
"Send a text case describing a clinical encounter (real or synthetic, do
not include PHI in a synthetic case).
Expect: form recommendation (up to 3 forms + Cancel), then on tapping a
form, a draft preview with File / Edit / Cancel.
Pass if you reach a draft preview."

ask 5 "voice case → recommendation → draft" \
"Send a voice note describing the same kind of case.
Expect: 'Transcribing voice note…' ack → 'voice note read' → form
recommendation → draft preview.
Pass if you reach a draft preview from a voice note."

ask 6 "photo case → recommendation → draft" \
"Send a photo of clinical notes (or a synthetic / placeholder image of
text). Use the dev account if you don't want to share real notes.
Expect: 'Reading image…' ack → 'image read' → form recommendation →
draft preview. NOT_CLINICAL responses are acceptable for placeholder
images; rerun with a clinical photo or skip.
Pass if a clinical photo reaches a draft preview."

ask 7 "edit a draft field" \
"From the draft preview, tap Edit, pick one field (e.g. Reflection),
send a new value.
Expect: updated preview, original keyboard reappears.
Pass if the field is updated and the preview redraws cleanly."

ask 8 "cancel returns to idle" \
"From any active state (draft preview / edit prompt), tap Cancel or send
/reset.
Expect: 'Cancelled.' or reset confirmation, no orphan keyboards.
Pass if the next /start works cleanly."

ask 9 "stale-button recovery" \
"Trigger a stale callback: open a draft preview, wait ~45s without
tapping, then tap one of the original buttons.
Expect: 'That earlier button is no longer active.' (or similar) with a
fresh recovery keyboard — never a dead end.
Pass if the bot recovers without crashing."

ask 10 "save as draft to Kaizen (operator-only)" \
"From a fresh draft preview on a disposable form, tap Save as draft.
Expect: 'Saving … as a Kaizen draft…' progress edits, then '✅ … saved.'
with the post-save keyboard. Open Kaizen and confirm the draft exists in
your activities list. Do NOT submit / sign / send it.
Pass if a draft appears in Kaizen and the bot reported success.
Skip if you do not want to write a live draft on this run."

ask 11 "supervisor save-draft confirmation boundary (operator-only)" \
"Only do this if you have a disposable / unfilled CBD ticket on a
supervisor account. Otherwise skip.
Steps:
  - As the supervisor user, receive the notification for the test ticket.
  - Tap Open. Capture a feedback intent (text or voice).
  - Review the local draft preview.
  - Tap 'Prepare Kaizen action plan (no write)'. Expect a plan with the
    explicit safety boundary copy.
  - Tap '📤 Save draft in Kaizen'. Expect a SEPARATE confirmation
    message naming the action and offering Yes / Cancel.
  - Tap Cancel on this run.
Expect: no Kaizen write at all on Cancel; session preserved. The Yes
path is also acceptable on a disposable unfilled CBD ticket if you want
to exercise it.
Pass if the confirmation step appears and Cancel leaves Kaizen
untouched.
Skip if you do not have a disposable supervisor CBD ticket."

ask 12 "no submit / sign / send happened" \
"Open Kaizen in the browser. Inspect the activity log / drafts list for
the account(s) used above.
Expect: no events submitted, signed, sent for review, approved,
rejected, or deleted during this smoke. Drafts only.
Pass if Kaizen state matches expectation."

if [[ "$RECORD" == "1" ]]; then
  {
    printf '\n## Summary\n\n'
    printf -- '- Pass: %s\n' "$PASS"
    printf -- '- Fail: %s\n' "$FAIL"
    printf -- '- Skip: %s\n' "$SKIP"
  } >> "$ARTEFACT"

  printf '\nArtefact: %s\n' "$ARTEFACT"
  printf 'Pass=%s Fail=%s Skip=%s\n' "$PASS" "$FAIL" "$SKIP"
fi

if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
