# Portfolio Guru — v2 Fixes Batch

## Fix 1 — Gemini 503 retry logic

In `backend/extractor.py`, wrap every Gemini API call (classify_intent, recommend_form_types, extract_cbd_data, answer_question) with retry logic:
- Retry up to 3 times on any exception whose message contains "503", "UNAVAILABLE", or "overloaded"
- Wait 2 seconds between retries
- Only raise the error after all retries are exhausted
- Use a simple helper: `def _gemini_call_with_retry(fn, *args, retries=3, delay=2)`

## Fix 2 — Clean state after filing or cancellation

In `backend/bot.py`, ensure `context.user_data.clear()` is called in ALL exit paths:
- After successful Kaizen filing (confirmation sent)
- After user taps ❌ Cancel at any stage
- After /reset command
- After /cancel command
- After any unrecoverable error in the filing flow

Currently state bleeds between cases. Every ConversationHandler.END return that follows a completed action must be preceded by context.user_data.clear().

## Fix 3 — Kaizen UUID discovery via browser-use

Write a standalone script `backend/discover_uuids.py` that:

1. Loads Kaizen credentials from BWS:
   - Bot token BWS secret: `af553b7d-5c05-418a-b80e-b405015708ed`
   - Google API key BWS secret: `af6579a0-2cbe-4cef-94b3-b405017b48fe`
   - Fernet key BWS secret: `9e653679-9a33-4c23-a15c-b405015713de`
   - Load credentials from the SQLite DB at `~/.openclaw/data/portfolio-guru/portfolio_guru.db`
   - The DB has a `credentials` table — fetch username and decrypt password using the Fernet key
   - Use the first row (Moeed's credentials)

2. Uses browser-use + Gemini to log into Kaizen:
   - Navigate to https://kaizenep.com
   - Log in with the credentials
   - Navigate to the "New Assessment" or "New Entry" page
   - Look for all assessment type options/buttons/links
   - For each assessment type (CBD, DOPS, LAT, ACAT, mini-CEX, QIPAT, MSF, PS, STAT, or any others found):
     - Click/hover to get the URL or extract the UUID from the href or data attribute
     - The UUID format is: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
     - The URL pattern is: https://kaizenep.com/events/new-section/<UUID>

3. Prints a Python dict of discovered UUIDs to stdout:
   ```python
   FORM_UUIDS = {
       "CBD": "3ce5989a-b61c-4c24-ab12-711bf928b181",  # known
       "DOPS": "<discovered>",
       "LAT": "<discovered>",
       ...
   }
   ```

4. Updates `backend/extractor.py` FORM_UUIDS dict with any newly discovered UUIDs (only fill in None values — do not overwrite CBD which is already verified)

Use the same browser-use pattern as filer.py. Import from store.py/credentials.py for credential loading.

## What NOT to change
- `filer.py` — do not touch
- `store.py`, `credentials.py` — do not touch  
- `models.py`, `whisper.py`, `vision.py` — do not touch
- `main.py`, `run_local.sh` — do not touch

## Done criteria
- [ ] Gemini 503 errors auto-retry 3x before showing error to user
- [ ] context.user_data.clear() called on every exit path in bot.py
- [ ] discover_uuids.py runs and discovers UUIDs from Kaizen
- [ ] extractor.py FORM_UUIDS updated with discovered values
- [ ] Syntax check passes: python -c 'import bot, extractor'
