> **DRAFT — NOT IN FORCE.** Internal accountability record prepared for manager and UK legal review. It does not claim compliance or prove that contracts, safeguards or controls are in place.

# Portfolio Guru record of processing activities and recipient register

**Evidence date:** 20 August 2026
**Proposed controller:** Solvoro Labs LLC, awaiting manager approval
**ROPA owner:** [BLOCKER — Manager: appoint an accountable privacy lead and authorise this record.]

This ROPA will be maintained even if counsel concludes that a small-organisation exception could apply. The processing is recurring and can involve sensitive case material, so maintaining the record is the prudent accountability position.

## 1. Controller context

Solvoro Labs LLC was formed in Texas on 23 November 2025 and is manager-managed. Moeed cannot bind it. It intentionally offers a recurring service to UK users, so UK GDPR territorial scope and the Article 27 UK-representative requirement must be addressed.

[BLOCKER — Manager + UK legal counsel: approve controller status, appoint a privacy lead, complete the Article 27 assessment and add the approved controller/representative contact details.]

## 2. Processing activities

| Activity and purpose | People and data | Proposed lawful basis | Recipients/transfers | Retention and safeguards |
| --- | --- | --- | --- | --- |
| Account setup and service delivery | Doctor; Telegram ID/profile, preferences, account state | Art 6(1)(b), contract | Telegram upstream; local Mac Mini; Supabase only if separately activated | Account schedule blocked; application controls and field encryption for sensitive values. |
| Case capture, extraction and form recommendation | Doctor; case text/audio/images/documents, extracted fields and corrections; accidental patient health data may occur | Art 6(1)(b) only for the doctor's intended service data. Article 6 and Article 9 positions for accidental third-party patient data are not adopted | Telegram receives originals; Google Vertex AI; local Mac Mini; encrypted GCS backup; Supabase only if activated | Intended clinical-content maximum 180 days, not proven across stores/backups. Text/doc text locally inspected/redacted before AI extraction; scanned images/PDFs may reach Vertex first. |
| Draft preview and Kaizen save | Doctor; draft fields, form choice, Kaizen credentials | Art 6(1)(b); accidental special-category position blocked | Kaizen/RCEM independent destination; hosting/transfer unknown | Kaizen controls saved draft; sensitive credentials application-encrypted and decrypted when needed. |
| Subscription and billing | Paying doctor; tier, Stripe identifiers, payment status and required financial records | Art 6(1)(b); Art 6(1)(c) for identified financial-record duties | Stripe, including controller and processor roles under its terms; global transfers possible | Stripe authoritative; internal legal-retention period blocked. No full card data held by Portfolio Guru. |
| Security, fraud and reliable operation | Doctor; IDs, timestamps, usage, errors, security/fraud signals | Art 6(1)(f), subject to approved LIA | Internal access; relevant hosting/service providers | Short, purpose-based schedule blocked; logs must exclude credentials and unnecessary case content. |
| Consent and terms evidence | Doctor; version, timestamp, user ID and durable contract evidence | Art 6 basis depends on record: contract administration and/or legal obligation. The code's current Article 9(2)(a) label is not adopted by this pack | Internal; encrypted GCS backup; Supabase only if activated | Schedule blocked; current reset intentionally retains consent history. Retain only as long as needed to evidence the relevant relationship or duty. |
| Rights, complaints and incidents | Doctor and any affected person; request, verification, correspondence, incident evidence | Legal obligation and, where necessary, legitimate interests in legal claims/security | Advisers, regulator, courts or affected suppliers where necessary | Case-specific hold and deletion rules blocked; access restricted to responders. |
| Optional marketing | Doctor; contact and preference | Art 6(1)(a), consent; PECR assessment required | Approved communications provider, if introduced | No optional-marketing workflow evidenced; keep suppression record where required. |

