> **DRAFT — NOT YET IN FORCE.** This is draft consent copy and a consent-record specification for review by the founder and a qualified solicitor / DPO. It is **not legal advice**. The wording below is designed to support **Art 9(2)(a) explicit consent** for special-category health data, but a lawyer must confirm it meets the UK GDPR standard (specific, informed, unambiguous, freely given, separately affirmed, and as easy to withdraw as to give). Resolve every `«REVIEW: ...»` marker before use.

# Consent Copy & Consent Record Spec — Portfolio Guru

## Part A — In-bot / in-app consent screen (shown before the user's first clinical case)

This screen must appear **before** the user can send their first case, must require a **deliberate, affirmative action** (no pre-ticked boxes, no "by continuing you agree"), and must be **separate** from accepting the Terms generally. Each bullet maps to a distinct, informed point of consent.

---

**Consent before your first case**

Case notes are health data. Please confirm before Portfolio Guru drafts from them:

- You will only send anonymised case details.
- When drafting, Portfolio Guru sends the anonymised case details you provide to Google Gemini via Vertex AI in the UK (London region).
- Your Kaizen login is stored encrypted and is not sent to the AI model.
- Portfolio Guru saves drafts only. It never submits to a supervisor.
- You can withdraw consent and erase Portfolio Guru's stored data any time with `/reset`.

By tapping **"I consent"**, you confirm you're a **GMC-registered doctor** using this for your own training record.

[ I consent ] [ Not now ]

_Full details: `/privacy`._

---

### Implementation notes for the consent screen

- Telegram has no native checkboxes; use **inline keyboard buttons** ("I consent" / "Not now"). The full text above must be shown before the buttons. «REVIEW: confirm this satisfies "explicit" and "unambiguous" for the solicitor — a single affirmative tap after reading distinct itemised points is the design intent.»
- Block sending any case content until consent for the **current version** is recorded.
- Provide a way to **view and withdraw** consent later (e.g. `/privacy`, `/forgetme`).
- If the consent **version changes materially**, re-prompt and re-record before further health-data processing.

---

## Part B — Versioned consent record specification

Store one record per user per accepted consent version. The canonical store is the encrypted SQLite DB; mirror to Supabase like other state. **Do not store the clinical content in the consent record** — only the fact and metadata of consent.

### Fields to store

| Field                                              | Type                     | Description                                                                                                                                       |
| -------------------------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                                               | uuid / autoincrement     | Primary key for the consent record.                                                                                                               |
| `telegram_user_id` (and future `whatsapp_user_id`) | int / string             | The platform identifier of the consenting user.                                                                                                   |
| `emgurus_user_id`                                  | string (nullable)        | Internal/web user id if linked, for cross-surface consistency.                                                                                    |
| `consent_version`                                  | string                   | The exact version string of the consent text accepted (e.g. `2026-06-25.v1`). Must match a stored, immutable copy of that version's wording.      |
| `consent_text_hash`                                | string                   | Hash of the exact wording shown, so you can prove what the user saw. «REVIEW: confirm you keep an immutable archive of each version's full text.» |
| `accepted_at`                                      | ISO 8601 timestamp (UTC) | When consent was given.                                                                                                                           |
| `channel`                                          | string                   | `telegram` / `whatsapp` / `web`.                                                                                                                  |
| `action`                                           | enum                     | `granted` / `withdrawn` / `re-granted`.                                                                                                           |
| `withdrawn_at`                                     | timestamp (nullable)     | When consent was withdrawn, if applicable.                                                                                                        |
| `lawful_basis`                                     | string                   | `art9_2a_explicit_consent` (for auditability).                                                                                                    |

### Behaviour

- **Append-only / immutable history.** A withdrawal creates a new `withdrawn` record (or sets `withdrawn_at`); never overwrite the original grant — you must be able to evidence the full consent history.
- **Gate on version.** Health-data processing proceeds only if the latest record for the user is `granted`/`re-granted` for the **current** `consent_version`.
- **Withdrawal effect.** On `withdrawn`, stop processing and trigger the deletion path (see Privacy Policy §8). Retain the consent record itself as evidence for «REVIEW: retention period».
- **Auditability.** It must be possible, for any past case, to state which consent version was in force when it was processed.

«REVIEW: confirm with the solicitor/DPO that storing `consent_version`, `consent_text_hash`, timestamp, channel, and user id is sufficient to evidence valid explicit consent, and agree the retention period for consent records (long enough to defend the lawful basis, no longer than necessary).»

---

_End of draft. Resolve all `«REVIEW»` markers before use._
