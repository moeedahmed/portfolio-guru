> **DRAFT — NOT IN FORCE.** Internal decision and evidence register for manager, operational, privacy and UK legal review. It is not legal advice, a compliance claim or launch approval.

# Portfolio Guru accountability register

**Evidence date:** 20 August 2026
**Recommended controller/contracting entity:** Solvoro Labs LLC, awaiting manager approval
**Status key:** **Observed** = evidenced product fact; **Proposed** = recommended but not adopted; **Blocked** = required decision/evidence missing; **Not active** = excluded from the current live flow.

Solvoro Labs LLC was formed in Texas on 23 November 2025 and is manager-managed. Moeed cannot bind the company. Manager action is required wherever this register adopts company status, signs terms/DPAs, appoints a representative, pays/registers in the entity's name or accepts residual risk.

| Decision/control | Evidence | Status | Owner | Next action |
| --- | --- | --- | --- | --- |
| Controller and contracting entity | Official Texas formation evidence; signed operating agreement says manager-managed | **Proposed/Blocked** | Manager | Approve Solvoro Labs LLC and authorise/sign the adopted legal pack after advice. |
| Public identity/contact/address | No approved public service address or monitored privacy/support contact supplied | **Blocked** | Manager + legal counsel | Approve publishable details; do not invent or use a private residential address. |
| UK GDPR territorial scope | US entity intentionally offers recurring services to people in the UK | **Proposed/Blocked** | UK legal counsel + manager | Record scope opinion and affected processing. |
| Article 27 UK representative | ICO guidance says an overseas provider offering UK services generally appoints a UK representative unless the narrow exemption applies; no appointment/exemption evidence | **Blocked** | Manager + UK legal counsel | Complete assessment; if required, appoint in writing and publish details. |
| ICO data-protection fee | ICO says organisations using personal information pay unless exempt; no checker/payment/exemption evidence | **Blocked** | Manager-appointed privacy lead | Run checker in adopted controller's name; file result and payment/registration evidence or reasoned exemption. |
| Routine lawful bases | Contract proposed for account/service/billing; legal obligation for required financial records; legitimate interests for proportionate security/fraud/operations; consent for optional marketing | **Proposed/Blocked** | Privacy lead + legal counsel | Approve purpose-by-purpose basis before relying on it. |
| Legitimate interests assessment | No completed purpose/necessity/balancing record | **Blocked** | Privacy lead | Complete LIA for security, fraud prevention and operations; disclose interests and review on change. |
| Accidental patient/special-category data | Identifiable patient data prohibited; doctor cannot consent for third-party patient; Telegram receives originals; scanned media may reach Vertex pre-redaction | **High-risk/Blocked** | Manager + privacy lead + legal counsel | Approve reject/minimise/quarantine/delete incident process and settle any Art 6, Art 9 and DPA 2018 conditions. |
| Data map and ROPA | Current flows documented in `processors-ropa.md`; recurring sensitive processing; small-organisation exemption not relied upon | **Proposed** | Privacy lead | Approve and maintain on every material change. |
| Google Cloud processor evidence | Inspected bot used Vertex London (`europe-west2`); encrypted off-device GCS backup configured; public CDPA and product pages available | **Blocked** | Moeed gathers; privacy/legal approve | File account acceptance, exact service/tier, Vertex retention/no-training configuration, GCS region/IAM/lifecycle, subprocessors, security and transfer terms. |
| Provider route enforcement | Vertex mode excludes the configured DeepSeek text route, but the client falls back to the Google developer API if Vertex prerequisites are absent; conditional OpenAI paths exist | **High-risk/Blocked** | Moeed + privacy/security lead | Make the approved provider route fail closed; test the allow-list and monitoring; reassess before any provider/model activation. |
| Supabase processor evidence | Dedicated London (`eu-west-2`) project and mirror code exist; inspected bot had neither required runtime variable, so active mirroring was not established | **Inactive/Blocked** | Moeed gathers; privacy/legal approve | Keep inactive; file project-region, account DPA, subprocessors, security and transfer evidence before activation. |
| Stripe roles/DPA/transfers | Stripe authoritative for billing; public DPA describes processor/controller roles and global transfers | **Blocked** | Moeed gathers; finance/privacy/legal approve | File contracting entity, account terms, DPA scope, transfer mechanism and relevant Stripe privacy notice. |
| Telegram role and limitation | Telegram privacy policy states cloud chats store messages/media; Telegram receives original before Portfolio Guru | **Observed** | Privacy lead | Keep classified as upstream independent controller; disclose Portfolio Guru cannot delete Telegram copies. |
| Kaizen role, permission and hosting | Kaizen authoritative for saved drafts; draft-only automation; terms/permission and hosting unknown | **Blocked** | Manager + legal counsel | Obtain permission or reasoned terms assessment; evidence hosting/transfers; stop expansion until resolved. |
| Other operational suppliers | Bitwarden Secrets Manager supplies runtime secrets; Healthchecks.io receives intended liveness/job metadata; no case content intended | **Blocked** | Moeed gathers; privacy/security approve | File account terms, access, minimisation, subprocessors and transfer position, or record a reasoned exclusion. |
| Other processors/models | Google developer API, DeepSeek, OpenAI and OpenMed-related paths exist but were not established as active in the inspected bot process | **Not active/Blocked** | Moeed + privacy lead | Fail closed, reassess and approve before any activation, new model, channel or supplier. |
| Restricted transfers | US proposed controller; possible supplier support/subprocessor/global access; region alone is insufficient | **Blocked** | Privacy lead + legal counsel | Map transfers; file adequacy/safeguard and TRA/data protection test where required. |
| Clinical-content retention | Intended maximum 180 days; local/GCS backup periods configured for 30/90 days but not evidenced at account level | **Proposed/Blocked** | Moeed + privacy lead | Prove across SQLite, files, temporary data, GCS and any activated Supabase mirror; approve exceptions/holds. |
| Other retention | Credential, account, consent, usage, support, logs and financial schedules not approved | **Blocked** | Privacy lead + finance/legal | Set purpose-based periods and automated/manual disposal evidence. |
| Data-subject requests | `/reset` exists; access/export/correction/restriction/objection and identity workflow not evidenced | **Blocked** | Moeed + privacy lead | Create request log/playbook and test each right across all stores within applicable deadlines. |
| Deletion proof | Full cross-store `/reset` deletion unproved; Telegram/Kaizen copies out of scope | **Blocked** | Moeed | Inventory stores and backups; automate tests; produce sampled deletion evidence and clear user disclosure. |
| Consent/reset transparency | Shipped consent labels Article 9(2)(a) and promises erasure of Portfolio Guru stored data with `/reset`; code intentionally retains consent history and a Stripe billing link | **High-risk/Blocked** | Manager + privacy/legal; Moeed implements | Settle lawful basis/evidence retention; version and test consistent consent, privacy and reset wording before wider use. |
| Breach response | No approved named lead, documented assessment/log or exercised 72-hour decision workflow in this pack | **Blocked** | Manager + privacy lead | Approve plan; exercise detection, containment, risk assessment, ICO decision and high-risk notification. |
| Access review | Local Mac Mini, cloud projects, secrets and backups require a current authorised-user review | **Blocked** | Moeed; manager validates | Record users/roles/last use, remove excess access, review keys and repeat at set cadence. |
| Local-device/file security | Sensitive fields application-encrypted; keys outside database; FileVault off; some draft/backup permissions weak | **High-risk/Blocked** | Moeed + accountable owner | Enable/evidence disk encryption, fix permissions, protect backups, document key rotation/recovery; accept residual risk. |
| DPIA and residual risk | `dpia.md` identifies high residual risks and is not approved | **Blocked** | Manager-appointed owner + legal/privacy advisers | Close treatments; decide Article 36 consultation if unmitigated high risk remains; sign/date acceptance. |
| Consumer provider details | Business identity/contact/address required for distance selling and absent | **Blocked** | Manager + UK consumer lawyer | Approve details for terms, checkout and durable confirmation. |
| Price, VAT and billing | Live billing exists, but no legally adopted tax-inclusive price/VAT/period/renewal decision in this pack | **Blocked** | Manager + finance/legal | Approve and reconcile terms, checkout and Stripe configuration. |
| Cancellation/refunds/durable confirmation | Route, statutory information, service-start acknowledgement, refund policy, cancellation form and durable confirmation not proven | **Blocked** | Manager + UK consumer lawyer; Moeed implements | Decide, implement and test end to end before wider paid launch. |
| Liability, complaints and jurisdiction | Fair consumer allocation, cap, complaints/ADR and UK-wide venue wording not adopted | **Blocked** | Manager + UK consumer lawyer | Approve without overbroad exclusions or consumer indemnity. |
| Legal-pack adoption | Six documents remain draft/not in force; manager signature/legal review absent | **Blocked** | Manager + UK legal counsel | Review together, resolve blockers, version, sign/adopt and retain evidence. |
| Wider paid/public launch | Security, deletion, patient-data, legal authority, supplier/transfers, consumer and Kaizen gates open | **STOP** | Manager | No expansion until every applicable launch gate is closed and proof reviewed. |

