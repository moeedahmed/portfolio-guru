> **DRAFT — NOT IN FORCE.** Prepared for manager and UK legal review. This draft is not legal advice, is not a claim of compliance, and must not be published or presented as operative.

# Portfolio Guru privacy notice

**Evidence date:** 20 August 2026
**Proposed version:** 1.0 draft

## 1. Proposed controller and contact details

Portfolio Guru is a Telegram-first service for UK doctors. The proposed controller is **Solvoro Labs LLC**, a Texas limited liability company formed on 23 November 2025. Its operating agreement is manager-managed; Moeed cannot bind the company.

[BLOCKER — Manager: approve Solvoro Labs LLC as controller and contracting entity, and authorise adoption of this notice after UK legal review.]

[BLOCKER — Manager + legal counsel: approve a public business/service address and monitored privacy contact. Do not publish this notice without both.]

Solvoro Labs LLC is outside the UK and intentionally offers the Service to people in the UK. UK GDPR territorial scope is therefore expected to apply to the relevant processing. No UK representative has been appointed and no exemption is asserted.

[BLOCKER — Manager + UK legal counsel: complete the Article 27 assessment and, if required, appoint a UK representative in writing and add its details here.]

Portfolio Guru is independent. It is not affiliated with, endorsed by or operated by RCEM, Kaizen, the GMC or any NHS body. It is not an NHS system, patient-care service or medical device.

## 2. Data and channel limitations

| Data | Source and important limitation |
| --- | --- |
| Telegram account data | Telegram user ID, profile/display details exposed to the bot, and messages used to operate the account. |
| Case and portfolio content | Text, voice, audio, photographs, documents, extracted fields, form choices, drafts and user corrections. This may reveal health information if a user breaks the patient-data rule. |
| Kaizen access data | Kaizen username/password and automation state used to save a user-approved draft. Sensitive credentials are application-encrypted. |
| Service and security data | Consent/version records, subscription tier, usage counts, timestamps, errors, security and fraud signals. |
| Billing data | Stripe customer/subscription identifiers and payment status. Stripe receives card and payment details directly; Portfolio Guru does not hold full card numbers. |
| Rights and support data | Requests, correspondence, identity checks and the record of the response. |

**Do not submit identifiable patient data.** Remove names, dates of birth, NHS numbers, addresses, images or documents that identify a patient, and unusual combinations of facts that could identify one. Redaction is a safety net, not permission to send patient data.

Telegram receives and stores original cloud-chat text and media before Portfolio Guru can inspect or redact it. Text and document text are locally inspected and redacted before AI extraction. Scanned images and scanned PDFs may reach the AI service before local redaction. The running bot was inspected on 20 August 2026 and was using Vertex AI in London (`PG_USE_VERTEX=1`, `europe-west2`); while that flag is effective, text extraction excludes the configured DeepSeek route. A user must still redact every format before sending it.

The code also contains inactive or conditional routes for the Google developer API, DeepSeek and OpenAI. The Vertex factory currently falls back to the Google developer API if its flag or project configuration is absent, and image/audio utilities contain hosted fallbacks. Those routes are not approved merely because they are inactive in the inspected process.

[BLOCKER — Moeed + privacy lead: make unapproved provider fallback fail closed, or evidence an approved route and monitoring for every model path; align the live consent wording and processor notice with the effective route before any launch expansion.]

## 3. Purposes and proposed lawful bases

| Purpose | Data | Proposed UK GDPR basis |
| --- | --- | --- |
| Create an account; extract portfolio fields; recommend a form; show and save a user-approved Kaizen draft | Account, case, portfolio and Kaizen access data | Article 6(1)(b), necessary to perform the service contract or take requested pre-contract steps. |
| Manage subscriptions and service entitlements | Account, usage and limited billing data | Article 6(1)(b). |
| Keep financial records required by law | Limited transaction and billing records | Article 6(1)(c), legal obligation, once the applicable obligations and schedule are documented. |
| Protect accounts, investigate abuse/fraud and maintain reliable operations | Account, usage, diagnostic and security data | Article 6(1)(f), the documented interests in security, fraud prevention and proportionate service operation, subject to an approved LIA. |
| Send optional marketing | Contact and preference data | Article 6(1)(a), consent, with PECR assessment and an easy withdrawal route. No optional marketing is assumed in this draft. |
| Respond to rights, complaints and legal claims | Account, request and correspondence data | The basis appropriate to the request: legal obligation and, where necessary, legitimate interests in establishing, exercising or defending legal claims. |

