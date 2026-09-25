# RCEM AI reflective-log compliance

Status: implemented locally; release verification and deployment are separate gates.

Policy source: *RCEM Position on the use of AI in Reflective Logs*, September 2025.

## Product boundary

Portfolio Guru may structure, edit, clarify, or prompt a resident doctor's own
reflection. It must not originate the reflective act, fabricate an experience,
or let AI-written reflection be saved without the doctor's critical input. The
doctor reviews the complete draft and remains responsible for its accuracy,
authenticity, and insight. Portfolio Guru continues to save Kaizen drafts only;
it never submits or signs them.

## Control mapping

| RCEM requirement | Portfolio Guru control | Evidence |
| --- | --- | --- |
| AI may structure and edit a doctor's reflection | Drafting and Quick Improve remain available after personal reflective input is detected | `backend/rcem_ai_policy.py`; approval flow in `backend/bot.py` |
| AI must not replace authentic reflection | A deterministic source gate blocks save and Quick Improve until the doctor's words include learning, interpretation, reaction, or an intended change | `has_personal_reflective_input`; `_draft_needs_reflection_detail_before_save` |
| Do not fabricate experiences or reflections | Existing extraction prompts prohibit invented facts; the new gate does not ask a model to judge its own authenticity | `backend/extractor.py`; `backend/rcem_ai_policy.py` |
| Declare AI use within the log | Before filing, one populated reflection field receives: "AI was used to help structure and edit this reflection." | `with_ai_use_declaration`; `_with_rcem_ai_declaration` |
| Resident remains accountable | Every reflective-draft preview states that the doctor remains responsible for accuracy, authenticity, and insight | `_draft_transparency_layer` |
| Human critical input before submission | Save is hidden and guarded until personal reflection is present; the full draft still needs explicit approval and is saved as a Kaizen draft only | approval flow in `backend/bot.py` |

## Compatibility boundary

New drafts retain their source and are always evaluated. An already-persisted
draft from before this control may lack its original source text; it keeps the
existing explicit review-and-save path rather than being made unusable. The AI
declaration is still added when that legacy draft is filed.

## Verification gate

Run `bash scripts/verify_changed.sh`. Focused policy tests live in
`backend/tests/test_rcem_ai_policy.py`. Before release, run
`bash scripts/verify_release.sh` and the existing risk-scaled Telegram proof.
No live Telegram, Vertex AI, Kaizen, push, or deployment action is authorised
by this document.

## Residual responsibility

The deterministic gate can prove that the supplied source includes reflective
language. It cannot prove that an account is true or that the reflection is
educationally meaningful. Those judgements remain with the resident doctor and
supervisor, as the RCEM statement requires.