## Evidence index and official sources

| Topic | Source |
| --- | --- |
| ICO fee | https://ico.org.uk/for-organisations/data-protection-fee/ |
| UK GDPR Article 27 text | https://www.legislation.gov.uk/eur/2016/679/article/27 |
| Article 30 documentation | https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/accountability-and-governance/documentation/what-do-we-need-to-document-under-article-30-of-the-gdpr/ |
| Legitimate interests/LIA | https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/a-guide-to-lawful-basis/legitimate-interests/ |
| International transfers | https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/international-transfers/ |
| Breach reporting | https://ico.org.uk/for-organisations/report-a-breach/personal-data-breach/ |
| UK distance selling | https://www.gov.uk/online-and-distance-selling-for-businesses |
| Google Cloud CDPA | https://cloud.google.com/terms/data-processing-addendum |
| Vertex AI data governance | https://cloud.google.com/vertex-ai/generative-ai/docs/data-governance |
| Vertex AI locations | https://cloud.google.com/vertex-ai/generative-ai/docs/learn/locations |
| Supabase DPA | https://supabase.com/legal/customer-resources/data-processing-addendum |
| Stripe DPA | https://stripe.com/gb/legal/dpa |
| Telegram privacy | https://telegram.org/privacy/gb |

## Review rule

Review this register before any launch expansion and whenever there is a material change to the entity, channel, model, processor, region, retention, payment terms, Kaizen automation or patient-data handling. Attach dated evidence; do not mark a control complete from a public webpage alone.

---

This register remains **DRAFT — NOT IN FORCE**. It records blockers; it does not close them.
