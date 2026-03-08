# TASK: Generic Browser-Use Filer + Filer Router

## Goal
Add a browser-use powered universal filer that can fill ANY e-portfolio form on any platform,
alongside the existing deterministic Kaizen filer. The router automatically picks the right approach.

## Architecture

### Files to create/modify:
1. `backend/browser_filer.py` — NEW: Generic browser-use filer for unmapped portfolios
2. `backend/filer_router.py` — NEW: Routes filing requests to the right filer
3. `backend/selector_logger.py` — NEW: Logs DOM selectors from browser-use sessions for learning
4. `backend/bot.py` — MODIFY: Use filer_router instead of kaizen_filer directly
5. `backend/kaizen_filer.py` — KEEP AS-IS: Deterministic Playwright filer for Kaizen

### Flow:
```
User approves draft
  → bot.py calls filer_router.route_filing(platform, form_type, fields, creds)
  → filer_router checks: does a deterministic mapping exist for this platform+form_type?
    → YES: call kaizen_filer.file_to_kaizen() (or future platform-specific filers)
    → NO: call browser_filer.file_with_browser_use()
  → both return same result format: {status, filled, skipped, error}
```

## 1. browser_filer.py

### Purpose
Use browser-use Agent to navigate to any e-portfolio, log in, find the right form,
and fill it field by field using AI navigation.

### Implementation

```python
async def file_with_browser_use(
    platform_url: str,        # e.g. "https://eportfolio.rcem.ac.uk" or "https://soar.nhs.uk"
    form_url: str | None,     # Direct form URL if known, None if agent needs to navigate
    form_name: str,           # Human-readable: "Case-Based Discussion"
    fields: dict[str, Any],   # field_key → value pairs from extractor
    credentials: dict,        # {"username": "...", "password": "..."}
    curriculum_links: list[str] | None = None,
    model: str = "gemini-3-flash-preview",  # Default to Gemini; upgrade to GPT-4o for complex forms
) -> dict:
    """Returns {status, filled, skipped, error, selectors_log}"""
```

### Key design decisions:
- Use browser-use's `sensitive_data` parameter for credentials (masks them in LLM context)
- Use `allowed_domains` on BrowserProfile to prevent credential leakage to other sites
- Build task prompt dynamically from fields dict — each field becomes a clear instruction
- `step_timeout=180` (3 min per step — e-portfolios are slow)
- `max_steps=40` (login ~5 steps + navigate ~5 + fill ~20 fields + save ~5 + buffer)
- After filling each field, the agent should verify the value was set correctly
- Register `register_new_step_callback` to log DOM selectors used at each step
- Save conversation path for debugging: `~/.openclaw/data/portfolio-guru/browser-use-logs/`
- Generate GIF of the session for debugging: saved to same directory
- `use_vision=True` (essential for reading form layouts)
- Model selection: start with Gemini 3 Flash (free), fall back to GPT-4o if Flash fails

### Task prompt template:
```
You are filling in an e-portfolio form for a medical trainee.

CREDENTIALS (use the sensitive_data placeholders):
- Username: {x_username}
- Password: {x_password}

STEPS:
1. Go to {platform_url}
2. Log in with the credentials above
3. Navigate to: {form_url or "find the form called: " + form_name}
4. Fill in each field as specified below
5. After filling each field, verify the value appears correctly
6. Save as DRAFT — NEVER submit, NEVER send to supervisor/assessor

FIELDS TO FILL:
{for each field_key, value in fields.items():}
- Field labelled "{field_key_to_label(field_key)}": Enter "{value}"
{endfor}

{if curriculum_links:}
CURRICULUM CHECKBOXES:
Find the curriculum/KC section and tick these items: {curriculum_links}
{endif}

CRITICAL RULES:
- NEVER click Submit, Send, or any button that sends to a supervisor
- Only click Save, Save Draft, or Save as Draft
- If you cannot find a field, skip it and note which field was missing
- If the page doesn't load or login fails, stop immediately and report
- Wait for pages to fully load before interacting (SPAs may take 10-20 seconds)
```

### Error handling:
- Timeout: 5 minutes total (vs 3 min for deterministic)
- If agent reports "login failed" → return {status: "failed", error: "Login failed — check credentials"}
- If agent reports "form not found" → return {status: "failed", error: "Could not find form on this platform"}
- If agent fills some but not all fields → return {status: "partial", filled: [...], skipped: [...]}

### Selector logging (for the learning loop):
The `register_new_step_callback` captures each step's action. Parse these to extract:
- DOM selectors used (CSS selectors, XPath)
- Field labels matched
- Values entered
Save to `~/.openclaw/data/portfolio-guru/selector-logs/{platform}/{form_type}/{timestamp}.json`

