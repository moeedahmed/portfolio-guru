# Portfolio Guru Form Coverage

Last verified: 2026-06-03.

Evidence sources:

- Offline wiring tests in `backend/tests/`.
- Deterministic field maps in `backend/kaizen_form_filer.py`.
- Read-only Kaizen create-form scrape against trusted ACCS and Intermediate fixture accounts.

No live save, submit, delete, or supervisor action was performed during the scrape.

## What "mapped" means

A form is fileable only when all of these exist:

- a Kaizen event UUID
- an extraction schema in `FORM_SCHEMAS`
- a deterministic DOM field map in `FORM_FIELD_MAP`
- a supported filer-router entry
- profile/catalogue visibility in `TRAINING_LEVEL_FORMS` or `FORM_CATEGORIES`

UUID-known alone is not enough. Portfolio Guru hides UUID-known forms until the schema and deterministic field map are safe, because otherwise the bot would create broken buttons.

## Deterministic Fileable Coverage

The shared EM form family is deterministic and fileable across the relevant curriculum/profile surfaces, including 2021 variants where Kaizen exposes them:

- CBD
- DOPS
- Mini-CEX
- ACAT
- ACAF
- LAT
- QIAT
- Journal Club / JCF
- ESLE
- Procedural Log
- Reflective Log
- Self-directed Learning
- Teaching
- Audit
- Research
- Educational Activity
- Formal Course
- Teaching Observation
- Confidentiality / Teaching Confidentiality
- Complaint
- Serious Incident
- Critical Incident
- Appraisal
- Clinical Governance
- Educational Meeting
- Educational Meeting Supplement
- PDP
- Ultrasound Case
- Management forms: rota, risk, recruitment, project/QI, risk process, training event, guideline, information management, induction, management experience, report, complaint, business case, cost improvement, equipment/service

## ACCS Forms Promoted After 2026-06-03 Scrape

These ACCS-specific forms are now deterministic and may be shown as fileable where the ACCS profile exposes them:

- DOPS ACCS 2025: `DOPS_ACCS`
- DOPS ACCS 2021: `DOPS_ACCS_2021`
- ACCS Procedural Log 2025: `PROCEDURAL_LOG_ACCS`
- ACCS Procedural Log 2021: `PROCEDURAL_LOG_ACCS_2021`

They use the same draftable evidence pattern as the existing DOPS and Procedural Log families, with ACCS-specific procedure dropdown IDs.

## UUID-Known But Hidden

These forms are visible in Kaizen selectors or admin surfaces but remain hidden in Portfolio Guru until their workflow semantics are mapped properly:

- ASAT
- EPA1
- EPA2
- HALO ICM
- HALO Procedural Sedation
- IAC
- MCR/MTR ACCS
- ACCS Progression
- Intermediate Progression
- Educational Agreement

Reason: the read-only scrape found UUIDs and some page structure, but these are not yet safe as normal "file a WPBA draft from a case" targets. Several are assessment, progression, agreement, or review workflows rather than simple trainee evidence draft forms.

## Utility/Admin Surfaces

These are intentionally route-recognised or UUID-known but not user-selectable evidence forms:

- Add Post
- Add Supervisor
- File Upload
- Out of Programme
- Higher Progression
- Absence
- CCT Application

## Current Product State

- Sana / non-training uses the shared 2021 form family.
- HST uses the shared higher catalogue with 2021/2025 variants layered by curriculum.
- ACCS now includes the safely mapped ACCS DOPS and ACCS Procedural Log forms.
- ACCS/Intermediate specialist assessment and progression workflows remain recorded but hidden until mapped deliberately.
