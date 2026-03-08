# TASK.md — Portfolio Guru: Add 9 New Kaizen Forms

## Session to resume
`f1d5979e-201c-4c8a-84ef-0bc511f7b31d`

## Objective
Add 9 new Kaizen form types to Portfolio Guru. Each form needs:
1. A UUID in `FORM_UUIDS` (backend/extractor.py)
2. A schema in `FORM_SCHEMAS` (backend/form_schemas.py)
3. A short code key in `TRAINING_LEVEL_FORMS` (backend/bot.py)
4. An emoji in `FORM_EMOJIS` (backend/bot.py)
5. An extraction prompt in `extract_form_data()` (backend/extractor.py)

## Step 0 — Discover UUIDs (REQUIRED FIRST)
Run `backend/discover_uuids.py` to scrape Kaizen and get the UUIDs for the 9 new forms.
The script logs into Kaizen and lists all form URLs. Find UUIDs for:
- Teaching Delivered By Trainee (2025 Update)
- Procedural Log ST3-ST6 (2025 Update)
- Self-directed Learning Reflection (2025 Update)
- Ultrasound Case Reflection (2025 Update)
- Reflection on ESLE (2025 Update)
- Reflection on Complaints (2025 Update)
- Reflection on Serious Incident (2025 Update)
- Educational Activity Attended (2025 Update)
- Attendance at Formal Course (2025 Update)

Add them to `FORM_UUIDS` with these short codes:
- `"TEACH"` — Teaching Delivered By Trainee
- `"PROC_LOG"` — Procedural Log ST3-ST6
- `"SDL"` — Self-directed Learning Reflection
- `"US_CASE"` — Ultrasound Case Reflection
- `"ESLE"` — Reflection on ESLE
- `"COMPLAINT"` — Reflection on Complaints
- `"SERIOUS_INC"` — Reflection on Serious Incident
- `"EDU_ACT"` — Educational Activity Attended
- `"FORMAL_COURSE"` — Attendance at Formal Course

## Step 1 — Add schemas to form_schemas.py

Add each form to `FORM_SCHEMAS`. Source of truth: Medic's verified report.
All schemas follow this pattern (see existing entries for reference):

```python
"SHORT_CODE": {
    "name": "Full Form Name",
    "filer_available": False,  # no filer built yet — save draft locally
    "fields": [
        {"key": "...", "label": "...", "type": "...", "required": True/False},
        ...
    ]
}
```

### Field types used
- `"date"` — date picker
- `"text"` — free text
- `"dropdown"` — single select, include `"options": [...]`
- `"multi_select"` — checkboxes, include `"options": [...]`
- `"kc_tick"` — curriculum alignment (SLO/KC ticking) — always key `"curriculum_links"`
- `"file_upload"` — skip in extraction (not supported)

### TEACH — Teaching Delivered By Trainee
Fields (from verified Kaizen form):
- `date_of_teaching` (date, required) — "Date of teaching activity"
- `title_of_session` (text, required) — "Title of session"
- `recognised_courses` (dropdown) — options: ["- n/a -", "ATLS", "APLS", "ALS", "ELS", "Other"]
- `learning_outcomes` (text, required) — "Learning outcomes used in session"
- `curriculum_links` (kc_tick)

### PROC_LOG — Procedural Log ST3-ST6
Fields:
- `date_of_activity` (date, required) — "Date of Activity"
- `stage_of_training` (dropdown, required) — options: ["Intermediate/ST3", "Higher/ST4-ST6", "PEM Sub-specialty", "ACCS ST1-ST2/CT1-CT2"]
- `year_of_training` (text) — "Year of training"
- `age_of_patient` (text) — "Age of patient"
- `reflective_comments` (text, required) — "Reflective comments on procedure"
- `curriculum_links` (kc_tick)

### SDL — Self-directed Learning Reflection
Fields:
- `reflection_title` (text, required) — "Reflection Title"
- `learning_activity_type` (multi_select, required) — options: ["RCEMlearning Module (Exam & CPD)", "RCEMlearning Reference", "e-Learning for Healthcare", "Podcast/Broadcast/Video", "RCEMFOAMed Podcast/Blog", "Blog/Article/Journal/Magazine", "Other"]
- `resource_details` (text, required) — "Please specify details of the learning resource"
- `reflection` (text) — "Reflection"
- `curriculum_links` (kc_tick)

