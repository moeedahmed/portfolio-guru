"""
Ground-truth Kaizen form schemas.
Source: Medic's KAIZEN-FORMS-VERIFICATION-REPORT.md (verified Feb 2026 against live kaizenep.com)
Each schema defines: fields, their types, required status, and dropdown options where applicable.
"""

FORM_SCHEMAS = {

    "CBD": {
        "name": "Case-Based Discussion",
        "filer_available": True,
        "fields": [
            {"key": "date_of_encounter",    "label": "Date",                    "type": "date",     "required": True},
            {"key": "clinical_setting",     "label": "Clinical Setting",        "type": "dropdown", "required": True,
             "options": ["Emergency Department", "Acute Medical Ward", "Paediatric Emergency Department",
                         "Intensive Care Unit", "Emergency Department Observation Unit", "Minor Injury Unit", "Other"]},
            {"key": "patient_presentation", "label": "Patient Presentation",    "type": "text",     "required": True},
            {"key": "stage_of_training",    "label": "Stage of Training",       "type": "dropdown", "required": True,
             "options": ["Intermediate/ST3", "Higher/ST4-ST6", "PEM Sub-specialty", "ACCS ST1-ST2/CT1-CT2"]},
            {"key": "trainee_role",         "label": "Trainee Role",            "type": "text",     "required": True},
            {"key": "clinical_reasoning",   "label": "Case to be discussed",    "type": "text",     "required": True},
            {"key": "reflection",           "label": "Reflection of event",     "type": "text",     "required": True},
            {"key": "level_of_supervision", "label": "Level of Supervision",    "type": "dropdown", "required": True,
             "options": ["Direct", "Indirect", "Distant"]},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)", "type": "kc_tick",  "required": False},
            {"key": "key_capabilities",     "label": "Key Capabilities",        "type": "kc_tick",  "required": False},
        ]
    },

    "DOPS": {
        "name": "Direct Observation of Procedural Skills",
        "filer_available": False,
        "fields": [
            {"key": "date_of_encounter",    "label": "Date",                    "type": "date",     "required": True},
            {"key": "procedure_name",       "label": "Procedure",               "type": "dropdown", "required": True,
             "options": ["Paediatric sedation", "Adult sedation", "Advanced airway management",
                         "Non-invasive ventilation", "Open Chest drain", "Resuscitative thoracotomy",
                         "Lateral Canthotomy", "DC cardioversion", "External pacing", "Pericardiocentesis",
                         "ED management of life-threatening haemorrhage", "Emergency delivery",
                         "Resuscitative hysterotomy", "Fracture / Dislocation manipulation",
                         "Large joint aspiration", "PoCUS - Echo in Life Support (ELS)",
                         "PoCUS - Shock Assessment", "PoCUS - Focused Assessment for AAA",
                         "PoCUS - eFAST / FAFF", "Other"]},
            {"key": "clinical_setting",     "label": "Clinical Setting",        "type": "text",     "required": True},
            {"key": "stage_of_training",    "label": "Stage of Training",       "type": "dropdown", "required": True,
             "options": ["Intermediate/ST3", "Higher/ST4-ST6", "PEM Sub-specialty", "ACCS ST1-ST2/CT1-CT2"]},
            {"key": "indication",           "label": "Indication",              "type": "text",     "required": True},
            {"key": "trainee_performance",  "label": "Trainee Performance",     "type": "text",     "required": True},
            {"key": "reflection",           "label": "Reflection",              "type": "text",     "required": False},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)", "type": "kc_tick",  "required": False},
            {"key": "key_capabilities",     "label": "Key Capabilities",        "type": "kc_tick",  "required": False},
        ]
    },

    "MINI_CEX": {
        "name": "Mini-Clinical Evaluation Exercise",
        "filer_available": False,
        "fields": [
            {"key": "date_of_encounter",    "label": "Date",                    "type": "date",     "required": True},
            {"key": "clinical_setting",     "label": "Clinical Setting",        "type": "dropdown", "required": True,
             "options": ["Emergency Department", "Acute Medical Ward", "Paediatric Emergency Department",
                         "Intensive Care Unit", "Emergency Department Observation Unit", "Minor Injury Unit", "Other"]},
            {"key": "patient_presentation", "label": "Patient Presentation",    "type": "text",     "required": True},
            {"key": "stage_of_training",    "label": "Stage of Training",       "type": "dropdown", "required": True,
             "options": ["Intermediate/ST3", "Higher/ST4-ST6", "PEM Sub-specialty", "ACCS ST1-ST2/CT1-CT2"]},
            {"key": "complexity",           "label": "Case Complexity",         "type": "dropdown", "required": False,
             "options": ["Low", "Medium", "High"]},
            {"key": "clinical_reasoning",   "label": "Clinical Assessment",     "type": "text",     "required": True},
            {"key": "reflection",           "label": "Reflection",              "type": "text",     "required": True},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)", "type": "kc_tick",  "required": False},
            {"key": "key_capabilities",     "label": "Key Capabilities",        "type": "kc_tick",  "required": False},
        ]
    },

    "ACAT": {
        "name": "Acute Care Assessment Tool",
        "filer_available": False,
        "fields": [
            {"key": "date_of_encounter",    "label": "Date of event",           "type": "date",     "required": True},
            {"key": "placement",            "label": "Placement",               "type": "dropdown", "required": True,
             "options": ["Emergency Medicine", "Anaesthetics", "Critical Care", "Internal Medicine",
                         "Paediatric", "PEM Sub Spec - Paediatric Emergency",
                         "PEM Sub Spec - General Paediatrics", "PEM Sub Spec - Paediatric Critical Care"]},
            {"key": "clinical_setting",     "label": "Clinical Setting",        "type": "dropdown", "required": True,
             "options": ["Acute Medical Ward", "Emergency Department", "Paediatric Emergency Department",
                         "Intensive Care Unit", "Emergency Department Observation Unit", "Minor Injury Unit", "Other"]},
            {"key": "cases_observed",       "label": "Cases Observed",          "type": "text",     "required": True},
            {"key": "reflection",           "label": "Reflection of event",     "type": "text",     "required": True},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)", "type": "kc_tick",  "required": False},
            {"key": "key_capabilities",     "label": "Key Capabilities",        "type": "kc_tick",  "required": False},
        ]
    },

    "LAT": {
        "name": "Leadership Assessment Tool",
        "filer_available": False,
        "fields": [
            {"key": "date_of_encounter",    "label": "Date",                    "type": "date",     "required": True},
            {"key": "clinical_setting",     "label": "Clinical Setting",        "type": "dropdown", "required": True,
             "options": ["Emergency Department", "Acute Medical Ward", "Paediatric Emergency Department",
                         "Intensive Care Unit", "Emergency Department Observation Unit", "Minor Injury Unit", "Other"]},
            {"key": "leadership_context",   "label": "Leadership Context",      "type": "text",     "required": True},
            {"key": "stage_of_training",    "label": "Stage of Training",       "type": "dropdown", "required": True,
             "options": ["Intermediate/ST3", "Higher/ST4-ST6", "PEM Sub-specialty", "ACCS ST1-ST2/CT1-CT2"]},
            {"key": "clinical_reasoning",   "label": "Description of activity", "type": "text",     "required": True},
            {"key": "reflection",           "label": "Reflection",              "type": "text",     "required": True},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)", "type": "kc_tick",  "required": False},
            {"key": "key_capabilities",     "label": "Key Capabilities",        "type": "kc_tick",  "required": False},
        ]
    },

    "ACAF": {
        "name": "Applied Critical Appraisal Form",
        "filer_available": False,
        "fields": [
            {"key": "date_of_encounter",    "label": "Date",                                "type": "date",  "required": True},
            {"key": "situation",            "label": "Section 1: Situation",                "type": "text",  "required": True},
            {"key": "pico_population",      "label": "Population or Problem",              "type": "text",  "required": False},
            {"key": "pico_intervention",    "label": "Intervention",                       "type": "text",  "required": False},
            {"key": "pico_comparison",      "label": "Comparison",                         "type": "text",  "required": False},
            {"key": "pico_outcome",         "label": "Outcome",                            "type": "text",  "required": False},
            {"key": "search_methodology",   "label": "Search Methodology",                 "type": "text",  "required": False},
            {"key": "evidence_evaluation",  "label": "Evaluate current evidence",          "type": "text",  "required": False},
            {"key": "apply_to_practice",    "label": "Apply the evidence to practice",     "type": "text",  "required": False},
            {"key": "communicate_to_patient","label": "Communicate findings to patient",   "type": "text",  "required": False},
            {"key": "future_research",      "label": "Recommend future research ideas",    "type": "text",  "required": False},
            {"key": "reflection",           "label": "Comment (Reflection)",               "type": "text",  "required": False},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)",            "type": "kc_tick","required": False},
        ]
    },

    "STAT": {
        "name": "Structured Teaching Assessment Tool",
        "filer_available": False,
        "fields": [
            {"key": "date_of_encounter",    "label": "Date",                    "type": "date",     "required": True},
            {"key": "stage_of_training",    "label": "Stage of Training",       "type": "dropdown", "required": True,
             "options": ["Intermediate/ST3", "Higher/ST4-ST6", "PEM Sub-specialty", "ACCS ST1-ST2/CT1-CT2"]},
            {"key": "learner_group",        "label": "Learner Group",           "type": "text",     "required": False},
            {"key": "setting",              "label": "Setting",                 "type": "dropdown", "required": False,
             "options": ["- n/a -", "Local", "Regional", "National", "Other"]},
            {"key": "delivery",             "label": "Delivery",                "type": "dropdown", "required": False,
             "options": ["- n/a -", "Face to Face", "Digital", "Other"]},
            {"key": "number_of_learners",   "label": "Number of Learners",      "type": "dropdown", "required": False,
             "options": ["- n/a -", "Less than 5", "5-15", "16-30", "More than 30"]},
            {"key": "session_length",       "label": "Length of Session",       "type": "text",     "required": False},
            {"key": "session_title",        "label": "Title of Teaching Session","type": "text",    "required": True},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)", "type": "kc_tick",  "required": False},
        ]
    },

    "MSF": {
        "name": "Multi-Source Feedback",
        "filer_available": False,
        "fields": [
            {"key": "date_of_encounter",    "label": "Date",                    "type": "date",     "required": True},
            {"key": "stage_of_training",    "label": "Stage of Training",       "type": "dropdown", "required": True,
             "options": ["Intermediate/ST3", "Higher/ST4-ST6", "PEM Sub-specialty", "ACCS ST1-ST2/CT1-CT2"]},
            {"key": "context",              "label": "Context / Description",   "type": "text",     "required": False},
            {"key": "reflection",           "label": "Reflection on feedback",  "type": "text",     "required": False},
        ]
    },

    "QIAT": {
        "name": "Quality Improvement Assessment Tool",
        "filer_available": False,
        "fields": [
            {"key": "date_of_encounter",    "label": "Date of completion",          "type": "date",     "required": True},
            {"key": "stage_of_training",    "label": "Stage of Training",           "type": "dropdown", "required": True,
             "options": ["ST1/CT1", "ST2/CT2", "ST3/CT3", "ST4", "ST5", "ST6", "ST7",
                         "OOP", "Non-training", "Portfolio pathway (CESR)", "Other"]},
            {"key": "placement",            "label": "Placement",                   "type": "text",     "required": True},
            {"key": "pdp_summary",          "label": "QI PDP summary for this year","type": "text",     "required": True},
            {"key": "qi_engagement",        "label": "QI education engagement",     "type": "text",     "required": True},
            {"key": "qi_understanding",     "label": "How developed QI understanding","type": "text",   "required": False},
            {"key": "involved_in_project",  "label": "Involved in QI project?",     "type": "dropdown", "required": True,
             "options": ["Yes", "No"]},
            {"key": "qi_journey_aspects",   "label": "QI Journey aspects",          "type": "multi_select","required": True,
             "options": ["Creating Conditions", "Understanding Systems", "Developing Aims",
                         "Testing Changes", "Implement", "Spread", "Leadership & Teams",
                         "Project Management & Communication", "Measurement"]},
            {"key": "reflection",           "label": "Reflections and Learning",    "type": "text",     "required": True},
            {"key": "next_pdp",             "label": "Next Year's PDP",             "type": "text",     "required": True},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)",     "type": "kc_tick",  "required": False},
        ]
    },

    "JCF": {
        "name": "Journal Club / Presentation Form",
        "filer_available": False,
        "fields": [
            {"key": "date_of_encounter",    "label": "Date",                    "type": "date",     "required": True},
            {"key": "learner_group",        "label": "Learner Group",           "type": "text",     "required": False},
            {"key": "setting",              "label": "Setting",                 "type": "dropdown", "required": False,
             "options": ["- n/a -", "Local", "Regional", "National", "Other"]},
            {"key": "delivery",             "label": "Delivery",                "type": "dropdown", "required": False,
             "options": ["- n/a -", "Face to Face", "Digital", "Other"]},
            {"key": "number_of_learners",   "label": "Number of Learners",      "type": "dropdown", "required": False,
             "options": ["- n/a -", "Less than 5", "5-15", "16-30", "More than 30"]},
            {"key": "session_length",       "label": "Length of Session",       "type": "text",     "required": False},
            {"key": "paper_title",          "label": "Title of Paper",          "type": "text",     "required": True},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)", "type": "kc_tick",  "required": False},
        ]
    },

    # ===== NEW FORMS (9 added) =====

    "TEACH": {
        "name": "Teaching Delivered By Trainee",
        "filer_available": False,
        "fields": [
            {"key": "date_of_teaching",     "label": "Date of teaching activity",   "type": "date",     "required": True},
            {"key": "title_of_session",     "label": "Title of session",            "type": "text",     "required": True},
            {"key": "recognised_courses",   "label": "Recognised Courses",          "type": "dropdown", "required": False,
             "options": ["- n/a -", "ATLS", "APLS", "ALS", "ELS", "Other"]},
            {"key": "learning_outcomes",    "label": "Learning outcomes used in session", "type": "text", "required": True},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)",     "type": "kc_tick",  "required": False},
        ]
    },

    "PROC_LOG": {
        "name": "Procedural Log ST3-ST6",
        "filer_available": False,
        "fields": [
            {"key": "date_of_activity",     "label": "Date of Activity",            "type": "date",     "required": True},
            {"key": "stage_of_training",    "label": "Stage of Training",           "type": "dropdown", "required": True,
             "options": ["Intermediate/ST3", "Higher/ST4-ST6", "PEM Sub-specialty", "ACCS ST1-ST2/CT1-CT2"]},
            {"key": "year_of_training",     "label": "Year of training",            "type": "text",     "required": False},
            {"key": "age_of_patient",       "label": "Age of patient",              "type": "text",     "required": False},
            {"key": "reflective_comments",  "label": "Reflective comments on procedure", "type": "text", "required": True},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)",     "type": "kc_tick",  "required": False},
        ]
    },

    "SDL": {
        "name": "Self-directed Learning Reflection",
        "filer_available": False,
        "fields": [
            {"key": "reflection_title",     "label": "Reflection Title",            "type": "text",     "required": True},
            {"key": "learning_activity_type","label": "Learning Activity Type",     "type": "multi_select", "required": True,
             "options": ["RCEMlearning Module (Exam & CPD)", "RCEMlearning Reference", "e-Learning for Healthcare",
                         "Podcast/Broadcast/Video", "RCEMFOAMed Podcast/Blog", "Blog/Article/Journal/Magazine", "Other"]},
            {"key": "resource_details",     "label": "Please specify details of the learning resource", "type": "text", "required": True},
            {"key": "reflection",           "label": "Reflection",                  "type": "text",     "required": False},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)",     "type": "kc_tick",  "required": False},
        ]
    },

    "US_CASE": {
        "name": "Ultrasound Case Reflection",
        "filer_available": False,
        "fields": [
            {"key": "case_reflection_title","label": "Case reflection title",       "type": "text",     "required": True},
            {"key": "date_of_case",         "label": "Date of case",                "type": "date",     "required": True},
            {"key": "location",             "label": "Location",                    "type": "text",     "required": False},
            {"key": "patient_gender",       "label": "Patient Gender",              "type": "dropdown", "required": False,
             "options": ["- n/a -", "Male", "Female", "Other"]},
            {"key": "patient_age",          "label": "Patient's Age",               "type": "text",     "required": False},
            {"key": "equipment_used",       "label": "Equipment Used",              "type": "text",     "required": False},
            {"key": "us_application",       "label": "Ultrasound Application",      "type": "multi_select", "required": False,
             "options": ["AAA", "ELS", "FAST", "Vascular Access", "Other"]},
            {"key": "clinical_scenario",    "label": "Describe the clinical scenario", "type": "text", "required": False},
            {"key": "how_used",             "label": "How was ultrasound used in this case?", "type": "text", "required": False},
            {"key": "usable_images",        "label": "Were you able to obtain usable images?", "type": "text", "required": False},
            {"key": "interpret_images",     "label": "Were you able to interpret the images?", "type": "text", "required": False},
            {"key": "changed_management",   "label": "Did the use of ultrasound change management of the patient?", "type": "text", "required": False},
            {"key": "learning_points",      "label": "What did you learn from this case?", "type": "text", "required": False},
            {"key": "other_comments",       "label": "Other comments",              "type": "text",     "required": False},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)",     "type": "kc_tick",  "required": False},
        ]
    },

    "ESLE": {
        "name": "Reflection on ESLE",
        "filer_available": False,
        "fields": [
            {"key": "reflection_title",     "label": "Title of reflection",         "type": "text",     "required": True},
            {"key": "date_of_esle",         "label": "Date of ESLE",                "type": "date",     "required": False},
            {"key": "esle_category",        "label": "ESLE Category",               "type": "multi_select", "required": True,
             "options": ["Management & Supervision", "Teamwork & Cooperation", "Decision making", "Situational Awareness"]},
            {"key": "circumstances",        "label": "Describe the circumstances. What did you do? What did others do?", "type": "text", "required": False},
            {"key": "replay_differently",   "label": "If you could replay the event, what would you have done differently?", "type": "text", "required": False},
            {"key": "why",                  "label": "Why?",                        "type": "text",     "required": False},
            {"key": "different_outcome",    "label": "How would the outcome be different if you replayed this event?", "type": "text", "required": False},
            {"key": "focussing_on",         "label": "Focussing on what you would have done differently...", "type": "text", "required": False},
            {"key": "learned",              "label": "What have you learned from the experience?", "type": "text", "required": False},
            {"key": "further_action",       "label": "Further action required",     "type": "text",     "required": False},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)",     "type": "kc_tick",  "required": False},
        ]
    },

    "COMPLAINT": {
        "name": "Reflection on Complaints",
        "filer_available": False,
        "fields": [
            {"key": "reflection_title",     "label": "Title of reflection",         "type": "text",     "required": True},
            {"key": "date_of_complaint",    "label": "Date of complaint",           "type": "date",     "required": True},
            {"key": "key_features",         "label": "Key features of complaint",   "type": "text",     "required": True},
            {"key": "key_aspects",          "label": "Key aspects of case and care given by trainee", "type": "text", "required": True},
            {"key": "learning_points",      "label": "What are the learning points from this case?", "type": "text", "required": True},
            {"key": "further_action",       "label": "Further action required",     "type": "text",     "required": True},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)",     "type": "kc_tick",  "required": False},
        ]
    },

    "SERIOUS_INC": {
        "name": "Reflection on Serious Incident",
        "filer_available": False,
        "fields": [
            {"key": "reflection_title",     "label": "Title of reflection",         "type": "text",     "required": True},
            {"key": "date_of_incident",     "label": "Date of incident",            "type": "date",     "required": True},
            {"key": "description",          "label": "Description of case including adverse events", "type": "text", "required": True},
            {"key": "root_causes",          "label": "Root causes of events",       "type": "text",     "required": True},
            {"key": "contributing_factors", "label": "Contributing factors",        "type": "text",     "required": True},
            {"key": "learning_points",      "label": "What are the learning points from this case?", "type": "text", "required": True},
            {"key": "further_action",       "label": "Further action required",     "type": "text",     "required": True},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)",     "type": "kc_tick",  "required": False},
        ]
    },

    "EDU_ACT": {
        "name": "Educational Activity Attended",
        "filer_available": False,
        "fields": [
            {"key": "date_of_education",    "label": "Date of education",           "type": "date",     "required": True},
            {"key": "title_of_education",   "label": "Title of education",          "type": "text",     "required": True},
            {"key": "delivered_by",         "label": "Who delivered the education", "type": "text",     "required": False},
            {"key": "learning_points",      "label": "Main learning points",        "type": "text",     "required": False},
            {"key": "curriculum_section",   "label": "Section of Curriculum covered in the teaching", "type": "text", "required": False},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)",     "type": "kc_tick",  "required": False},
        ]
    },

    "FORMAL_COURSE": {
        "name": "Attendance at Formal Course",
        "filer_available": False,
        "fields": [
            {"key": "stage_of_training",    "label": "Stage of Training",           "type": "dropdown", "required": True,
             "options": ["Intermediate/ST3", "Higher/ST4-ST6", "PEM Sub-specialty", "ACCS ST1-ST2/CT1-CT2"]},
            {"key": "project_description",  "label": "Project Description",         "type": "text",     "required": True},
            {"key": "reflective_notes",     "label": "Reflective notes from experience", "type": "text", "required": True},
            {"key": "resources_used",       "label": "Resources Used",              "type": "text",     "required": True},
            {"key": "lessons_learned",      "label": "Lessons learned",             "type": "text",     "required": True},
            {"key": "curriculum_links",     "label": "Curriculum Links (SLOs)",     "type": "kc_tick",  "required": False},
        ]
    },
}
