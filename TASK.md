# TASK: Standardise Gemini model to gemini-3-flash-preview across all bots

## Context
Moeed wants gemini-3-flash-preview as the standard model across all bots.
gemini-3-flash-preview is the latest available Flash model (stable gemini-3-flash doesn't exist yet).
gemini-2.5-flash is the stable fallback.
gemini-2.0-flash and gemini-2.0-flash-lite are retiring June 2026 — remove from all fallback chains.

## Files to change in /Users/moeedahmed/projects/portfolio-guru/backend/

### 1. browser_use_starter.py
- Line ~42: change `model="gemini-2.0-flash-lite"` → `model="gemini-3-flash-preview"`
- Update the comments (lines 5-6, 39-40) to reflect the new model choice

### 2. vision.py
- Line ~64: `models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash"]`
  → change to `models_to_try = ["gemini-3-flash-preview", "gemini-2.5-flash"]`

### 3. whisper.py
- Line ~86: `models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash"]`
  → change to `models_to_try = ["gemini-3-flash-preview", "gemini-2.5-flash"]`

### 4. documents.py
- Line ~67: `models_to_try = ["gemini-2.5-flash-preview-04-17", "gemini-2.0-flash"]`
  → change to `models_to_try = ["gemini-3-flash-preview", "gemini-2.5-flash"]`

### Files already correct (do NOT change):
- filer.py — already uses gemini-3-flash-preview ✅
- discover_uuids.py — already uses gemini-3-flash-preview ✅
- browser_filer.py — already uses gemini-3-flash-preview ✅
- extractor.py — uses gemini-2.5-flash as primary (acceptable, leave as-is)

## Files to change in /Users/moeedahmed/projects/emgurus-hub/supabase/functions/exam-api/index.ts

### 5. exam-api/index.ts
Three lines to change:
- Line ~123: `gemini-3-pro-preview` (deprecated) → `gemini-3-flash-preview`
- Line ~615: `gemini-2.0-flash` → `gemini-3-flash-preview`
- Line ~880: `gemini-2.0-flash` → `gemini-3-flash-preview`

## What NOT to change
- Do NOT change gemini-2.5-flash where it appears as a PRIMARY model in extractor.py — it's acceptable
- Do NOT change anything in venv/ or node_modules/
- Do NOT change any other files
- Do NOT modify openclaw.json

## Verification
After changes:
```bash
grep -rn "gemini-2.0-flash\|gemini-1\|gemini-3-pro-preview\|gemini-2.5-flash-preview-04-17" \
  /Users/moeedahmed/projects/portfolio-guru/backend/*.py \
  /Users/moeedahmed/projects/emgurus-hub/supabase/functions/exam-api/index.ts \
  2>/dev/null | grep -v venv | grep -v ".git"
```
This should return NO results (all old models replaced).

Then commit in each repo:
```bash
cd /Users/moeedahmed/projects/portfolio-guru
git add backend/browser_use_starter.py backend/vision.py backend/whisper.py backend/documents.py
git commit -m "chore: standardise Gemini model to gemini-3-flash-preview across all backends"

cd /Users/moeedahmed/projects/emgurus-hub
git add supabase/functions/exam-api/index.ts
git commit -m "chore: upgrade exam-api edge function to gemini-3-flash-preview"
```

## Delivery
```
openclaw message send --account builder --channel telegram --target -1003705494413 --thread-id 784 -m "💻 Done: Gemini model standardised to gemini-3-flash-preview

What changed:
• Portfolio Guru: browser_use_starter, vision, whisper, documents all upgraded (was gemini-2.0 / stale preview)
• Exam API edge function: 3 endpoints upgraded (was gemini-2.0-flash and the deprecated gemini-3-pro)
• All fallback chains now use gemini-2.5-flash (stable) as backup

Verified: grep confirms no gemini-2.0 or stale preview strings remain
Committed in both repos — nothing deployed yet"
```
