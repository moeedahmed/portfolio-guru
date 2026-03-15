# TASK-HISTORY — portfolio-guru

## 2026-03-15
- [DONE] Rebuilt WPBA type suggestion flow (bot.py, extractor.py) — commit 8960397
- [DONE] Fixed AWAIT_CASE_INPUT missing CallbackQueryHandlers
- [DONE] Added load_dotenv to bot.py
- [DONE] Fixed extract_explicit_form_type false triggers
- [DONE] Ported KC mappings into form_schemas.py (SLO3/4/5/6)
- [DONE] Added CDP connection mode to kaizen_filer.py
- [DONE] Built bulk_filer.py + /bulk command
- [DONE] Built kaizen_unsigned_scraper.py + /unsigned command
- [DONE] Built chase_guard.py + /chase command
- [DONE] Archived Medic Kaizen scripts to legacy/
- [DONE] Added 5-category intent classifier with case_context param
- [DONE] Built handle_template_review_text — chitchat/question/new_case routing
- [DONE] Built handle_edit_value_with_intent — guards short chitchat in edit mode
- [DONE] Fixed context.user_data.clear() in chitchat handler (was dropping drafts)
- [DONE] Added post_reset flag to /reset command
- [DONE] Built _edit_last_bot_msg helper for in-place message editing
- [DONE] Built CASE|new and CASE|improve callback handlers
- [DONE] Upgraded model: gemini-2.5-flash → gemini-3-flash-preview
- [DONE] Fixed FERNET_SECRET_KEY loading race condition in credentials.py
- [DONE] Fixed SLO padding — removed 3-minimum constraint from KC extraction prompt
- [DONE] Improved partial filing message — names skipped fields, explains why
- [DONE] Error handler: edit in place instead of spawning new messages
