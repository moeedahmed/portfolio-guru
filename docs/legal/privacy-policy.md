> **DRAFT — NOT YET IN FORCE.** This is a working draft prepared for review by the founder and a qualified solicitor / Data Protection Officer (DPO). It is **not legal advice** and must not be published or relied upon until a legal professional has reviewed and approved it. Every `«REVIEW: ...»` marker flags a decision the founder must resolve with legal counsel before publication.

# Privacy Policy — Portfolio Guru

**Last updated:** «REVIEW: insert date the approved version goes live»
**Version:** 0.1 (draft)

This Privacy Policy explains how Portfolio Guru ("**we**", "**us**", "**the Service**") collects, uses, stores and shares your personal data when you use the Portfolio Guru bot (via Telegram, and in future WhatsApp) and the web front at `emgurus.com/portfolio`. It is written to meet the transparency requirements of the **UK GDPR** and the **Data Protection Act 2018 (DPA 2018)**.

Because Portfolio Guru processes information about your clinical work, some of the data we handle is **special-category data** (data concerning health) under Article 9 UK GDPR. We treat this data with the heightened care that classification requires. Please read the section on lawful basis carefully.

---

## 1. Who we are (Data Controller)

The data controller for your personal data is:

- **Trading name:** Portfolio Guru, an EM Gurus product.
- **Legal entity:** «REVIEW: confirm the exact legal entity that is the controller — e.g. "EM Gurus Ltd" (company number), a sole trader operating as "EM Gurus", or the founder personally. The controller named here must match Companies House / HMRC registration and the entity that holds the Stripe and Google Cloud accounts.»
- **Registered address:** «REVIEW: insert registered/correspondence address required for an ICO-compliant notice.»
- **Contact for privacy matters:** «REVIEW: insert a monitored privacy contact email, e.g. privacy@emgurus.com.»
- **Data Protection Officer:** «REVIEW: a DPO is not strictly mandatory for an organisation of this size under Art 37, but given large-scale processing of special-category health data a DPO (or a documented decision that one is not required, plus a named privacy lead) is strongly advisable. State the outcome here.»
- **ICO registration:** «REVIEW: the controller must pay the ICO data protection fee and register. Insert ICO registration number once obtained.»

We are **not affiliated with, endorsed by, or operated by the Royal College of Emergency Medicine (RCEM), the Kaizen ePortfolio platform, the General Medical Council (GMC), or any NHS body.**

---

## 2. What personal data we collect

| Category                                                   | Examples                                                                                                                                                                                                                                                     | How we get it                                |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------- |
| **Clinical case content (special-category — health data)** | The text, voice notes, audio, photographs and documents you send describing clinical cases and reflections, and the structured WPBA (workplace-based assessment) fields we extract from them. This may include patient clinical details if you include them. | You send it to the bot.                      |
| **Account & identity data**                                | Your Telegram user ID (and, in future, WhatsApp identifier), display name as exposed by the messaging platform, and any name/grade/training details you provide.                                                                                             | Messaging platform + your input.             |
| **Kaizen ePortfolio credentials**                          | Your RCEM Kaizen username and password, which you provide so the Service can log in and save drafts on your behalf.                                                                                                                                          | You provide them; stored encrypted (see §5). |
| **Billing data**                                           | Subscription tier, payment status, and billing identifiers. Card details are handled directly by Stripe — **we do not see or store full card numbers.**                                                                                                      | Stripe, on your purchase.                    |
| **Usage & technical data**                                 | Counts of cases filed (for free-tier limits), form types used, timestamps, error/diagnostic logs.                                                                                                                                                            | Generated as you use the Service.            |
| **Consent records**                                        | The version of the consent text you accepted, with a timestamp and your user ID (see our consent record).                                                                                                                                                    | Recorded when you consent.                   |

We ask you **not** to send identifiable patient data. You are responsible for anonymising patient information before sending it (see §3 and our Terms of Service). «REVIEW: confirm whether any automated redaction/anonymisation is implemented at launch; if not, the policy must not imply it exists. As built today, anonymisation is the user's responsibility and is not automated.»

---

## 3. The lawful basis for processing (and why)

Under UK GDPR we must have a lawful basis under **Article 6** for all personal data, and an **additional condition under Article 9** for special-category (health) data.

### 3.1 General personal data — Article 6

| Processing                                                                 | Art 6 lawful basis                                                                                                                                               |
| -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Operating your account, extracting WPBA data, filing drafts to Kaizen      | **Art 6(1)(b)** — performance of the contract you enter into when you use the Service.                                                                           |
| Taking payment and managing subscriptions                                  | **Art 6(1)(b)** — contract; and **Art 6(1)(c)** — legal obligation (e.g. tax/accounting records).                                                                |
| Security, fraud prevention, service diagnostics, and improving reliability | **Art 6(1)(f)** — our legitimate interests, balanced against your rights. «REVIEW: a Legitimate Interests Assessment (LIA) should be documented for this basis.» |