## 2. filer_router.py

### Purpose
Single entry point for all filing. Decides whether to use deterministic or browser-use.

```python
# Registry of deterministic filers
DETERMINISTIC_FILERS = {
    "kaizen": {
        "module": "kaizen_filer",
        "function": "file_to_kaizen",
        "supported_forms": [...all 19 form types...],
    },
    # Future: "soar", "horus", "llp" etc.
}

async def route_filing(
    platform: str,            # "kaizen", "soar", "horus", etc.
    form_type: str,           # "CBD", "DOPS", etc.
    fields: dict,
    credentials: dict,        # {"username": "...", "password": "..."}
    curriculum_links: list[str] | None = None,
    platform_url: str | None = None,  # Required for browser-use path
    form_url: str | None = None,      # Direct form URL if known
    form_name: str | None = None,     # Human-readable form name
) -> dict:
    """
    Routes to deterministic filer if mapping exists, otherwise browser-use.
    Returns: {status, filled, skipped, error, method: "deterministic"|"browser-use"}
    """
```

### Logic:
1. Check if `platform` is in DETERMINISTIC_FILERS
2. If yes, check if `form_type` is in that platform's supported forms
3. If both yes → call deterministic filer
4. Otherwise → call browser_filer.file_with_browser_use()
5. Add `method` key to result so bot.py can show appropriate messaging

## 3. selector_logger.py

### Purpose
Log and analyse DOM selectors from browser-use sessions. Future: auto-generate Playwright mappings.

```python
def log_selectors(platform: str, form_type: str, selectors: list[dict]) -> str:
    """Save selector log. Returns path to log file."""

def get_selector_history(platform: str, form_type: str) -> list[dict]:
    """Get all logged selectors for a platform+form combination."""

def analyse_selectors(platform: str, form_type: str) -> dict | None:
    """
    If enough consistent selector data exists (3+ successful filings),
    return a candidate deterministic mapping.
    Returns None if not enough data.
    """
```

This is the learning loop foundation. For v2, we can add:
- Auto-generate `{platform}_filer.py` from consistent selector patterns
- Confidence scoring (how many times each selector succeeded vs failed)
- Human review step before promoting a mapping to deterministic

## 4. bot.py changes

### Import change:
```python
# Old:
from kaizen_filer import file_to_kaizen, FORM_UUIDS
# New:
from filer_router import route_filing
from kaizen_filer import FORM_UUIDS  # Still need UUIDs for Kaizen URLs
```

### In handle_approval_approve:
Replace the direct `file_to_kaizen()` call with `route_filing()`.

The `platform` parameter comes from user's profile (stored at setup time).
For now, all users are on Kaizen, so default to "kaizen".

### Future: platform selection at setup
Add `AWAIT_PLATFORM` state after training level:
- "Which e-portfolio platform do you use?"
- Buttons: Kaizen | Horus | SOAR | Other
- Store in UserProfile

For now, hardcode "kaizen" as default platform.

## 5. Model selection strategy

### For browser-use:
- **Primary: Gemini 3 Flash** — free tier, handles simple forms
- **Fallback: GPT-4o** — if Flash fails or for complex SPAs
  - Requires OPENAI_API_KEY env var (exists in BWS as credentials.OPENAI_API_KEY)
- **Future: Claude Sonnet** — if needed for specific platforms

### Cost profile:
- Deterministic Playwright: $0.00 per filing
- Browser-use + Gemini Flash: ~$0.00-0.01 per filing (free tier)
- Browser-use + GPT-4o: ~$0.02-0.05 per filing
- Browser-use + Claude Sonnet: ~$0.03-0.06 per filing

## Dependencies

### Already installed:
- browser-use 0.12.1 (includes playwright)
- google-genai (for Gemini)

### May need:
- openai (for GPT-4o fallback) — check if browser-use bundles it

## Testing plan

1. Test filer_router → kaizen path (should work exactly as before)
2. Test browser_filer on Kaizen CBD (known form, can compare against deterministic)
3. Test browser_filer on a different platform (if Moeed has access to Horus/SOAR)
4. Verify selector logging captures usable data
5. Verify timeout handling (5 min browser-use vs 3 min deterministic)

## Safety constraints (CRITICAL)
- NEVER submit any form — always save as draft
- NEVER send to supervisor/assessor
- Credentials passed via browser-use sensitive_data (masked in LLM context)
- allowed_domains locks browser to the target platform only
- Browser-use session timeout: 5 minutes max
- If browser-use fails, return clean error — never leave browser hanging