### US_CASE — Ultrasound Case Reflection
Fields:
- `case_reflection_title` (text, required) — "Case reflection title"
- `date_of_case` (date, required) — "Date of case"
- `location` (text) — "Location"
- `patient_gender` (dropdown) — options: ["- n/a -", "Male", "Female", "Other"]
- `patient_age` (text) — "Patient's Age"
- `equipment_used` (text) — "Equipment Used"
- `us_application` (multi_select) — options: ["AAA", "ELS", "FAST", "Vascular Access", "Other"]
- `clinical_scenario` (text) — "Describe the clinical scenario"
- `how_used` (text) — "How was ultrasound used in this case?"
- `usable_images` (text) — "Were you able to obtain usable images?"
- `interpret_images` (text) — "Were you able to interpret the images?"
- `changed_management` (text) — "Did the use of ultrasound change management of the patient?"
- `learning_points` (text) — "What did you learn from this case?"
- `other_comments` (text) — "Other comments"
- `curriculum_links` (kc_tick)

### ESLE — Reflection on ESLE
Fields:
- `reflection_title` (text, required) — "Title of reflection"
- `date_of_esle` (date) — "Date of ESLE"
- `esle_category` (multi_select, required) — options: ["Management & Supervision", "Teamwork & Cooperation", "Decision making", "Situational Awareness"]
- `circumstances` (text) — "Describe the circumstances. What did you do? What did others do?"
- `replay_differently` (text) — "If you could replay the event, what would you have done differently?"
- `why` (text) — "Why?"
- `different_outcome` (text) — "How would the outcome be different if you replayed this event?"
- `focussing_on` (text) — "Focussing on what you would have done differently..."
- `learned` (text) — "What have you learned from the experience?"
- `further_action` (text) — "Further action required"
- `curriculum_links` (kc_tick)

### COMPLAINT — Reflection on Complaints
Fields:
- `reflection_title` (text, required) — "Title of reflection"
- `date_of_complaint` (date, required) — "Date of complaint"
- `key_features` (text, required) — "Key features of complaint"
- `key_aspects` (text, required) — "Key aspects of case and care given by trainee"
- `learning_points` (text, required) — "What are the learning points from this case?"
- `further_action` (text, required) — "Further action required"
- `curriculum_links` (kc_tick)

### SERIOUS_INC — Reflection on Serious Incident
Fields:
- `reflection_title` (text, required) — "Title of reflection"
- `date_of_incident` (date, required) — "Date of incident"
- `description` (text, required) — "Description of case including adverse events"
- `root_causes` (text, required) — "Root causes of events" (prompt: patient, illness, team, task, environment, culture, organisation)
- `contributing_factors` (text, required) — "Contributing factors" (prompt: distractions, equipment, task overload, help)
- `learning_points` (text, required) — "What are the learning points from this case?"
- `further_action` (text, required) — "Further action required"
- `curriculum_links` (kc_tick)

### EDU_ACT — Educational Activity Attended
Fields:
- `date_of_education` (date, required) — "Date of education"
- `title_of_education` (text, required) — "Title of education"
- `delivered_by` (text) — "Who delivered the education"
- `learning_points` (text) — "Main learning points"
- `curriculum_section` (text) — "Section of Curriculum covered in the teaching"
- `curriculum_links` (kc_tick)

### FORMAL_COURSE — Attendance at Formal Course
Fields:
- `stage_of_training` (dropdown, required) — options: ["Intermediate/ST3", "Higher/ST4-ST6", "PEM Sub-specialty", "ACCS ST1-ST2/CT1-CT2"]
- `project_description` (text, required) — "Project Description"
- `reflective_notes` (text, required) — "Reflective notes from experience"
- `resources_used` (text, required) — "Resources Used"
- `lessons_learned` (text, required) — "Lessons learned"
- `curriculum_links` (kc_tick)

