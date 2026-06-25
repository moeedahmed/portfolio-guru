> **DRAFT — NOT YET IN FORCE.** This Data Protection Impact Assessment (DPIA) is a working draft for review by the founder and a qualified solicitor / DPO. It is **not legal advice**. A DPIA is a living document: it must be completed, signed off, and kept under review. Resolve every `«REVIEW: ...»` marker before relying on this assessment.

# Data Protection Impact Assessment (DPIA) — Portfolio Guru

**Structure follows the ICO's sample DPIA template.**

- **Project:** Portfolio Guru — AI-assisted RCEM Kaizen ePortfolio drafting for UK EM trainees.
- **DPIA owner:** «REVIEW: name the accountable person (likely the founder / privacy lead).»
- **Date started:** «REVIEW» **Last reviewed:** «REVIEW» **Next review due:** «REVIEW (set a cadence, e.g. annually or on material change).»
- **Why a DPIA is required:** the Service involves **large-scale processing of special-category (health) data**, **innovative use of AI**, and processing that is likely to be high-risk under Art 35 UK GDPR / the ICO's "likely to result in high risk" criteria. A DPIA is therefore mandatory.

---

## Step 1 — Identify the need for a DPIA

Portfolio Guru ingests clinicians' free-text/voice/image clinical case descriptions, sends them to a third-party AI model for extraction, stores the results and the clinician's portfolio credentials, mirrors data to the cloud, and writes drafts into a third-party ePortfolio. The combination of health data + AI + credential storage + cross-border processors triggers several ICO high-risk indicators (sensitive data, new technologies, matching/combining, and a relationship-of-trust with regulated professionals). A DPIA screens and mitigates these risks before launch.

---

## Step 2 — Describe the processing

### 2.1 Nature of the processing

- **Collection:** user sends case content via Telegram (future: WhatsApp).
- **AI extraction:** content sent to **Google Vertex AI (Gemini)** — _intended_ EU region — to extract structured WPBA fields and recommend a form type. «REVIEW: codebase currently uses the Google AI Developer API (`GOOGLE_API_KEY`), not a region-pinned Vertex AI endpoint. Confirm and correct before sign-off — this affects residency and training-use claims.»
- **Storage:** canonical encrypted **SQLite** store on a controlled machine; **Fernet**-encrypted Kaizen credentials; **Supabase** (intended EU) cloud mirror for the web app.
- **Use:** generate a draft, show it to the user for review, and on approval log in to **Kaizen** and save a **draft** (never auto-submit).
- **Deletion:** account-scoped deletion on account closure / credential removal «REVIEW: confirm coverage across all stores including Supabase mirror and any logs».
- **Payments:** **Stripe** for the £9.99/mo tier; free tier limited to 5 cases/month.

### 2.2 Scope of the processing

- **Data subjects:** primarily the using clinician; secondarily, any patient whose details the clinician includes (the user is instructed to anonymise).
- **Data categories:** health/clinical content (special category), identity (messaging ID, name/grade), Kaizen credentials, billing, usage/diagnostics, consent records.
- **Volume/scale:** «REVIEW: estimate expected user numbers and cases/month; "large scale" classification drives obligations.»
- **Geography:** users in the UK; processors potentially in EU/US (see transfers).
- **Duration/retention:** «REVIEW: insert retention periods agreed in the Privacy Policy.»

### 2.3 Context of the processing

- Relationship of professional trust; users are GMC-registered doctors; data concerns their training and patients.
- Innovative AI technology; third-party platforms (RCEM/Kaizen, Telegram/WhatsApp) outside our control.
- No prior public expectation that an automation tool drafts ePortfolio entries; transparency is critical.
- «REVIEW: confirm whether RCEM/Kaizen terms permit automated third-party access — a contractual/relationship risk distinct from data protection.»

### 2.4 Purposes of the processing

- For the user: faster, lower-friction WPBA record-keeping.
- For us: provide a paid SaaS product; ensure reliability and prevent abuse.

---

## Step 3 — Consultation process

- **Data subjects:** «REVIEW: describe how user views are sought (e.g. beta feedback) and reflected.»
- **Processors:** rely on Google, Supabase, Stripe, Telegram/Meta DPAs and documentation. «REVIEW: obtain and file each DPA.»
- **DPO/legal:** this DPIA itself is the consultation artefact for solicitor/DPO sign-off.
- **ICO prior consultation:** required only if high residual risk cannot be mitigated. «REVIEW: assess after mitigations — aim to avoid by reducing residual risk to acceptable.»

---

## Step 4 — Assess necessity and proportionality

- **Lawful basis:** Art 6(1)(b) contract for core processing; Art 6(1)(f) for security/diagnostics (with LIA); **Art 9(2)(a) explicit consent** for health data. «REVIEW: confirm Art 9 condition and any Schedule 1 / Appropriate Policy Document need.»
- **Necessity:** sending case content to an AI model is necessary to deliver the extraction feature the user requests; credential storage is necessary to save drafts on the user's behalf.
- **Proportionality / data minimisation:**
  - Credentials are never sent to the AI model and are encrypted at rest.
  - The user is asked not to send patient-identifiable data.
  - «REVIEW: consider minimising retention of raw case content (delete after draft saved) and whether image/voice can be processed without long-term storage.»
