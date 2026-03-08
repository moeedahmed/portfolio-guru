# TASK.md — Portfolio Guru Quality & Credibility Hardening

## Context
Portfolio Guru generates clinical portfolio drafts from free-text case descriptions. Five improvements to increase credibility with medical users and reduce AI-looking output.

## Scope
Files affected: `backend/extractor.py` (primary), `backend/bot.py` (thin-case gate only)
No schema changes, no new dependencies, no auth/DB changes.

## What must NOT break
- Existing extraction flow (text → form recommendation → draft → approval → file)
- Voice profile injection (already working)
- Humaniser regex pipeline (already working)
- Conversation handler state machine (states, transitions)
- All form types (CBD + 14 non-CBD forms)
- Telegram message formatting (Markdown parse_mode)

## Changes

### 1. Hard grounding rule — no fabrication

**File:** `backend/extractor.py` — both `extract_cbd_data()` and `extract_form_data()` prompts

**Current:** Prompt says "Extract only what is stated or clearly implied. Do not fabricate clinical details." and "For unspecified fields, use a reasonable placeholder."

**Change:** Strengthen the grounding instruction in BOTH extraction prompts. Replace the current rules with:

```
===== GROUNDING RULES (NON-NEGOTIABLE) =====
- Extract ONLY what the doctor explicitly stated or clearly implied. Never invent clinical details.
- If a field cannot be filled from the case description, set it to "Not mentioned in case" — do NOT generate plausible-sounding content to fill gaps.
- Never add diagnoses, investigations, procedures, or clinical reasoning the doctor did not describe.
- It is better to leave a field sparse than to fabricate content. Doctors will reject inaccurate drafts.
```

Also update the JSON field comments to reinforce: change `"reflection": "what was learned from this case / learning points"` to `"reflection": "what was learned — extract from what was said, do NOT invent learning points"`.

Remove the fallback defaults that mask missing data:
- Remove: `if not data.get("trainee_role"): data["trainee_role"] = "Primary clinician"`
- Remove: `if not data.get("clinical_reasoning"): data["clinical_reasoning"] = data.get("reflection", "See reflection")`
- Replace both with: `data[field] = data.get(field) or "Not mentioned in case"`

Keep the date default (today) and "Reflection not extracted" default — those are safe.

### 2. Thin case detection — ask specific follow-up questions

**File:** `backend/extractor.py` — new function `assess_case_sufficiency()`
**File:** `backend/bot.py` — call it before extraction in `handle_case_input()`

**New function in extractor.py:**

```python
async def assess_case_sufficiency(case_description: str) -> dict:
    """Check if a case has enough detail for a quality portfolio entry.
    Returns {"sufficient": True/False, "questions": ["...", "..."]}
    """
```

Prompt the model with:
```
You are a medical portfolio assistant. A doctor has described a clinical case for their e-portfolio entry.
Assess whether the description contains enough detail to write a high-quality entry.

A sufficient case should mention most of:
- What the patient presented with
- What the doctor did (assessment, investigations, management)
- Clinical reasoning (why they made those decisions)
- What they learned or would do differently

Case description:
{case_description}

If the case has enough detail, return: {"sufficient": true, "questions": []}
If the case is too thin, return: {"sufficient": false, "questions": ["specific question 1", "specific question 2"]}

Rules:
- Ask 2-3 specific questions about what's missing — not generic "tell me more"
- Questions should target the specific gaps: missing reasoning, missing outcome, missing reflection, etc.
- Return ONLY the JSON. No explanation.
```

**In bot.py — `handle_case_input()`:**

After `case_text` is determined and before the extraction call, add:

```python
# Check case sufficiency
sufficiency = await assess_case_sufficiency(case_text)
if not sufficiency.get("sufficient", True):
    questions = sufficiency.get("questions", [])
    q_text = "\n".join(f"• {q}" for q in questions)
    await update.message.reply_text(
        f"Your case is a bit brief for a strong portfolio entry. A few questions that would help:\n\n{q_text}\n\n"
        "Send me the extra detail and I'll add it to your case, or tap below to continue with what you have.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ I'll add more", callback_data="ACTION|add_detail"),
             InlineKeyboardButton("✅ Continue anyway", callback_data="ACTION|continue_thin")]
        ])
    )
    # Store case_text so continue handler can proceed
    context.user_data["case_text"] = case_text
    context.user_data["awaiting_detail"] = True
    return AWAIT_CASE_INPUT
```

