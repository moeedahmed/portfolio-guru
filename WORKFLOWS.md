# Portfolio Guru — Workflow Reference
> Last updated: 2026-03-07
> Source of truth for all conversation flows. Update this file whenever a flow changes.
> Mermaid diagrams — render at mermaid.live or in any Markdown viewer that supports it.

---

## 1. First-Time User Flow

```mermaid
flowchart TD
    A([User sends /start]) --> B[Welcome message\n+ 3 buttons]
    B --> C{Button tapped}
    C -->|📋 What is this?| D[Explain Portfolio Guru\n— one message]
    C -->|🔑 Connect Kaizen| E{Credentials\nalready saved?}
    C -->|📤 File a case| F{Credentials\nalready saved?}
    
    E -->|No| G[Ask for Kaizen username]
    G --> H[Ask for Kaizen password\ndelete message immediately]
    H --> I[Encrypt + store in SQLite\nConfirm connected ✅]
    I --> J[Show File a case prompt]
    
    E -->|Yes| K[Kaizen already connected ✅\n+ File a case button]
    
    F -->|No| L[Please connect Kaizen first\n+ Connect Kaizen button]
    F -->|Yes| J
    
    D --> B
```

---

## 2. Core Filing Flow (Happy Path)

```mermaid
flowchart TD
    A([User sends case]) --> B{Intent classifier\nGemini}
    B -->|chitchat| C[Friendly reply\nPrompt to send a case]
    B -->|question| D[Answer via Gemini\nReturn to idle]
    B -->|case| E[Detect input type]
    
    E -->|Text| F[Use as-is]
    E -->|Voice note| G[Whisper transcription\n→ text]
    E -->|Photo| H[Gemini Vision extraction\n→ text]
    
    F & G & H --> I[recommend_form_types\nGemini analyses case]
    
    I --> J[Show form type buttons\ne.g. CBD / DOPS / LAT\n+ ❌ Cancel]
    
    J --> K{User taps form}
    K -->|❌ Cancel| L[Cancelled.\nClear state → END]
    K -->|Form selected| M[extract_cbd_data\nGemini extracts all fields\nHumanizer on reflection]
    
    M --> N[Show full draft preview\nDate · Setting · Presentation\nClinical reasoning · Reflection\nSLOs · Key Capabilities]
    N --> O[Approval buttons\n✅ File this draft\n✏️ Edit\n❌ Cancel]
    
    O -->|❌ Cancel| L
    O -->|✏️ Edit| P[Edit flow — see Flow 3]
    O -->|✅ File this draft| Q[browser-use opens Kaizen\nNavigates to form UUID URL\nFills all fields\nSaves as DRAFT only]
    
    Q -->|Success| R[✅ Saved as draft in Kaizen\nDate · Form type · SLOs\n\nDone / Edit entry / File another]
    Q -->|Failure| S[❌ Filing failed\nError detail\nRetry / Cancel]
    
    R -->|✅ Done| T[Clear state → END]
    R -->|📤 File another| U[Clear state\nPrompt for new case]
    R -->|✏️ Edit entry| V[Open Kaizen draft\nfor editing — see Flow 4]
    
    S -->|Retry| Q
    S -->|Cancel| L
```

---

## 3. Edit Flow (Before Filing)

```mermaid
flowchart TD
    A([User taps ✏️ Edit]) --> B[Which field to edit?\nField buttons:\nDate · Setting · Presentation\nClinical reasoning · Reflection\nSLOs · Cancel]
    
    B -->|Cancel| C[Back to draft preview\n+ approval buttons]
    B -->|Field selected| D[Ask for new value\nfree text]
    
    D --> E[Apply new value to draft]
    E --> F[Show updated draft preview]
    F --> G[Approval buttons again\n✅ File / ✏️ Edit / ❌ Cancel]
```

---

## 4. Edit Previously Filed Draft (v2.1 — not yet built)