[BLOCKER — Manager-appointed privacy lead + legal counsel: approve the lawful-basis record and complete the legitimate interests assessment before relying on Article 6(1)(f).]

The contract basis above applies to the doctor's intended account and service data. It does not supply a basis for accidental third-party patient data.

Users are prohibited from sending identifiable patient data. A doctor's agreement or consent cannot supply an Article 9 condition for a third-party patient's health data. If identifiable or potentially identifiable patient data is detected, Portfolio Guru's intended response is to stop ordinary processing and minimise, quarantine or delete it under an approved incident procedure.

[BLOCKER — Manager + legal counsel: approve the special-category-data position and incident procedure, including any Article 6 basis, Article 9 condition, DPA 2018 condition, notices, evidence preservation and deletion steps that may be required for accidental receipt. Until then, wider launch remains blocked.]

## 4. Recipients and roles

| Recipient | Role and data shared |
| --- | --- |
| Google Cloud | Proposed processor for Vertex AI extraction and encrypted off-device Google Cloud Storage backups. The inspected Vertex runtime was London (`europe-west2`). Backup configuration targets a GCS bucket and encrypts the archive before upload; bucket region, lifecycle and account-level proof are not filed in this pack. Account-level CDPA, exact service scope, data-location, subprocessor, no-training/retention and transfer evidence remain blocked. |
| Supabase | A dedicated London (`eu-west-2`) project and mirror code exist, but the inspected bot process had neither Supabase runtime variable. An active live mirror was therefore **not established** at the evidence date. Before activation, file the account DPA, project region, subprocessor, security and transfer evidence. |
| Telegram | Upstream independent platform/controller for the user's Telegram account and cloud chats, not a processor acting only on Portfolio Guru's instructions. It receives the original messages and media and controls its own copies and retention. |
| Kaizen / RCEM ePortfolio | Independent destination platform/controller for drafts saved to the user's account. Kaizen is authoritative for data saved there. Its automation permission and hosting/transfer position remain unresolved. |
| Stripe | Payment provider and authoritative billing platform. Stripe acts in the roles described by its own terms and DPA, including controller activities; the account-specific contracting entity and transfer mechanism remain to be evidenced. |
| Bitwarden Secrets Manager and Healthchecks.io | Operational suppliers. Bitwarden supplies runtime secrets; Healthchecks receives intended liveness/job metadata. No case content is intended for either. Account terms, access, minimisation and transfer evidence are not filed. |
| Professional advisers, regulators or courts | Data only where reasonably necessary for advice, a legal claim or a binding legal requirement. |

Portfolio Guru does not sell personal data. No claim is made here that every processor contract or transfer safeguard has been executed.

## 5. Storage and security

The live canonical service data remains in local SQLite and files on a Mac Mini. Supabase mirror code and a dedicated project exist, but the inspected bot process did not establish an active mirror. Encrypted off-device backup archives are configured to be copied to Google Cloud Storage. Sensitive credentials and case fields are application-encrypted; encryption keys and provider secrets are kept outside the database. Credentials are not intended to be sent to an AI model.

Known weaknesses at the evidence date are that some local drafts/backups have weak file permissions and FileVault is off. Encryption at field level does not remove the need for full-device encryption, restrictive file permissions, access review, backup control and tested recovery/deletion procedures.

[BLOCKER — Moeed: remediate and evidence Mac Mini disk encryption, local file/backup permissions, least-privilege access and key-management controls; manager-appointed privacy lead to accept any residual risk.]

## 6. International processing

