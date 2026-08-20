> **DRAFT — NOT IN FORCE / NOT APPROVED.** This DPIA is an internal risk assessment for manager, privacy and UK legal review. It is not legal advice, a compliance claim or residual-risk acceptance.

# Portfolio Guru data protection impact assessment

**Assessment date:** 20 August 2026
**Proposed controller:** Solvoro Labs LLC, awaiting manager approval
**DPIA owner:** [BLOCKER — Manager: appoint an accountable owner.]
**Review trigger:** before launch expansion and on any material change to channel, model, processor, storage, patient-data handling or Kaizen automation.

## 1. Screening decision

A DPIA is appropriate because the Service combines AI-assisted interpretation of clinical case material, messaging-platform collection, credential storage, third-party portfolio automation and possible accidental patient health data. The scale needed to determine every formal trigger has not been evidenced. This assessment therefore takes the cautious high-risk route without claiming that it is complete or approved.

Wider paid or public launch must remain blocked while high residual risks and governance decisions remain open.

## 2. Processing and purpose

Portfolio Guru helps a UK doctor create an ePortfolio draft:

1. The doctor sends text, voice, audio, a photo or a document in a Telegram cloud chat. Telegram receives and stores the original first.
2. Portfolio Guru inspects and redacts text/document text locally before sending it to Google Vertex AI. Scanned images and scanned PDFs may reach Vertex before local redaction.
3. Vertex AI extracts draft fields and recommends a form. The intended live location is London (`europe-west2`). OpenMed code existed but was inactive when checked.
4. The Service stores canonical data in local SQLite/files on a Mac Mini. A dedicated Supabase project in London (`eu-west-2`) is active as a non-canonical mirror.
5. The doctor reviews/corrects the draft. Only after approval may the Service use application-encrypted credentials to save a draft in Kaizen. It never submits to a supervisor.
6. Stripe is authoritative for billing. Kaizen is authoritative for saved drafts. Telegram controls its own messages.

The purpose is administrative ePortfolio capture, extraction and draft-saving. It is not clinical advice, patient care, an NHS system or a medical device.

## 3. Scope and people affected

| Item | Scope |
| --- | --- |
| Intended users | Adult UK doctors using their own Kaizen account. Volume and case frequency are not evidenced in this pack. |
| Intended personal data | Telegram/account identifiers, portfolio content about the doctor, credentials, service usage, consent/terms evidence, billing identifiers and support/security records. |
| Prohibited but foreseeable data | Identifiable or potentially identifiable patient details in text, audio, images or documents; this can be special-category health data. |
| Systems | Telegram, Mac Mini SQLite/files, Vertex AI London, Supabase London mirror, Kaizen and Stripe. |
| Retention | Intended 180-day clinical-content maximum; other schedules and cross-store enforcement remain unproved. |
| Geography | UK users; proposed US controller; UK-region cloud projects; possible global supplier access/transfers. |

## 4. Necessity, proportionality and lawful basis

- Account and requested draft processing are proposed under Article 6(1)(b), contract.
- Required financial records are proposed under Article 6(1)(c), legal obligation, once the actual duty and period are documented.
- Proportionate security, fraud prevention and reliable operation are proposed under Article 6(1)(f), subject to an LIA.
- Optional marketing would require consent and a PECR assessment.
- The product prohibits identifiable patient data. A doctor's consent cannot provide an Article 9 condition for a third-party patient. The lawful handling of accidental patient data, including containment and deletion, is unresolved.

Data-minimisation measures observed or intended include draft-only use, doctor review, no supervisor submission, field-level encryption, keys outside the database, no credentials in AI prompts, local text redaction and a 180-day clinical-content target. Those measures do not cure the upstream Telegram copy, pre-redaction image/PDF path, local-device weaknesses or unproved deletion.

[BLOCKER — Manager-appointed privacy lead + legal counsel: approve the Article 6 record, LIA and accidental special-category incident position before launch expansion.]

## 5. Consultation and evidence

| Consultation/evidence | Status |
| --- | --- |
| Manager authority | Not obtained for controller adoption, contracts, this DPIA or residual-risk acceptance. |
| UK legal/privacy review | Not completed. |
| User consultation | No documented consultation evidence included in this pack. |
| Google, Supabase and Stripe | Public DPA/CDPA pages reviewed; account-level acceptance, exact scope and transfer evidence absent. |
| Telegram | Public privacy policy confirms cloud-chat storage; treated as an upstream independent platform/controller. |
| Kaizen | Terms/automation permission and hosting/transfer evidence unresolved. |
| Operational inspection | Canonical Mac Mini SQLite/files; London Supabase mirror; application encryption; FileVault off; weak permissions on some drafts/backups; deletion proof incomplete. |

## 6. Risk assessment and required treatment

