> **DRAFT — NOT IN FORCE.** Internal legal/accountability readiness note. It is not legal advice, approval to launch or a claim of compliance.

# Portfolio Guru private-beta readiness

**Evidence date:** 20 August 2026
**Decision:** controlled dogfood only; **no wider paid beta or public launch**.

## Verdict

The product has important safeguards: doctor review, draft-only Kaizen saving, application encryption for sensitive fields, intended London Vertex processing, a dedicated London Supabase mirror and an intended 180-day clinical-content limit. The legal entity, UK accountability decisions, consumer contract, supplier/transfer evidence and several security/deletion controls are not adopted or proven.

Solvoro Labs LLC is the recommended controller and contracting entity, but it is manager-managed and Moeed cannot bind it. Manager approval/signature and UK legal review are therefore launch gates.

## Evidence snapshot

| Area | Current evidence | Readiness |
| --- | --- | --- |
| Product boundary | Telegram-first assistant for UK doctors; recommends fields/forms and saves only a doctor-approved Kaizen draft; never submits to a supervisor. Not clinical advice, patient care, an NHS system or a medical device. | Suitable boundary; keep prominent and tested. |
| Patient data/channel | Identifiable patient data is prohibited. Telegram receives original cloud-chat content first. Text/doc text is locally inspected/redacted before Vertex; scanned images/PDFs may reach Vertex first. | **High-risk gap** for original and scanned media. Redaction is not permission. |
| AI | Intended live Vertex AI location London (`europe-west2`). OpenMed code existed but was inactive when checked. | Account terms, location/no-training and transfer evidence still required. |
| Canonical storage | Live canonical SQLite/files on Mac Mini. Dedicated Supabase project active in London (`eu-west-2`) as non-canonical mirror. | Current non-canonical mirror is recorded; canonical cloud migration is not claimed. |
| Security | Sensitive credentials/case fields application-encrypted; keys/secrets outside database. Some drafts/backups have weak permissions; FileVault off. | **High residual risk** pending remediation and access review. |
| Retention/deletion | Intended clinical-content maximum 180 days. `/reset` exists, but full cross-store deletion is unproved. Telegram and Kaizen copies are outside Portfolio Guru deletion. | **Blocked** pending inventory and evidence. |
| External roles | Telegram upstream independent controller; Kaizen authoritative for saved drafts; Stripe authoritative for billing. | Correct role framing; terms/hosting/account evidence outstanding. |
| Entity authority | Texas formation on 23 November 2025 verified; LLC manager-managed; Moeed cannot bind it. | **Blocked** pending manager decision/signature. |

## Boundary for current dogfood

Any continuing controlled dogfood must remain within the existing operator-approved group and must:

- warn before input that identifiable patient data is prohibited in every format;
- keep doctor preview/correction and draft-only Kaizen saving;
- never submit to a supervisor;
- keep the legal documents visibly draft/not in force and avoid compliance claims;
- provide a clear route to stop use and request reset, while disclosing deletion limitations;
- avoid optional marketing and new processors/channels; and
- stop and escalate suspected patient data or a security incident under the approved interim operator procedure.

[BLOCKER — Manager-appointed accountable owner: confirm whether even the current dogfood may continue while the accidental-patient-data and local-security risks remain high.]

## Gates before wider paid or public launch

| Gate | Required closure evidence | Owner/status |
| --- | --- | --- |
| Controller authority | Manager decision adopting Solvoro Labs LLC, authorising controller/contracting status and signed legal pack | [BLOCKER — Manager] |
| Public contacts | Approved service address, business details and monitored privacy/support contacts | [BLOCKER — Manager + legal] |
| UK territorial/representative | Article 27 assessment and appointment/details if required | [BLOCKER — Manager + UK legal] |
| ICO fee | Completed checker, exemption rationale or payment/registration evidence in adopted entity name | [BLOCKER — Manager-appointed privacy lead] |
| Lawful basis | Approved Article 6 record, LIA and accidental Article 9/DPA 2018 incident position | [BLOCKER — Privacy lead + UK legal] |
| DPIA | Treatments evidenced; residual risks accepted by authorised owner; prior-consultation decision recorded | [BLOCKER — Manager/accountable owner] |
| Processor/transfers | Account-level Google, Supabase and Stripe contracts; roles, subprocessors, locations, safeguards and TRAs/data protection tests where required | [BLOCKER — Moeed gathers; privacy/legal approve] |
| Security | FileVault/device encryption, restrictive permissions, least privilege, key/backup controls and access review evidenced | [BLOCKER — Moeed] |
| Retention/deletion/rights | Approved schedule and tested cross-store retention, `/reset`, access/export/correction/restriction/objection workflows | [BLOCKER — Moeed + privacy lead] |
| Breach response | Named lead, escalation route, breach log, 72-hour decision workflow and exercise evidence | [BLOCKER — Manager + privacy lead] |
| Consumer contract | Fair terms with identity/address/contact, full tax-inclusive price, billing, cancellation/refund, durable confirmation, liability, complaints and UK consumer review | [BLOCKER — Manager + finance/legal] |
| Kaizen | Documented automation permission/terms position and hosting/transfer evidence | [BLOCKER — Manager + legal] |
| Product proof | Real Telegram journey proving warnings, redaction boundaries, approval-only draft save and no supervisor submission | [BLOCKER — Moeed; live test only with explicit approval] |

## Current decision

Do not broaden invitations, market publicly or rely on the current legal drafts as accepted terms/notices. A live payment capability does not close the consumer-law or governance gates.

## Official references checked on 20 August 2026

- ICO data protection fee: https://ico.org.uk/for-organisations/data-protection-fee/
- ICO UK representative guidance: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/international-transfers/receiving-personal-information-from-the-eea/
- ICO breach reporting: https://ico.org.uk/for-organisations/report-a-breach/personal-data-breach/
- GOV.UK distance selling: https://www.gov.uk/online-and-distance-selling-for-businesses
- Google Cloud CDPA: https://cloud.google.com/terms/data-processing-addendum
- Supabase DPA: https://supabase.com/legal/customer-resources/data-processing-addendum
- Stripe DPA: https://stripe.com/gb/legal/dpa
- Telegram privacy policy: https://telegram.org/privacy/gb

---

This note remains **DRAFT — NOT IN FORCE**. Wider paid/public launch is blocked.