The proposed controller is in the United States. The inspected Vertex runtime was London, Google Cloud Storage holds configured encrypted backups, and the Supabase mirror was not established as active. Supplier support, subprocessors, Stripe, Telegram and operational suppliers may involve access or processing outside the UK. A service region alone does not prove that all processing stays in that region.

[BLOCKER — Manager-appointed privacy lead + legal counsel: map each restricted transfer and file the applicable adequacy, UK IDTA/Addendum or other safeguard, plus a transfer risk assessment/data protection test where required.]

## 7. Retention and deletion

| Data | Current or proposed position |
| --- | --- |
| Clinical content and extracted fields | Intended maximum retention is 180 days. End-to-end enforcement across SQLite, local files, temporary processing and any activated mirror has not been proven. Local backup retention is configured for 30 days and the GCS lifecycle is described in code as 90 days, but account-level lifecycle evidence and deletion proof are not filed. |
| Kaizen credentials | Intended to be kept only while the account needs draft-saving access. The final deletion period and proof are blocked. |
| Account, consent, usage, security and support records | Schedule not yet approved. Records must be minimised and assigned purpose-based periods. |
| Financial records | Kept for the period required by applicable accounting/tax law; the period is not fixed in this draft. Stripe controls its own billing records. |
| Telegram messages | Controlled by Telegram and the user's Telegram settings; Portfolio Guru deletion cannot erase Telegram's copies. |
| Kaizen drafts | Controlled by Kaizen and the user; Portfolio Guru deletion cannot erase a draft already saved there. |

[BLOCKER — Moeed: produce a cross-store retention inventory and automated/manual deletion evidence; manager + legal/accounting advisers to approve the schedule.]

The in-service `/reset` route deletes identified local records and attempts mirror erasure, but complete deletion across every store, file, backup and third-party copy has not yet been proven. Consent history and the Stripe billing link are intentionally retained by code; Telegram and Kaizen copies must be managed separately under those platforms' controls. The current in-product consent copy says the user can “erase Portfolio Guru's stored data any time with /reset”, which is materially broader than this observed behaviour.

[BLOCKER — Manager + privacy/legal counsel: decide the lawful and proportionate evidence-retention scope; Moeed: correct and version the consent/reset copy and prove the resulting deletion path before wider use.]

## 8. Rights and complaints

Depending on the circumstances, UK data-protection rights may include information, access, rectification, erasure, restriction, portability, objection, withdrawal of consent and safeguards relating to automated decisions. Portfolio Guru recommends a form and prepares drafts, but the doctor reviews the draft and decides whether to save and later submit it. It is not intended to make solely automated decisions with legal or similarly significant effects.

[BLOCKER — Manager: approve and publish a monitored privacy contact and identity-verification process. Moeed: test access, correction, export, restriction, objection and deletion workflows across all stores.]

A user may also complain to the Information Commissioner's Office: https://ico.org.uk/make-a-complaint/

## 9. Other platforms and changes

Telegram's and Kaizen's own terms and privacy notices apply to their services. Portfolio Guru cannot alter or delete the copies they control. Material changes to this notice or the processing must be assessed, notified clearly and, where required, accepted or consented to before the changed processing begins.

## Official references

- UK GDPR Article 27 text: https://www.legislation.gov.uk/eur/2016/679/article/27
- ICO legitimate interests guidance: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/a-guide-to-lawful-basis/legitimate-interests/
- ICO international transfers guidance: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/international-transfers/
- Telegram privacy policy: https://telegram.org/privacy/gb
- Google Cloud CDPA: https://cloud.google.com/terms/data-processing-addendum
- Vertex AI data governance: https://cloud.google.com/vertex-ai/generative-ai/docs/data-governance
- Vertex AI locations: https://cloud.google.com/vertex-ai/generative-ai/docs/learn/locations
- Supabase DPA: https://supabase.com/legal/customer-resources/data-processing-addendum
- Stripe DPA: https://stripe.com/gb/legal/dpa

---

**Adoption blocker:** manager approval/signature, UK legal review, controller contact details, UK representative decision, processor/transfer evidence and operational proof are all outstanding. This notice remains **DRAFT — NOT IN FORCE**.
