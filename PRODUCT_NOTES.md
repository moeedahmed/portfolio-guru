
## v2.1 Product Vision (captured 2026-03-07)

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