If user taps "Continue anyway" → proceed to extraction as normal.
If user sends more text → append to `case_text` and re-run sufficiency (max 1 re-check, then proceed regardless).

Add a callback handler for `ACTION|continue_thin` and `ACTION|add_detail` in the universal action handler.

### 3. Paragraph breaks in long narrative fields

**File:** `backend/extractor.py` — add to BOTH extraction prompts (CBD and generic)

Add to the `===== REFLECTION STYLE =====` section (and equivalent in `extract_form_data`):

```
===== FORMATTING =====
- Break any narrative field (reflection, clinical_reasoning, description) into 2-3 short paragraphs if it exceeds ~80 words.
- Use natural paragraph breaks: what happened → what I did/thought → what I learned or would change.
- Never write a single block of 100+ words with no paragraph break.
```

### 4. Baseline portfolio writing standard (when no voice profile)

**File:** `backend/extractor.py` — add to BOTH extraction prompts, injected only when `voice_profile_json` is empty/falsy

After the voice profile injection block, add an else clause:

```python
if voice_profile_json:
    from voice_profile import build_voice_instruction
    voice_block = build_voice_instruction(voice_profile_json)
    if voice_block:
        system_prompt += f"\n{voice_block}"
else:
    system_prompt += """

===== DEFAULT WRITING STANDARD =====
Write as an experienced UK EM trainee would write their own portfolio entry:
- First person, professional but not stiff ("I assessed" not "The patient was assessed by the trainee")
- Specific clinical language without being verbose — name the condition, the investigation, the finding
- Short, direct sentences. Vary length slightly to avoid monotony.
- Reflection should sound genuine and personal, not templated — what genuinely surprised you, challenged you, or changed your practice
- Avoid: hedging phrases ("it could be argued"), academic formality ("the aforementioned"), motivational language ("this was a fantastic learning opportunity")
- British English spelling (recognised, organised, haemorrhage, paediatric)
- Sound like a confident registrar writing after a shift, not an AI summarising a textbook
"""
```

### 5. Humaniser improvements — catch remaining AI patterns

**File:** `backend/extractor.py` — add to `SLOP_PATTERNS` list

Add these commonly missed patterns:

```python
# Additional patterns (catch remaining AI-tells)
r"\bensur(?:e[sd]?|ing)\b",           # "ensuring" — overused filler
r"\benhance[sd]?\b",                   # "enhanced my understanding"
r"\bultimately\b",                     # "ultimately this case..."
r"\bsignificant(?:ly)?\b",            # "significantly improved"
r"\bnotably\b",                        # "notably, the patient..."
r"\bthis case (?:served as|was) a (?:valuable|important|key)\b",  # templated reflection openers
r"\breinforced (?:the importance|my understanding)\b",             # AI reflection cliché
r"\bhighlighted the (?:importance|need|value)\b",                  # AI reflection cliché
```

## Regression checklist (verify after changes)
1. Submit a detailed case (3+ sentences) → should produce a full draft as before
2. Submit a very thin case ("I saw a patient with chest pain") → should trigger sufficiency questions
3. Submit a case with NO reflection mentioned → the draft reflection should say "Not mentioned in case", not an invented reflection
4. Check that voice profile users still get personalised output
5. Check that long reflections are paragraphed (not one wall of text)
6. Verify humaniser still strips em dashes, "delve", "navigate" etc. — existing patterns must not regress
7. Check all form types still work (test CBD + one non-CBD like DOPS or LAT)
8. Verify the conversation state machine: thin-case flow → "Continue anyway" → should proceed to form selection normally

## Implementation order
1. Grounding rules (prompt change only — lowest risk)
2. Paragraph formatting instruction (prompt change only)
3. Default writing standard (prompt change only, conditional)
4. Humaniser pattern additions (regex only)
5. Thin case detection (new function + bot flow change — highest risk, do last)

## Change classification: STANDARD
- No auth/schema/state changes
- Mostly prompt modifications (low blast radius)
- One new conversation flow branch (thin case) — moderate risk, isolated to one handler
