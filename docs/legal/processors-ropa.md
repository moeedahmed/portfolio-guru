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
| Account setup and service delivery | Doctor; Telegram ID/profile, preferences, account state | Art 6(1)(b), contract | Telegram upstream; Supabase mirror | Account schedule blocked; application controls and field encryption for sensitive values. |
| Case capture, extraction and form recommendation | Doctor; case text/audio/images/documents, extracted fields and corrections; accidental patient health data may occur | Art 6(1)(b) only for the doctor's intended service data. Article 6 and Article 9 positions for accidental third-party patient data are not adopted | Telegram receives originals; Google Vertex AI; local Mac Mini; Supabase mirror | Intended clinical-content maximum 180 days, not proven across stores. Text/doc text locally inspected/redacted before Vertex; scanned images/PDFs may reach Vertex first. |
| Draft preview and Kaizen save | Doctor; draft fields, form choice, Kaizen credentials | Art 6(1)(b); accidental special-category position blocked | Kaizen/RCEM independent destination; hosting/transfer unknown | Kaizen controls saved draft; sensitive credentials application-encrypted and decrypted when needed. |
| Subscription and billing | Paying doctor; tier, Stripe identifiers, payment status and required financial records | Art 6(1)(b); Art 6(1)(c) for identified financial-record duties | Stripe, including controller and processor roles under its terms; global transfers possible | Stripe authoritative; internal legal-retention period blocked. No full card data held by Portfolio Guru. |
| Security, fraud and reliable operation | Doctor; IDs, timestamps, usage, errors, security/fraud signals | Art 6(1)(f), subject to approved LIA | Internal access; relevant hosting/service providers | Short, purpose-based schedule blocked; logs must exclude credentials and unnecessary case content. |
| Consent and terms evidence | Doctor; version, timestamp, user ID and durable contract evidence | Art 6 basis depends on record: contract administration and/or legal obligation | Internal; Supabase mirror where configured | Schedule blocked; retain only as long as needed to evidence the relevant relationship or duty. |
| Rights, complaints and incidents | Doctor and any affected person; request, verification, correspondence, incident evidence | Legal obligation and, where necessary, legitimate interests in legal claims/security | Advisers, regulator, courts or affected suppliers where necessary | Case-specific hold and deletion rules blocked; access restricted to responders. |
| Optional marketing | Doctor; contact and preference | Art 6(1)(a), consent; PECR assessment required | Approved communications provider, if introduced | No optional-marketing workflow evidenced; keep suppression record where required. |

[BLOCKER — Manager-appointed privacy lead + legal counsel: approve the Article 6 map, complete the LIA, and decide the Article 9/DPA 2018 response for accidental patient data. A doctor's consent is not an Article 9 basis for a third-party patient.]

## 3. Processors and service providers acting on instructions

| Provider | Processing and location evidence | Contract/transfer status | Next action |
| --- | --- | --- | --- |
| Google Cloud Vertex AI | Portfolio extraction. Intended live location London (`europe-west2`). Text/document text locally inspected/redacted first; scanned images/PDFs may arrive before local redaction. | Public CDPA exists; account acceptance, exact product scope, subprocessors, data location, no-training and transfer evidence not filed. | [BLOCKER — Moeed: export account-level terms/configuration evidence; legal/privacy lead: assess and approve.] |
| Supabase | Active dedicated project in London (`eu-west-2`) mirrors non-canonical service data; sensitive fields are application-encrypted. | Public DPA exists; account acceptance, subprocessor and transfer evidence not filed. | [BLOCKER — Moeed: export project-region and account DPA evidence; legal/privacy lead: assess and approve.] |
| Other infrastructure support | No additional live processor is established by this evidence pack. OpenMed code existed but was inactive when checked. | Not treated as a live recipient. | Reassess before activation or adding any supplier. |

The local Mac Mini and its files/SQLite database are internal storage, not a processor. They remain the live canonical store. Some local drafts/backups have weak permissions and FileVault was off at the evidence date.

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
| Clinical content/extracted fields | Intended 180-day maximum; cross-store execution and backups not proven. |
| Temporary attachments and processing files | Inventory and deletion timing not proven for every format/path. |
| Credentials | Kept only while needed is the intended principle; final period and deletion proof blocked. |
| Account/usage/consent/support/security data | Purpose-based periods not approved. |
| Financial records | Applicable legal period to be confirmed with accounting/legal advisers. |
| Telegram and Kaizen data | Retention controlled by those independent platforms and the user. |

[BLOCKER — Moeed: produce a store-by-store inventory and deletion evidence; manager + legal/accounting advisers: approve the schedule.]

## 6. Security measures and gaps

- Sensitive credentials and case fields are application-encrypted; keys and provider secrets remain outside the database.
- The canonical SQLite/files and runtime are on a Mac Mini; Supabase is a non-canonical London mirror.
- Credentials are not intended to enter AI prompts.
- Draft-only architecture requires doctor review before a Kaizen draft is saved and never submits to a supervisor.
- Known gaps: FileVault off; weak permissions on some drafts/backups; access review, deletion, restore and incident evidence incomplete.

[BLOCKER — Moeed: remediate and evidence device encryption, permissions, least privilege, backup protection, key management, deletion and recovery tests.]

## 7. Accountability and maintenance

Update this record before a new channel, processor, model, storage location, marketing use or material data flow is activated, and after any incident or material supplier change. Keep dated account-level evidence; a public DPA URL alone does not prove acceptance.

## Official references

- ICO Article 30 documentation: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/accountability-and-governance/documentation/what-do-we-need-to-document-under-article-30-of-the-gdpr/
- ICO legitimate interests: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/a-guide-to-lawful-basis/legitimate-interests/
- ICO international transfers: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/international-transfers/
- Google Cloud CDPA: https://cloud.google.com/terms/data-processing-addendum
- Supabase DPA: https://supabase.com/legal/customer-resources/data-processing-addendum
- Stripe DPA: https://stripe.com/gb/legal/dpa
- Telegram privacy policy: https://telegram.org/privacy/gb

---

This record remains **DRAFT — NOT IN FORCE** and requires manager authority, legal review and operational evidence.