| Risk to people | Current risk | Existing/observed measures | Required treatment and proposed residual risk |
| --- | --- | --- | --- |
| P1. Identifiable patient data reaches Telegram before Portfolio Guru can intervene | High | Clear prohibition; redaction guidance | Put warning before first message and at every upload; approved detection/containment/deletion playbook; channel limitation in notice. Residual **High** because Telegram keeps its own copy. |
| P2. Scanned image/PDF reaches Vertex before local redaction | High | User prohibition; intended London Vertex location | Pre-upload warning; block or locally inspect these formats before Vertex, or obtain approved alternative control; test with representative files. Residual **High** until implemented/proved. |
| P3. No settled Article 9/DPA 2018 route for accidental patient data | High | Product rejects identifiable patient data | Legal decision and incident-only minimisation/quarantine/deletion procedure. Do not use the doctor's consent as the patient's condition. Residual **High** until approved. |
| P4. Local device/file compromise exposes credentials or case material | High | Sensitive fields application-encrypted; keys outside database | Enable/evidence full-device encryption; fix file/backup permissions; least-privilege and access review; key rotation/recovery; restore test. Residual **High** until proof, then requires sign-off. |
| P5. `/reset` or retention misses SQLite, files, backups, Supabase or temporary artefacts | High | Intended 180-day clinical retention; reset path exists | Cross-store inventory, automated tests, backup-expiry design and sampled deletion certificate. Explain Telegram/Kaizen exclusions. Residual **High** until proven. |
| P6. Supplier contract or restricted transfer lacks valid evidence | High | London cloud regions; public supplier DPAs | File account terms, subprocessor list, role, transfer map/mechanism and TRA/data protection test as required. Region is not complete proof. Residual **High** until accepted. |
| P7. Account takeover permits unauthorised draft access/save | High | Telegram and Kaizen authentication; draft approval step | Threat model messaging takeover; high-risk confirmation/re-authentication; session revocation; access alerts and support procedure. Residual **Medium/High**, sign-off required. |
| P8. AI error creates misleading portfolio content | Medium | Advisory recommendation; doctor preview/correction; draft only | Clear uncertainty wording, behavioural tests, audit trail of user approval, easy correction. Residual **Low/Medium**. |
| P9. Kaizen automation breaches platform expectations or exposes data in unknown hosting | High | User approves draft save; Kaizen authoritative | Obtain permission or reasoned terms assessment; document hosting/transfers; stop automation if not permitted. Residual **High** until resolved. |
| P10. Consumer cannot exercise rights or receives incomplete deletion | High | `/reset` intended | Monitored contact, identity check, rights log, response playbook and end-to-end tests. Residual **High** until proved. |
| P11. Breach response is late or incomplete | High | Operator alerting exists at product level | Named incident lead, 24/7 route, risk assessment, breach log, 72-hour ICO decision workflow and affected-person notification test. Residual **High** until exercised. |
| P12. AI/provider behaviour changes without reassessment | Medium | Dedicated projects and documented intended regions | Versioned supplier/model inventory, change monitoring and DPIA review gate before activation. Residual **Medium**. |

## 7. Residual-risk decision

High residual risks remain for upstream Telegram collection, scanned media, accidental patient data, local security, deletion, supplier/transfers, account takeover, Kaizen permission, rights and breach response. This DPIA does not accept them.

| Decision | Required authority/status |
| --- | --- |
| Measures approved | [BLOCKER — Manager: approve only after evidence and legal/privacy advice.] |
| Residual risk accepted | [BLOCKER — Manager-appointed accountable owner: record each accepted risk, rationale and date. Moeed cannot accept on the LLC's behalf.] |
| UK representative/controller position | [BLOCKER — Manager + UK legal counsel: decide and document.] |
| ICO prior consultation | [BLOCKER — Accountable owner + legal counsel: reassess after treatment; if high residual risk remains that cannot be reduced, decide whether Article 36 prior consultation is required before processing.] |
| Review/approval date | Not set; this DPIA is **not approved**. |

## 8. Launch decision

Controlled dogfood may continue only within the separately approved operational boundary, with no relaxation of the patient-data prohibition. Do not expand to a wider paid beta or public launch until the accountability register's launch gates are closed and residual risk is signed off by someone authorised to bind the company.

## Official references

- ICO DPIA guidance: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/accountability-and-governance/data-protection-impact-assessments-dpias/
- ICO breach reporting: https://ico.org.uk/for-organisations/report-a-breach/personal-data-breach/
- ICO international transfers: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/international-transfers/
- ICO legitimate interests: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/a-guide-to-lawful-basis/legitimate-interests/
- Google Cloud CDPA: https://cloud.google.com/terms/data-processing-addendum
- Supabase DPA: https://supabase.com/legal/customer-resources/data-processing-addendum
- Stripe DPA: https://stripe.com/gb/legal/dpa
- Telegram privacy policy: https://telegram.org/privacy/gb

---

This DPIA remains **DRAFT — NOT IN FORCE / NOT APPROVED**.
