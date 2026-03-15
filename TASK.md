# TASK — Two targeted copy/UX fixes

## Fix 1: Partial filing message — "Filled: 7 · Skipped: 2" is unclear

In backend/bot.py, find the partial status message (search for "some fields may be incomplete").

The current message reads:
"⚠️ QIAT draft saved but some fields may be incomplete. Filled: 7 · Skipped: 2. Review in your portfolio..."

Users don't understand what "Skipped" means — sounds like an error.

Replace the partial status block with a message that:
- Says fields were left blank intentionally (not enough info, not a bug)
- Names the skipped fields (up to 3, then "+ N more")
- Tells them what to do (open Kaizen, fill those fields manually, then assign assessor)

Target wording:
"✅ [Form] draft saved to Kaizen.
[N] fields filled from your case.
[N] left blank — not enough info to fill without guessing: [field1, field2].
Open your portfolio, complete those fields, then assign an assessor."

Use the `skipped` list already available in that code block to generate the field names.
Clean up the names for display: replace underscores with spaces, title case.

## Fix 2: Error handler — stop spawning new messages on each retry

In backend/bot.py, find the global error_handler function (search for "Something went wrong while filing").

Currently it calls `reply_text` which spawns a new message every time the user taps Retry.
Tapping Retry 3 times = 3 identical error messages.

Change the error handler to use `_edit_last_bot_msg` (already exists in bot.py) instead of
`reply_text`. If there's no prior bot message to edit, fall back to reply_text.

Same change applies to the ACTION|retry_filing handler — find it and apply the same fix.

## Files to change
- backend/bot.py only

## Must not change
- Filing logic, form schemas, credential handling, conversation flow

## Final step
Restart the bot:
```bash
pkill -f "bot.py" || true
sleep 2
cd /Users/moeedahmed/projects/portfolio-guru/backend && nohup venv/bin/python3 bot.py >> /tmp/portfolio-guru-bot.log 2>&1 &
```

Then deliver:
```
openclaw message send --account builder --channel telegram --target -1003705494413 --thread-id 844 -m "✅ Done: partial filing message now explains skipped fields clearly. Error handler no longer spawns duplicate messages on retry."
```
