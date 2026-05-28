# Resume Brief — portfolio-guru

Generated: 2026-05-29 (manual update after continuity check)
Status: current

## Where we left off

- Repo: /Users/moeedahmed/projects/portfolio-guru
- Branch: chore/telegram-bot-qa-discipline
- Last commit: 0d04468 2026-05-28 feat: run private vNext Telegram bot
- Uncommitted changes: yes (this slice — not yet committed; see TASK.md addendum)

## Latest vNext slice (2026-05-29)

Extended `vnext_text_extractor.py` with 6 new verbatim extractors (setting,
presenting_complaint, diagnosis, procedure, supervision, learning_point).
Added `_is_draft_ready()` to the engine (≥3 eligible facts + ≥1 clinical key →
DRAFT_READY). The acceptance-criteria STEMI case now produces 8 facts and reaches
DRAFT_READY in a single message. Full offline gate: 765 passed, 0 failed.

## Next step

- Commit this slice (files: vnext_text_extractor.py, conversational_case_engine.py,
  vnext_runner.py, test_vnext_text_extractor.py, test_conversational_case_engine.py,
  test_telegram_vnext_adapter.py, test_conversational_vnext_bot.py, TASK.md,
  docs/plan.md, docs/continuity/RESUME_BRIEF.md).
- Dogfood the live private bot using `scripts/run_vnext_local.sh`.
- Next feature: form-type recommendation and local preview from captured facts.

## Immediate read before restarting

1. Product hub Status + Brief in Notion
2. `AGENTS.md` for durable repo context
3. `TASK.md` for active sprint state, if present
4. This resume brief for drift warnings and repo state

## Warnings

- Repo has uncommitted changes — capture/commit or summarise before switching context

## Practical AGENTS.md check

- Useful for coding-agent/ACP work: yes
- No missing practical sections

## Recent commits

- 0d04468 2026-05-28 feat: run private vNext Telegram bot
- 75a9fa5 2026-05-28 feat: add conservative vNext text fact extractor
- b14c37d 2026-05-28 feat: add vNext Telegram→engine adapter
- 5d7425b 2026-05-28 feat: scaffold vNext conversational case engine
- 1c79a36 2026-05-28 fix: keep form choice escape hatch
- 9eb87b3 2026-05-28 fix: guard QIAT stage and curriculum tags
- 86242e5 2026-05-28 fix: gate new input after failed filing
- 3b78fff 2026-05-27 fix: harden LAT Kaizen field filling

## Uncommitted change summary

- ?? .openclaw/
- ?? HEARTBEAT.md
- ?? IDENTITY.md
- ?? SOUL.md
- ?? TOOLS.md
- ?? USER.md

## Repo context snapshot

### AGENTS.md — present, 5 days old

Key headings:

- # Portfolio Guru — AGENTS.md
- ## Project
- ## Current Bot State
- ## Stack
- ## Key Constraints
- ## Filing Routing Discipline
- ## Known Failure Modes
- ## Supported Forms
- ## Key Files
- ## Conversation States
- ## Key Design Decisions
- ## Continuity Protocol

### CLAUDE.md — present, 5 days old

Key headings:

- # Portfolio Guru — AGENTS.md
- ## Project
- ## Current Bot State
- ## Stack
- ## Key Constraints
- ## Filing Routing Discipline
- ## Known Failure Modes
- ## Supported Forms
- ## Key Files
- ## Conversation States
- ## Key Design Decisions
- ## Continuity Protocol

### TASK.md — present, 0 days old

Key headings:

- # Active Task — Private Beta Launch Cut
- ## Objective
- ## Current Slice
- ## Done
- ## Verification
- ## Guardrails (Carried Forward)
- ## UX Polish Slice — Post-Filed Buttons (2026-05-26)
- # 539 passed, 22 skipped, 13 deselected, 3 snapshots passed
- ## Orchestrator Hand-Off

### WORKFLOWS.md — present, 1 days old

Key headings:

- # Portfolio Guru — Agent Workflow Reference
- ## Conversation States
- ## Flow 1 — First-Time User
- ## Flow 2 — Core Filing (Happy Path)
- ## Flow 2A — Assess Ticket (Read-Only Mapping / Planned)
- ## Flow 2B — Portfolio Readiness / ARCP Health (Planned)
- ## Flow 3 — Edit Before Filing
- ## Flow 4 — Edit Previously Filed Draft (v2.1 — NOT YET BUILT)
- ## Flow 5 — Reset / Recovery
- ## Form Type Decision Rules
- ## Key Capabilities Selection Rules
- ## Data Flow

## Recommended restart path

Refresh the product hub/Brief and repo context before a new build sprint.

If this product has been idle for weeks, do not start implementation from memory. Refresh the product hub Status/Brief first, then create or update `TASK.md` for the next sprint.