## Step 2 — Add to TRAINING_LEVEL_FORMS (backend/bot.py)

Update the `TRAINING_LEVEL_FORMS` dict to include the new form codes at appropriate levels:

```python
TRAINING_LEVEL_FORMS = {
    "ST3":  ["CBD", "DOPS", "MINI_CEX", "ACAT", "MSF", "PROC_LOG", "SDL", "EDU_ACT", "FORMAL_COURSE", "TEACH", "COMPLAINT", "SERIOUS_INC", "ESLE"],
    "ST4":  ["CBD", "DOPS", "MINI_CEX", "ACAT", "MSF", "LAT", "ACAF", "QIAT", "PROC_LOG", "SDL", "EDU_ACT", "FORMAL_COURSE", "TEACH", "US_CASE", "COMPLAINT", "SERIOUS_INC", "ESLE"],
    "ST5":  ["CBD", "DOPS", "MINI_CEX", "ACAT", "MSF", "LAT", "ACAF", "QIAT", "STAT", "JCF", "PROC_LOG", "SDL", "EDU_ACT", "FORMAL_COURSE", "TEACH", "US_CASE", "COMPLAINT", "SERIOUS_INC", "ESLE"],
    "ST6":  ["CBD", "DOPS", "MINI_CEX", "ACAT", "MSF", "LAT", "ACAF", "QIAT", "STAT", "JCF", "PROC_LOG", "SDL", "EDU_ACT", "FORMAL_COURSE", "TEACH", "US_CASE", "COMPLAINT", "SERIOUS_INC", "ESLE"],
    "SAS":  ["CBD", "DOPS", "MINI_CEX", "ACAT", "MSF", "LAT", "ACAF", "QIAT", "STAT", "JCF", "PROC_LOG", "SDL", "EDU_ACT", "FORMAL_COURSE", "TEACH", "US_CASE", "COMPLAINT", "SERIOUS_INC", "ESLE"],
}
```

## Step 3 — Add emojis to FORM_EMOJIS (backend/bot.py)

Add to the `FORM_EMOJIS` dict:
```python
"TEACH":        "👨‍🏫",
"PROC_LOG":     "🔬",
"SDL":          "📖",
"US_CASE":      "🔊",
"ESLE":         "⚠️",
"COMPLAINT":    "📝",
"SERIOUS_INC":  "🚨",
"EDU_ACT":      "🎓",
"FORMAL_COURSE":"📋",
```

## Step 4 — Add extraction prompts to extractor.py

In `extract_form_data()`, add cases for each new form type inside the existing if/elif chain.
Each prompt should:
1. Tell Gemini what the form is for
2. List the fields to extract with their keys
3. Instruct it to use British English
4. Instruct it to write professionally but naturally
5. Tell it NOT to invent clinical details not present in the case text

Use the existing CBD/DOPS/LAT prompts as style reference.

Key instruction for all reflection forms:
```
This is a self-reflection form. The trainee is reflecting on their own experience.
Write in first person ("I managed...", "I reflected on...").
Do not invent clinical details. Base everything on the case description provided.
```

## Step 5 — Test locally

After all forms added:
1. Run `python3 -c "from form_schemas import FORM_SCHEMAS; print(list(FORM_SCHEMAS.keys()))"` — should show all 19 forms
2. Run `python3 -c "from extractor import FORM_UUIDS; print(list(FORM_UUIDS.keys()))"` — should show all 19 UUIDs
3. Run `python3 -c "import bot; print('OK')"` — should import cleanly

## Do NOT touch
- `filer.py` — filing logic, frozen
- `store.py` — credential storage, frozen
- `credentials.py` — credential model, frozen
- `main.py` — entry point, frozen
- `run_local.sh` — startup script, frozen
- Any existing form schemas already in `FORM_SCHEMAS`

## Commit when done
```
feat: add 9 new Kaizen forms (TEACH, PROC_LOG, SDL, US_CASE, ESLE, COMPLAINT, SERIOUS_INC, EDU_ACT, FORMAL_COURSE)
```