```mermaid
flowchart TD
    A([User says 'edit my last case'\nor 'edit CBD from Tuesday']) --> B[Bot queries case history\nSQLite local DB]
    B -->|Found| C[Show case summary\n+ Confirm this one?]
    B -->|Not found| D[No matching case found\nAsk user to describe it]
    
    C -->|Yes| E[browser-use opens Kaizen\nnavigates to saved draft URL]
    C -->|No, different one| D
    
    E --> F[Show current field values\nfrom Kaizen]
    F --> G[Edit flow — same as Flow 3]
```

---

## 5. Reset / Recovery Flow

```mermaid
flowchart TD
    A([User sends /reset\nor taps Reset button]) --> B[Clear all context.user_data]
    B --> C[Conversation reset message\nSend me a case whenever ready]
    C --> D([Idle — waiting for input])
    
    E([Bot stuck mid-state\nuser sends new case]) --> F{Intent = case?}
    F -->|Yes| G[It looks like a new case.\nSend /reset to start fresh]
    F -->|No| H[Handle as chitchat/question\nStay in current state]
```

---

## 6. Form Type Decision Logic

```mermaid
flowchart TD
    A([Case text]) --> B[Always: CBD eligible\nif any clinical case managed]
    A --> C{Procedure performed\nby trainee?}
    A --> D{Leadership/coordination\nof team or resus?}
    A --> E{Full shift or\nmultiple patients?}
    
    C -->|Yes — LP, intubation,\ncentral line, chest drain etc| F[Add DOPS]
    D -->|Yes — led team,\nmajor incident, resus coord| G[Add LAT]
    E -->|Yes| H[Add ACAT]
    
    B & F & G & H --> I[Show as buttons\nmax 3 forms]
    
    J[DOPS/LAT/ACAT only show\nif UUID is mapped\nand trainee ACTUALLY did it\nnot just observed]
```

---

## 7. State Machine (Conversation States)

| State | Description | Exit triggers |
|-------|-------------|---------------|
| `IDLE` (no state) | Waiting for any input | Any message → intent classification |
| `AWAIT_FORM_CHOICE` | Waiting for form type button | FORM\|x → extraction; CANCEL → END |
| `AWAIT_APPROVAL` | Draft shown, waiting for decision | APPROVE → file; EDIT → edit flow; CANCEL → END |
| `AWAIT_EDIT_FIELD` | Asked which field to edit | FIELD\|x → ask for value; CANCEL → back to approval |
| `AWAIT_EDIT_VALUE` | Waiting for new field value | Any text → update draft → back to approval |
| `AWAIT_USERNAME` | Setup: waiting for username | Any text → ask password |
| `AWAIT_PASSWORD` | Setup: waiting for password | Any text → store → confirm |

**Rule:** Every path to `ConversationHandler.END` must call `context.user_data.clear()` first.

---

## 8. Data Flow

```mermaid
flowchart LR
    A[User input\ntext/voice/photo] --> B[bot.py\nconversation state machine]
    B --> C[extractor.py\nGemini API]
    C --> D[Draft data\nin context.user_data]
    D --> E[filer.py\nbrowser-use + Chromium]
    E --> F[(Kaizen ePortfolio\nDraft saved)]
    
    G[(SQLite DB\n~/.openclaw/data/portfolio-guru/)] --> B
    B --> G
    
    H[BWS Secrets\nTelegram + Google + Fernet] --> B
    H --> E
```

---

## Key Constraints (never violate)
- **NEVER submit** CBD to supervisor — draft save only
- **NEVER log** credentials in plaintext
- **NEVER open Kaizen** before user taps ✅ File this draft
- **NEVER select a KC** unless the trainee directly demonstrated it in this case
- Date format for Kaizen: `d/m/yyyy` (not ISO)
- KC over-selection is a bug — be conservative, not liberal

---

## Pending (v2.1)
- [ ] "Are we done?" button after successful filing
- [ ] Case history in SQLite — edit previously filed drafts
- [ ] Deterministic button structure at every state (AI populates text only)
- [ ] Portfolio type selection (Kaizen first, SOAR/LLP later)
- [ ] Usage limits + Stripe monetisation gate
- [ ] Settings menu (change portfolio, change credentials, view usage)
