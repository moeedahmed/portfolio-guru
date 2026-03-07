
## Versioning Standard (adopted 2026-03-07)

**Major versions (v1, v2, v3)** — complete rebuilds or fundamental architecture shifts.
- v1: initial bot (basic filing, no multimodal, no intent classifier)
- v2: current — multimodal input, intent classifier, form type recommendations, approval gate

**Minor versions (v2.1, v2.2, ...)** — a coherent batch of features, incremented only when that batch is fully shipped and verified end-to-end. Never used as a label for "future stuff" or "backlog".

**No patch versions** — bug fixes are part of the current minor version until it ships.

**Rule:** "v2.1 scope" or "next batch" = not yet shipped. "v2.1" = shipped and verified.

---

## v2.1 Scope (not yet shipped — captured 2026-03-07)

### Portfolio type selection
- First-time users select which portfolio system they use (Kaizen, SOAR, LLP, etc.)
- "Connect Kaizen" becomes "Connect Portfolio" with a type picker
- Each portfolio type gets its own filer module
- Start with Kaizen only, expand gradually

### Usage limits (monetisation gate)
- Free tier: limited number of filings per month (TBD — e.g. 5/month)
- After limit hit: prompt to upgrade
- Paid tier: unlimited filings
- Stripe integration when ready

### Credentials UX
- Never ask for credentials again once saved
- "Connect Kaizen" button checks first — if already connected, shows "Kaizen connected ✅" + option to change account
- Settings menu accessible at any time for: change portfolio, change credentials, view usage

### End of case flow
- After successful filing: "Are we done?" with buttons — Done / Edit this entry / File another case
- "Done" clears state fully
- "File another case" resets immediately to input prompt

### Case history
- Every filed case saved to SQLite with: date, form type, title/presentation, Kaizen URL
- User can say "edit my last case" or "edit the CBD from Tuesday"
- Bot retrieves from history, browser-use opens the draft for editing
