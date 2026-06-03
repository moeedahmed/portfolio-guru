"""Golden offline-QA cases for Haris (ACCS/Intermediate) and Sana (SAS).

All clinical detail is anonymised: ages and presentations are typical EM
encounters with no real identifiers. The cases exercise three portfolio shapes
that have historically branched the form catalogue / draft preview:

- Haris ACCS  → trainee with ACCS stage band, CBD/Mini-CEX/ESLE flow.
- Haris INT   → DREAM Pathway intermediate; QIAT/ACAT/REFLECT.
- Sana  SAS   → non-trainee CESR/Portfolio Pathway; stage select stays blank,
                 trainee-only SLEs (DOPS/ACAT/Mini-CEX) must not appear,
                 supported forms include LAT_2021, REFLECT_LOG_2021, QIAT.

Text cases drive the bot through case-text → form-recommendation → form-tap.
Multimodal cases drive the same path through synthetic photo/voice/document
updates with offline-patched extractors, so the transcript captures what the
bot thought it read without contacting Telegram.
"""

from __future__ import annotations

from tests.qa_transcript import CaseDefinition, Step


HARIS_ACCS_PROFILE = {
    "has_credentials": True,
    "training_level": "ACCS",
    "curriculum": "2025",
    "voice_profile": None,
}

HARIS_INTERMEDIATE_PROFILE = {
    "has_credentials": True,
    "training_level": "INTERMEDIATE",
    "curriculum": "2025",
    "voice_profile": None,
}

SANA_SAS_PROFILE = {
    "has_credentials": True,
    "training_level": "SAS",
    "curriculum": "2021",
    "voice_profile": None,
}