[BLOCKER — Manager-appointed privacy lead + legal counsel: approve the Article 6 map, complete the LIA, and decide the Article 9/DPA 2018 response for accidental patient data. A doctor's consent is not an Article 9 basis for a third-party patient.]

## 3. Processors and service providers acting on instructions

| Provider | Processing and location evidence | Contract/transfer status | Next action |
| --- | --- | --- | --- |
| Google Cloud Vertex AI | Portfolio extraction. The running bot inspected on 20 August 2026 had Vertex enabled in London (`europe-west2`); text extraction excluded the configured DeepSeek route while that flag was effective. Text/document text is locally inspected/redacted first; scanned images/PDFs may arrive before local redaction. | Public CDPA and current data-governance/location pages exist; account acceptance, exact product scope, subprocessors, retention/no-training configuration and transfer evidence not filed. The client can fall back to the Google developer API if Vertex configuration is absent. | [BLOCKER — Moeed: make unapproved fallback fail closed and export account/configuration evidence; legal/privacy lead: assess and approve.] |
| Google Cloud Storage | Encrypted off-device backups of canonical SQLite, persistence and draft state are configured for a GCS bucket. Code describes a 90-day bucket lifecycle. | Public CDPA may cover the service, but bucket region, IAM, account acceptance, lifecycle, restoration/deletion and transfer evidence are not filed here. | [BLOCKER — Moeed: export bucket location, IAM, encryption/lifecycle and account evidence; legal/privacy lead: assess and approve.] |
| Supabase | A dedicated London (`eu-west-2`) project and non-canonical mirror code exist, but the inspected bot process had neither required Supabase runtime variable. An active live mirror was not established at the evidence date. | Public DPA exists; account acceptance, project region, subprocessor and transfer evidence not filed. | Keep inactive until approved; export project/account evidence before any activation. |
| Bitwarden Secrets Manager / Healthchecks.io | Bitwarden supplies runtime secrets; Healthchecks receives intended liveness/job metadata. No case content is intended. | Account terms, access, minimisation, subprocessors and transfer position not filed. | [BLOCKER — Moeed gathers; privacy/security lead classifies and approves or records exclusion rationale.] |
| Inactive/conditional model paths | Code contains Google developer API, DeepSeek and OpenAI routes. They were not established as active in the inspected bot process; OpenMed-related code was also not established as active. | Not approved recipients. Code presence and failover behaviour create change/fail-open risk. | Fail closed and reassess before activation; maintain a tested provider allow-list. |

The local Mac Mini and its files/SQLite database are internal storage, not a processor. They remain the live canonical store. Some local files/backups have weak permissions and FileVault was off at the evidence date.

## 4. Independent controllers and mixed-role recipients

| Recipient | Role and boundary | Evidence/status |
| --- | --- | --- |
| Telegram | Upstream independent platform/controller for accounts and cloud chats. It receives original text/media before Portfolio Guru and retains copies under its own policy. Portfolio Guru cannot delete Telegram's copies. | Telegram privacy policy reviewed; no claim that Telegram is an Article 28 processor for Portfolio Guru. |
| Kaizen / RCEM ePortfolio | Independent destination/controller for the user's portfolio and authoritative copy of saved drafts. | Automation permission, contracting terms, hosting and transfer locations unresolved. |
| Stripe | Authoritative billing platform. Its DPA describes both processor and controller activities and global transfers. Exact Portfolio Guru account entity/scope remains to be evidenced. | Public DPA reviewed; account-level agreement and transfer evidence blocked. |
| Regulators, courts and professional advisers | Separate recipients/controllers for their own legal functions where disclosure is necessary and lawful. | Case-specific only. |

## 5. Retention schedule

| Record | Position at evidence date |
| --- | --- |
| Clinical content/extracted fields | Intended 180-day maximum; cross-store execution not proven. Local backups are configured for 30 days and GCS for 90 days, but live lifecycle/deletion evidence is not filed. |
| Temporary attachments and processing files | Inventory and deletion timing not proven for every format/path. |
| Credentials | Kept only while needed is the intended principle; final period and deletion proof blocked. |
| Account/usage/consent/support/security data | Purpose-based periods not approved. |
| Financial records | Applicable legal period to be confirmed with accounting/legal advisers. |
| Telegram and Kaizen data | Retention controlled by those independent platforms and the user. |

[BLOCKER — Moeed: produce a store-by-store inventory and deletion evidence; manager + legal/accounting advisers: approve the schedule.]

## 6. Security measures and gaps

- Sensitive credentials and case fields are application-encrypted; keys and provider secrets remain outside the database.
- The canonical SQLite/files and runtime are on a Mac Mini; an encrypted off-device GCS backup is configured. Supabase mirror code/project exist but the active bot mirror was not established.
- Credentials are not intended to enter AI prompts.
- Draft-only architecture requires doctor review before a Kaizen draft is saved and never submits to a supervisor.
- Known gaps: FileVault off; weak permissions on some local files/backups; provider fail-open behaviour; access review, deletion, restore and incident evidence incomplete.

The shipped consent record currently labels processing as Article 9(2)(a) explicit consent and promises erasure of Portfolio Guru's stored data with `/reset`. The legal position in this pack does not adopt the first statement for accidental third-party patient data, and the observed reset code intentionally retains consent history and a Stripe billing link. This inconsistency is a transparency and lawful-basis blocker.

[BLOCKER — Moeed: remediate and evidence device encryption, permissions, least privilege, backup protection, key management, deletion and recovery tests.]

## 7. Accountability and maintenance

Update this record before a new channel, processor, model, storage location, marketing use or material data flow is activated, and after any incident or material supplier change. Keep dated account-level evidence; a public DPA URL alone does not prove acceptance.

## Official references

- ICO Article 30 documentation: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/accountability-and-governance/documentation/what-do-we-need-to-document-under-article-30-of-the-gdpr/
- ICO legitimate interests: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/a-guide-to-lawful-basis/legitimate-interests/
- ICO international transfers: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/international-transfers/
- Google Cloud CDPA: https://cloud.google.com/terms/data-processing-addendum
- Vertex AI data governance: https://cloud.google.com/vertex-ai/generative-ai/docs/data-governance
- Vertex AI locations: https://cloud.google.com/vertex-ai/generative-ai/docs/learn/locations
- Supabase DPA: https://supabase.com/legal/customer-resources/data-processing-addendum
- Stripe DPA: https://stripe.com/gb/legal/dpa
- Telegram privacy policy: https://telegram.org/privacy/gb

---

This record remains **DRAFT — NOT IN FORCE** and requires manager authority, legal review and operational evidence.