### 3.2 Special-category (health) data — Article 9 — the key decision

Your clinical case content is **data concerning health** under Art 9(1). Contract (Art 6(1)(b)) alone is **not** sufficient — we also need an Art 9 condition.

**Our proposed Art 9 condition is `Article 9(2)(a)` — your explicit consent.** We propose explicit consent (rather than, for example, Art 9(2)(h) "provision of health/social care", which is designed for clinicians and care providers acting in a care relationship, not for a portfolio-automation tool) because:

- Portfolio Guru is **not** providing health or social care to a patient; it is a productivity tool for a doctor's professional training record. The 9(2)(h) care-provision and 9(2)(i) public-health conditions do not naturally fit.
- Explicit consent gives you clear control, is transparent, and is the most defensible condition for a commercial SaaS tool sending health-related text to a third-party AI model.
- Explicit consent must be **specific, informed, unambiguous, freely given, and separately affirmed** — which is why we capture it on a dedicated consent screen before you can send your first case (see our consent copy), and record the version and timestamp.

> «REVIEW: This is the single most important legal decision in this document. Confirm with the solicitor/DPO that **Art 9(2)(a) explicit consent** is the correct condition, and that an **Appropriate Policy Document (APD)** under DPA 2018 Schedule 1 is or is not required for the chosen condition. If any reliance is placed on a Schedule 1 condition (e.g. for record-keeping), an APD is mandatory. Also confirm whether processing patient clinical details (even if the user is meant to anonymise) creates additional controller obligations or a need to treat the user as the relevant data subject only.»

You can withdraw consent at any time (see §8). Withdrawal stops future processing; it does not make past processing unlawful.

---

## 4. Who we share your data with (processors and sub-processors)

We use the third-party **processors** below. Each acts on our instructions under a data processing agreement (DPA). A full Record of Processing Activities and sub-processor table is maintained separately (see `processors-ropa.md`).

| Processor                                          | What they do for us                                                                                | Data they receive                                                          | Location / residency                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Google Cloud — Vertex AI (Gemini)**              | AI extraction of structured WPBA data from your case content                                       | Clinical case text, transcribed voice, image/document content              | **Intended: EU region with EU data residency.** «REVIEW: CRITICAL — the codebase currently calls Gemini via the Google AI / Developer API (`GOOGLE_API_KEY`), not Vertex AI with a pinned EU region. The "Vertex AI EU, EU data residency" claim is an _intent_, not yet verified in code. Before publishing this notice, either (a) migrate extraction to Vertex AI with an EU `location` and confirm Google's EU data-residency/ML-processing commitments, or (b) describe the actual processor and region truthfully. Do not state EU residency as fact until verified.» |
| **Supabase**                                       | Cloud mirror of account, profile, usage and (encrypted) credential data so the web app can read it | Account/profile/usage data; encrypted Kaizen credentials                   | **Intended: EU region.** «REVIEW: confirm the Supabase project region is EU (e.g. eu-west / eu-central) and that no data is stored in a US region.»                                                                                                                                                                                                                                                                                                                                                                                                                         |
| **Telegram** (and, in future, **Meta / WhatsApp**) | Messaging transport you use to reach the bot                                                       | All message content in transit, your platform user ID                      | «REVIEW: Telegram's servers and data locations are outside your control and may be outside the UK/EEA. WhatsApp/Meta involves transfers to the US. Both must be addressed in the international-transfers section and in your transparency information. Confirm whether you can rely on the platforms' own terms or need additional safeguards.»                                                                                                                                                                                                                             |
| **Stripe**                                         | Payment processing and subscription billing                                                        | Billing identifiers, payment status (card data handled by Stripe directly) | «REVIEW: Stripe processes data including in the US; confirm Stripe's UK/EU entity and transfer mechanism (Stripe relies on SCCs / UK Addendum).»                                                                                                                                                                                                                                                                                                                                                                                                                            |

We do **not** sell your personal data. We do **not** use your clinical content to train our own models, and we instruct our AI processor not to use it to train theirs. «REVIEW: confirm contractually that the chosen Google API tier does not use your prompts/content for model training — Vertex AI and the paid Gemini API generally exclude training use, but the free AI Studio tier historically does not. This claim must be verified against the exact API and billing tier in use.»

---

## 5. How we store and protect your data

- **Canonical store:** an encrypted **SQLite** database on a controlled machine. Your Kaizen credentials are encrypted at rest using **Fernet (AES-128-CBC + HMAC)** and are only decrypted in memory at the moment they are needed to log in to Kaizen on your behalf.
- **Credentials are never sent to any AI model or included in any AI prompt.**
- **Cloud mirror:** account, profile and usage data (and the _encrypted_ credential blob) are mirrored to Supabase (intended EU region) so the web app can function.
- **Access control, logging and key management:** «REVIEW: document who can access the machine and the Supabase project, how the Fernet key is stored and rotated, and confirm that decrypted values and credentials are never written to logs.»
- We do not log decrypted credentials, tokens, or secrets.

