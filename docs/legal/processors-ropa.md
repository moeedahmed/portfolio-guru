> **DRAFT — NOT YET IN FORCE.** This Record of Processing Activities (ROPA) and sub-processor list is a working draft for review by the founder and a qualified solicitor / DPO. It is **not legal advice**. Maintaining a ROPA is a legal obligation under Art 30 UK GDPR. Resolve every `«REVIEW: ...»` marker and keep this document current as processors change.

# Record of Processing Activities & Sub-processor List — Portfolio Guru

**Controller:** «REVIEW: legal entity — must match Privacy Policy.»
**ROPA owner:** «REVIEW: name.» **Last updated:** «REVIEW.»

---

## Part 1 — Record of Processing Activities (Art 30)

### Activity 1 — AI extraction of WPBA data from clinical cases

| Field           | Detail                                                                                         |
| --------------- | ---------------------------------------------------------------------------------------------- |
| Purpose         | Extract structured portfolio fields and recommend a form type from user-supplied case content. |
| Data subjects   | Using clinician (and any patient details the user includes).                                   |
| Data categories | **Special category (health)**: clinical case text, transcribed voice, image/document content.  |
| Lawful basis    | Art 6(1)(b) contract; **Art 9(2)(a) explicit consent**.                                        |
| Recipients      | Google (Vertex AI / Gemini).                                                                   |
| Transfers       | Intended EU; «REVIEW: verify EU residency — see sub-processor table.»                          |
| Retention       | «REVIEW: define; minimise raw content.»                                                        |
| Security        | TLS in transit; no credentials in prompts; «REVIEW: confirm no-training commitment.»           |

### Activity 2 — Saving drafts to Kaizen ePortfolio