- **Data quality / accuracy:** AI output is non-deterministic; mitigated by mandatory human review (draft-only) — see automation risk below.
- **Processor compliance:** Art 28 DPAs in place with each processor. «REVIEW: confirm.»
- **Transfers:** see risk register.

---

## Step 5 — Identify and assess risks

| #   | Risk to individuals                                                                                                 | Likelihood | Severity    | Overall                  |
| --- | ------------------------------------------------------------------------------------------------------------------- | ---------- | ----------- | ------------------------ |
| R1  | **Special-category health data exposed to a third-party AI/LLM**, or used for model training                        | «REVIEW»   | High        | **High** until mitigated |
| R2  | **Kaizen credential compromise** (theft of stored credentials → unauthorised portfolio access)                      | «REVIEW»   | High        | **High**                 |
| R3  | **Unlawful / unsafe international transfer** (US sub-processors: Stripe, Meta/WhatsApp; Google if not EU-pinned)    | «REVIEW»   | Medium–High | **High**                 |
| R4  | **Re-identification of patients** from clinical content the user failed to anonymise                                | «REVIEW»   | High        | **High**                 |
| R5  | **Inaccuracy / automation harm** — wrong extraction or form-type recommendation leading to a flawed portfolio entry | Medium     | Medium      | **Medium**               |
| R6  | **Excessive retention** of raw clinical content                                                                     | «REVIEW»   | Medium      | **Medium**               |
| R7  | **Account takeover via messaging platform** (Telegram/WhatsApp account compromise)                                  | «REVIEW»   | Medium      | **Medium**               |
| R8  | **Invalid/insufficient consent** for Art 9 health-data processing                                                   | «REVIEW»   | High        | **High**                 |
| R9  | **Logging of secrets or clinical content** in diagnostics                                                           | Low        | High        | **Medium**               |

---

## Step 6 — Identify measures to reduce risk

| #   | Mitigations                                                                                                                                                                                                                                                                                                                                                                    | Residual risk                 | Owner               |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------- | ------------------- |
| R1  | Use **Vertex AI EU region** with EU data residency; contractually exclude content from model training; send the **minimum** content needed; never include credentials in prompts. «REVIEW: verify Vertex AI EU migration + no-training commitment for the exact API/billing tier; until verified, residual risk stays High.»                                                   | «REVIEW»                      | Founder             |
| R2  | **Fernet (AES-128-CBC + HMAC) encryption at rest**, decryption only in memory at login time, credentials never logged or sent to AI; document **Fernet key management and rotation**; restrict machine and Supabase access; encrypt the credential blob in the Supabase mirror too. «REVIEW: confirm key storage/rotation and that the mirror stores only the encrypted blob.» | Low–Medium                    | Founder             |
| R3  | Keep processing in **EU/UK**; for unavoidable US transfers (Stripe, Meta/WhatsApp) rely on **IDTA / SCCs + UK Addendum** and run **Transfer Risk Assessments**. «REVIEW: document mechanism per processor.»                                                                                                                                                                    | Medium                        | Founder + solicitor |
| R4  | **Explicit user instruction and consent** that they must anonymise; in-bot reminder; «REVIEW: consider building automated PII/PHI redaction before AI processing — would materially lower this risk.»                                                                                                                                                                          | Medium (High if no redaction) | Founder             |
| R5  | **Draft-only architecture** — never auto-submit; mandatory human review of every field; form-type recommendation is advisory; clear UI framing; test extraction across multiple runs (non-determinism noted in project docs).                                                                                                                                                  | Low–Medium                    | Founder             |
| R6  | **Minimise retention** of raw case content (e.g. delete after draft saved); define and enforce retention periods; provide user deletion/export. «REVIEW: implement and state actual behaviour.»                                                                                                                                                                                | Low–Medium                    | Founder             |
| R7  | Encourage platform 2FA; design so a single compromised message cannot alter access settings; «REVIEW: consider a per-user confirmation step before saving drafts.»                                                                                                                                                                                                             | Medium                        | Founder             |
| R8  | **Dedicated, versioned explicit-consent screen** before first case (see `consent-copy.md`); record version + timestamp + user id; easy withdrawal.                                                                                                                                                                                                                             | Low                           | Founder + DPO       |
| R9  | Code rule: **never log decrypted values, credentials, or tokens**; scrub clinical content from logs; short log retention.                                                                                                                                                                                                                                                      | Low                           | Founder             |

---

## Step 7 — Sign off and record outcomes

| Item                           | Detail                                                                                        |
| ------------------------------ | --------------------------------------------------------------------------------------------- |
| Measures approved by           | «REVIEW: name + date»                                                                         |
| Residual risk accepted by      | «REVIEW: name + date»                                                                         |
| DPO advice given / acted on    | «REVIEW»                                                                                      |
| ICO prior consultation needed? | «REVIEW: only if high residual risk remains after mitigation — target NO.»                    |
| This DPIA to be reviewed       | «REVIEW: set trigger — material change, new processor, new platform (WhatsApp), or annually.» |

**Key open items the founder must close before launch:** (1) confirm/verify Vertex AI EU residency + no-training in code; (2) document credential key management; (3) finalise Art 9 explicit-consent flow and Schedule 1/APD position; (4) document international-transfer mechanisms; (5) decide on automated patient-data redaction.

---

_End of draft. This DPIA is incomplete until all `«REVIEW»` items are resolved and it is signed off._