No system is perfectly secure, but we take reasonable technical and organisational measures appropriate to the sensitivity of the data.

---

## 6. International transfers

Our intent is to keep your data within the **UK/EEA** by using EU-region services for AI processing (Vertex AI EU) and storage (Supabase EU).

Some processors may involve transfers outside the UK/EEA:

- **Telegram / WhatsApp (Meta):** messaging transport may route through, or be stored on, servers outside the UK/EEA (Meta/WhatsApp in particular involves US transfers).
- **Stripe:** may process some billing data in the US.

Where data is transferred outside the UK, we rely on an appropriate transfer mechanism — the **UK International Data Transfer Agreement (IDTA)** or the **EU Standard Contractual Clauses with the UK Addendum**, and/or **UK adequacy regulations** where they apply. «REVIEW: for each processor confirm (a) whether a transfer outside the UK/EEA actually occurs, and (b) the specific lawful transfer mechanism relied on, and run a Transfer Risk Assessment (TRA) where required. The US sub-processor exposure (Stripe, Meta/WhatsApp, and Google if not pinned to EU) is the main item here.»

---

## 7. How long we keep your data (retention)

| Data                                      | Proposed retention                                                                                                                                                                                                                                                                                    |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Clinical case content sent for extraction | **Implemented (2026-07-02):** encrypted case text and extracted fields in the cloud mirror are deleted **180 days** after filing by a daily automated job (`backend/retention.py`, window configurable via `PG_CLINICAL_RETENTION_DAYS`). The non-clinical record (form type, status, date) is kept for the user's own history and ARCP progress features. Attachment and voice files are deleted immediately after processing; in-flight drafts are cleared when the draft is saved or the session is reset. «REVIEW: solicitor to confirm 180 days is appropriate.» |
| Account, profile, usage data              | For the life of your account plus a short wind-down period. «REVIEW: set a number, e.g. 30 days after account closure.»                                                                                                                                                                               |
| Kaizen credentials (encrypted)            | Until you remove them or close your account. Deleted on account closure. «REVIEW: confirm the credential-deletion path runs on account closure — the codebase has account-scoped state clearing; verify it covers all stores including the Supabase mirror.»                                          |
| Billing records                           | As required by UK tax/accounting law — typically **6 years**. «REVIEW: confirm with accountant.»                                                                                                                                                                                                      |
| Consent records                           | For as long as needed to evidence the lawful basis, plus a reasonable period afterwards. «REVIEW: set a number.»                                                                                                                                                                                      |
| Diagnostic logs                           | «REVIEW: set a short retention, e.g. 30–90 days, and confirm logs contain no clinical content or secrets.»                                                                                                                                                                                            |

---

## 8. Your rights

Under UK GDPR you have the right to:

- **Be informed** (this notice).
- **Access** a copy of your personal data (subject access request).
- **Rectification** of inaccurate data.
- **Erasure** ("right to be forgotten") — including deletion of your clinical content, credentials, and account. Because our primary Art 9 basis is consent, withdrawing consent will also trigger deletion of the consent-based data.
- **Restriction** of processing.
- **Data portability** for data you provided, where processing is based on consent or contract and is automated.
- **Object** to processing based on legitimate interests.
- **Withdraw consent** at any time, as easily as you gave it.
- Rights related to **automated decision-making**. Note: Portfolio Guru **only saves drafts**; it never auto-submits to your supervisor, and a human (you) reviews every entry before it counts. We do not carry out solely-automated decision-making producing legal or similarly significant effects on you. «REVIEW: confirm this characterisation holds and that the form-type recommendation is advisory only.»

### How to exercise your rights

Contact us at «REVIEW: privacy contact email». In-bot, you can use «REVIEW: confirm/define commands, e.g. /delete or /forgetme, and /export». We will respond within **one month** as required by UK GDPR. We may ask you to verify your identity.

---

## 9. Complaints

If you are unhappy with how we handle your data, please contact us first. You also have the right to complain to the UK supervisory authority:

**Information Commissioner's Office (ICO)** — https://ico.org.uk/make-a-complaint/ — Helpline 0303 123 1113 — Wycliffe House, Water Lane, Wilmslow, Cheshire SK9 5AF.

---

## 10. Changes to this policy

We may update this policy. Material changes affecting how we process your health data may require us to ask for your consent again. We will notify you of significant changes via the bot or web app. «REVIEW: define notification mechanism.»

---

_End of draft. Resolve all `«REVIEW»` markers with the solicitor/DPO before publication._