| Field           | Detail                                                                               |
| --------------- | ------------------------------------------------------------------------------------ |
| Purpose         | Log in with the user's stored credentials and save an approved draft.                |
| Data subjects   | Using clinician.                                                                     |
| Data categories | Kaizen credentials; the approved draft content (health-related).                     |
| Lawful basis    | Art 6(1)(b) contract; Art 9(2)(a) explicit consent.                                  |
| Recipients      | RCEM Kaizen platform (the user's own portfolio).                                     |
| Transfers       | «REVIEW: Kaizen hosting location.»                                                   |
| Retention       | Draft lives in the user's Kaizen account; credentials per credential-retention rule. |
| Security        | **Fernet-encrypted** credentials at rest; decrypted only in memory at login.         |

### Activity 3 — Account, profile & usage management

| Field           | Detail                                                                                 |
| --------------- | -------------------------------------------------------------------------------------- |
| Purpose         | Operate accounts, enforce free-tier limits, sync to web app.                           |
| Data subjects   | Using clinician.                                                                       |
| Data categories | Messaging ID, name/grade, usage counts, tier.                                          |
| Lawful basis    | Art 6(1)(b) contract; Art 6(1)(f) legitimate interests (reliability/abuse prevention). |
| Recipients      | Supabase (cloud mirror).                                                               |
| Transfers       | Intended EU; «REVIEW: confirm Supabase region.»                                        |
| Retention       | Life of account + «REVIEW».                                                            |
| Security        | Encrypted store; access controls «REVIEW».                                             |

### Activity 4 — Payments & subscriptions

| Field           | Detail                                                               |
| --------------- | -------------------------------------------------------------------- |
| Purpose         | Take payment for the £9.99/mo tier; manage subscriptions.            |
| Data subjects   | Paying clinician.                                                    |
| Data categories | Billing identifiers, payment status (no full card data held by us).  |
| Lawful basis    | Art 6(1)(b) contract; Art 6(1)(c) legal obligation (tax/accounting). |
| Recipients      | Stripe.                                                              |
| Transfers       | «REVIEW: Stripe US transfer mechanism (SCCs/UK Addendum).»           |
| Retention       | Billing records typically 6 years «REVIEW».                          |
| Security        | PCI handled by Stripe.                                               |

### Activity 5 — Messaging transport

| Field           | Detail                                                          |
| --------------- | --------------------------------------------------------------- |
| Purpose         | Deliver the conversational interface.                           |
| Data subjects   | Using clinician.                                                |
| Data categories | All message content in transit; platform user ID.               |
| Lawful basis    | Art 6(1)(b) contract; Art 9(2)(a) for health content carried.   |
| Recipients      | Telegram (now); Meta/WhatsApp (future).                         |
| Transfers       | «REVIEW: likely outside UK/EEA; Meta = US. Document mechanism.» |
| Retention       | Governed by platform; we do not control.                        |
| Security        | Platform transport security.                                    |

### Activity 6 — Consent records

| Field           | Detail                                                                   |
| --------------- | ------------------------------------------------------------------------ |
| Purpose         | Evidence valid explicit consent.                                         |
| Data subjects   | Using clinician.                                                         |
| Data categories | User id, consent version, timestamp, channel (see `consent-copy.md`).    |
| Lawful basis    | Art 6(1)(c)/legal obligation to demonstrate compliance (accountability). |
| Recipients      | Internal; Supabase mirror.                                               |
| Retention       | «REVIEW: long enough to defend the basis.»                               |
| Security        | Encrypted store.                                                         |

---

## Part 2 — Sub-processor list

| Sub-processor                                                 | Purpose                                                           | Data categories                                                       | Location / residency                                                                                                                                          | DPA status / link                                                                                                                                                   |
| ------------------------------------------------------------- | ----------------------------------------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Google Cloud — Vertex AI (Gemini)**                         | AI extraction of WPBA data                                        | Health/clinical content (text, transcribed voice, images, documents)  | Intended **EU** «REVIEW: VERIFY — code currently uses Google AI Developer API (`GOOGLE_API_KEY`), not region-pinned Vertex AI. Do not assert EU until fixed.» | Google Cloud Data Processing Addendum / CDPA. «REVIEW: confirm which Google terms apply to the exact API/tier and that no-training applies; link the accepted DPA.» |
| **Supabase**                                                  | Cloud mirror of account/profile/usage + encrypted credential blob | Account, profile, usage, tier, consent records; encrypted credentials | Intended **EU** «REVIEW: confirm project region.»                                                                                                             | Supabase DPA. «REVIEW: confirm executed; link.»                                                                                                                     |
| **Stripe**                                                    | Payment processing & subscriptions                                | Billing identifiers, payment status                                   | EU/US «REVIEW: confirm Stripe contracting entity + transfer mechanism.»                                                                                       | Stripe DPA (incorporated in Stripe Services Agreement; SCCs/UK Addendum). «REVIEW: confirm.»                                                                        |
| **Telegram**                                                  | Messaging transport                                               | All message content in transit, platform user ID                      | «REVIEW: outside our control; likely outside UK/EEA.»                                                                                                         | «REVIEW: assess reliance on Telegram's terms; no standard B2B DPA — document risk.»                                                                                 |
| **Meta / WhatsApp** (future)                                  | Messaging transport                                               | All message content in transit, WhatsApp ID                           | **US** transfers «REVIEW».                                                                                                                                    | WhatsApp Business / Meta DPA + SCCs/UK Addendum. «REVIEW: not yet engaged — complete before WhatsApp launch.»                                                       |
| **Hosting machine / infrastructure** (canonical SQLite store) | Primary encrypted data store & bot runtime                        | All categories (encrypted)                                            | «REVIEW: state where the controlled machine is located and who has physical/admin access; if a self-hosted Mac, document safeguards.»                         | Internal — not a third-party processor, but document controls.                                                                                                      |

---

## Maintenance

- Update this ROPA whenever a processor is added, removed, or changes region; before the WhatsApp launch; and at each DPIA review.
- Keep an evidence folder of each signed/accepted DPA and transfer mechanism. «REVIEW: create and link.»

---

_End of draft. Resolve all `«REVIEW»` markers with the solicitor/DPO and attach DPA evidence before relying on this record._