CASES: list[CaseDefinition] = [
    CaseDefinition(
        case_id="haris-accs-cbd-chest-pain",
        persona="Haris (ACCS junior)",
        profile=HARIS_ACCS_PROFILE,
        recommended_forms=[
            ("CBD", "Reflective discussion of acute coronary case"),
            ("MINI_CEX", "Observed clerking with focused exam"),
        ],
        draft_form_type="CBD",
        draft_fields={
            "date_of_encounter": "17/3/2026",
            "clinical_setting": "ED resus",
            "patient_presentation": "Middle-aged adult, central chest pain, dynamic ECG changes",
            "clinical_reasoning": "Treated as ACS, dual antiplatelet, escalated to cardiology",
            "reflection": "Earlier 12-lead repeat would have triggered cath lab activation faster",
            "curriculum_links": ["SLO1", "SLO3"],
            "key_capabilities": [
                "SLO1 KC1: Assess and stabilise the acutely unwell patient",
            ],
        },
        steps=[
            Step(
                label="send-case-text",
                text=(
                    "ACCS CT2 in resus — adult with central chest pain, dynamic ECG "
                    "changes, troponin rising. Activated cath lab, reflected on "
                    "escalation timing."
                ),
                expect_button_any=("FORM|best", "FORM|CBD"),
            ),
            Step(
                label="tap-form-cbd",
                callback="FORM|CBD",
                expect_text_any=("Case-Based Discussion", "CBD"),
            ),
        ],
    ),
    CaseDefinition(
        case_id="haris-accs-mini-cex-paeds",
        persona="Haris (ACCS junior)",
        profile=HARIS_ACCS_PROFILE,
        recommended_forms=[
            ("MINI_CEX", "Observed paediatric assessment"),
            ("CBD", "Reflective discussion of safety-netting"),
        ],
        draft_form_type="MINI_CEX",
        draft_fields={
            "date_of_encounter": "18/3/2026",
            "clinical_setting": "ED paediatric area",
            "patient_presentation": "Young child with febrile illness and rash",
            "clinical_reasoning": "Differential included meningococcal sepsis; followed escalation pathway",
            "reflection": "Better-structured safety-netting handover would help nights",
            "curriculum_links": ["SLO2"],
            "key_capabilities": [
                "SLO2 KC2: Recognise and manage the unwell child",
            ],
        },
        steps=[
            Step(
                label="send-case-text",
                text=(
                    "ACCS observed assessment of febrile child with rash; consultant "
                    "watched the clerking and gave feedback on safety-netting."
                ),
                expect_button_any=("FORM|MINI_CEX", "FORM|CBD"),
            ),
            Step(
                label="tap-form-mini-cex",
                callback="FORM|MINI_CEX",
            ),
        ],
    ),
    CaseDefinition(
        case_id="haris-intermediate-acat",
        persona="Haris (Intermediate)",
        profile=HARIS_INTERMEDIATE_PROFILE,
        recommended_forms=[
            ("ACAT", "Acute take leadership across multiple patients"),
            ("CBD", "Reflective discussion on triage decisions"),
        ],
        draft_form_type="ACAT",
        draft_fields={
            "date_of_encounter": "19/3/2026",
            "clinical_setting": "ED majors / acute take",
            "patient_presentation": "Twelve-patient acute take, three category-2 escalations",
            "clinical_reasoning": "Prioritised sepsis bundle, delegated procedural tasks, ran handover",
            "reflection": "Improved early consultant ask reduced bed-block by 40 minutes",
            "curriculum_links": ["SLO3", "SLO5"],
            "key_capabilities": [
                "SLO3 KC1: Lead and manage the acute take",
            ],
        },
        steps=[
            Step(
                label="send-case-text",
                text=(
                    "Intermediate registrar — ran the acute take, twelve patients, "
                    "three escalations. Asked for consultant input early on the "
                    "septic patient."
                ),
                expect_button_any=("FORM|ACAT", "FORM|CBD"),
            ),
            Step(
                label="tap-form-acat",
                callback="FORM|ACAT",
            ),
        ],
    ),
    CaseDefinition(
        case_id="sana-sas-cbd-2021",
        persona="Sana (SAS / CESR)",
        profile=SANA_SAS_PROFILE,
        recommended_forms=[
            ("CBD_2021", "CESR-pathway reflective case discussion"),
            ("REFLECT_LOG_2021", "Reflection on departmental learning"),
        ],
        draft_form_type="CBD_2021",
        draft_fields={
            "date_of_encounter": "20/3/2026",
            "clinical_setting": "ED resus / surgical handover",
            "patient_presentation": "Adult with perforated viscus, urgent CT confirmed",
            "clinical_reasoning": "Coordinated resus, analgesia, sepsis bundle, early surgical referral",
            "reflection": "Recorded as CESR portfolio evidence; reinforced communication with theatres",
            "curriculum_links": ["CESR1"],
            "key_capabilities": [],
        },
        steps=[
            Step(
                label="send-case-text",
                text=(
                    "SAS shift in resus — adult with peritonitic abdomen, CT showed "
                    "perforation, surgical team accepted within 30 minutes."
                ),
                expect_button_any=("FORM|CBD_2021", "FORM|REFLECT_LOG_2021"),
                forbid_text_any=("DOPS", "Mini-CEX", "ACAT"),
            ),
            Step(
                label="tap-form-cbd-2021",
                callback="FORM|CBD_2021",
            ),
        ],
    ),
    CaseDefinition(
        case_id="sana-sas-qiat-audit",
        persona="Sana (SAS / CESR)",
        profile=SANA_SAS_PROFILE,
        recommended_forms=[
            ("QIAT", "QI project on triage flow"),
            ("REFLECT_LOG_2021", "Reflection on QI engagement"),
        ],
        draft_form_type="QIAT",
        draft_fields={
            "date_of_encounter": "21/3/2026",
            "clinical_setting": "Departmental QI",
            "patient_presentation": "Audit of door-to-analgesia times for limb-injury patients",
            "clinical_reasoning": "Designed change cycle, baseline + re-audit, presented at clinical governance",
            "reflection": "Median door-to-analgesia fell from 47 to 28 minutes after intervention",
            "curriculum_links": ["CESR2"],
            "key_capabilities": [],
        },
        steps=[
            Step(
                label="send-case-text",
                text=(
                    "Led a QI project as an SAS doctor — audited door-to-analgesia "
                    "times for limb injuries, ran a change cycle, re-audited."
                ),
                expect_button_any=("FORM|best", "FORM|QIAT"),
            ),
            Step(
                label="tap-form-qiat",
                callback="FORM|QIAT",
            ),
        ],
    ),
    CaseDefinition(
        case_id="sana-sas-reflect-log",
        persona="Sana (SAS / CESR)",
        profile=SANA_SAS_PROFILE,
        recommended_forms=[
            ("REFLECT_LOG_2021", "Reflection on near-miss in handover"),
            ("LAT_2021", "Leadership assessment for the same shift"),
        ],
        draft_form_type="REFLECT_LOG_2021",
        draft_fields={
            "date_of_encounter": "22/3/2026",
            "clinical_setting": "Night shift handover",
            "patient_presentation": "Near-miss: handover gap on anticoagulated head injury",
            "clinical_reasoning": "Closed loop with nursing handover sheet, escalated to clinical lead",
            "reflection": "Updated departmental handover template; presented at next M&M",
            "curriculum_links": ["CESR3"],
            "key_capabilities": [],
        },
        steps=[
            Step(
                label="send-case-text",
                text=(
                    "SAS night shift — handover gap on anticoagulated head-injury "
                    "patient. Reflected and updated departmental handover template."
                ),
                expect_button_any=("FORM|best", "FORM|REFLECT_LOG_2021", "FORM|LAT_2021"),
                forbid_text_any=("DOPS", "Mini-CEX", "ACAT"),
            ),
            Step(
                label="tap-form-reflect-log",
                callback="FORM|REFLECT_LOG_2021",
            ),
        ],
    ),
    CaseDefinition(
        case_id="haris-accs-photo-handwritten-resus",
        persona="Haris (ACCS junior)",
        profile=HARIS_ACCS_PROFILE,
        recommended_forms=[
            ("CBD", "Handwritten resus note supports a case discussion"),
            ("MINI_CEX", "Observed assessment could also fit if supervisor watched"),
        ],
        draft_form_type="CBD",
        draft_fields={
            "date_of_encounter": "23/3/2026",
            "clinical_setting": "ED resus",
            "patient_presentation": "Adult with sepsis physiology, hypotension and raised lactate",
            "clinical_reasoning": "Started sepsis six, antibiotics, fluids and early ICU discussion",
            "reflection": "Handwritten notes reminded me to document escalation timing clearly",
            "curriculum_links": ["SLO1", "SLO5"],
            "key_capabilities": [
                "SLO1 KC1: Assess and stabilise the acutely unwell patient",
            ],
        },
        steps=[
            Step(
                label="send-handwritten-photo",
                media_type="photo",
                file_name="handwritten-resus-note.jpg",
                extracted_text=(
                    "ED resus note: adult with suspected sepsis, BP 86 systolic, "
                    "lactate 4.2. Sepsis six started, broad-spectrum antibiotics, "
                    "fluids, ICU discussion. Reflection: document escalation time."
                ),
                expect_text_any=("Forms that fit", "Case-Based Discussion"),
                expect_button_any=("FORM|best", "FORM|CBD"),
            ),
            Step(
                label="tap-form-cbd",
                callback="FORM|CBD",
                expect_text_any=("Case-Based Discussion", "CBD"),
            ),
        ],
    ),
    CaseDefinition(
        case_id="haris-intermediate-voice-acute-take",
        persona="Haris (Intermediate)",
        profile=HARIS_INTERMEDIATE_PROFILE,
        recommended_forms=[
            ("ACAT", "Voice summary describes acute take leadership"),
            ("CBD", "Could be discussed as a focused clinical reasoning case"),
        ],
        draft_form_type="ACAT",
        draft_fields={
            "date_of_encounter": "24/3/2026",
            "clinical_setting": "ED acute take",
            "patient_presentation": "Busy shift with multiple category-2 patients and one septic shock escalation",
            "clinical_reasoning": "Prioritised resus review, delegated minors cover, escalated early to consultant",
            "reflection": "Voice summary highlighted the need to name leadership behaviours explicitly",
            "curriculum_links": ["SLO3", "SLO8"],
            "key_capabilities": [
                "SLO8 KC1: Lead the multidisciplinary team",
            ],
        },
        steps=[
            Step(
                label="send-voice-note",
                media_type="voice",
                file_name="acute-take-summary.ogg",
                extracted_text=(
                    "Intermediate registrar voice note. I led a busy acute take, "
                    "three category two patients, one septic shock patient in resus. "
                    "I delegated tasks, escalated early to the consultant and reflected "
                    "on team leadership."
                ),
                expect_text_any=("Forms that fit", "ACAT"),
                expect_button_any=("FORM|best", "FORM|ACAT"),
            ),
            Step(
                label="tap-form-acat",
                callback="FORM|ACAT",
            ),
        ],
    ),
    CaseDefinition(
        case_id="sana-sas-document-course-certificate",
        persona="Sana (SAS / CESR)",
        profile=SANA_SAS_PROFILE,
        recommended_forms=[
            ("SDL", "Course certificate is CPD/self-directed learning evidence"),
            ("REFLECT_LOG", "Reflection can capture how learning changed practice"),
        ],
        draft_form_type="SDL_2021",
        draft_fields={
            "date_of_encounter": "25/3/2026",
            "clinical_setting": "Regional teaching course",
            "patient_presentation": "Certificate for ultrasound-guided vascular access course",
            "clinical_reasoning": "Completed CPD activity and identified practice changes for ED procedures",
            "reflection": "Will apply probe handling and sterile technique learning in supervised practice",
            "curriculum_links": ["CESR4"],
            "key_capabilities": [],
        },
        steps=[
            Step(
                label="send-pdf-certificate",
                media_type="document",
                file_name="vascular-access-course-certificate.pdf",
                mime_type="application/pdf",
                extracted_text=(
                    "Certificate of attendance: Ultrasound-guided vascular access "
                    "course, regional EM teaching day. Learning outcomes: probe "
                    "handling, sterile technique, complication awareness."
                ),
                expect_text_any=("Forms that fit", "Self"),
                expect_button_any=("FORM|best", "FORM|SDL"),
                forbid_text_any=("DOPS", "Mini-CEX", "ACAT"),
            ),
            Step(
                label="tap-form-sdl-2021",
                callback="FORM|SDL_2021",
            ),
        ],
    ),
    CaseDefinition(
        case_id="sana-sas-mixed-photo-plus-text-complaint",
        persona="Sana (SAS / CESR)",
        profile=SANA_SAS_PROFILE,
        recommended_forms=[
            ("REFLECT_LOG_2021", "Mixed note plus text supports communication reflection"),
            ("LAT_2021", "Could evidence leadership if supervisor feedback is added"),
        ],
        draft_form_type="REFLECT_LOG_2021",
        draft_fields={
            "date_of_encounter": "26/3/2026",
            "clinical_setting": "ED majors",
            "patient_presentation": "Family complaint about waiting time during a crowded shift",
            "clinical_reasoning": "Explained clinical uncertainty, safety-netted and escalated concerns to nurse in charge",
            "reflection": "Mixed upload should merge note facts and extra typed context without splitting the case",
            "curriculum_links": ["CESR3"],
            "key_capabilities": [],
        },
        steps=[
            Step(
                label="announce-photo-coming",
                text=(
                    "I will send the image next, don't start yet. Communication case: "
                    "I spoke to the family, acknowledged the wait, explained uncertainty, "
                    "safety-netted and escalated the concern to the nurse in charge."
                ),
                expect_text_any=("wait for the images", "shared everything"),
            ),
            Step(
                label="send-handover-photo",
                media_type="photo",
                file_name="handover-note.jpg",
                extracted_text=(
                    "Handover note: crowded ED, delayed review, family upset about "
                    "waiting time, patient clinically stable after senior review."
                ),
                expect_text_any=("Finding matching forms", "Forms that fit"),
                expect_button_any=("FORM|best", "FORM|REFLECT_LOG_2021", "FORM|LAT_2021"),
                forbid_text_any=("DOPS", "Mini-CEX", "ACAT"),
            ),
            Step(
                label="tap-form-reflect-log",
                callback="FORM|REFLECT_LOG_2021",
            ),
        ],
    ),
]
