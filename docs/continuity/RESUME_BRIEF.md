# Resume Brief — portfolio-guru

Generated: 2026-05-29 (manual update after continuity check)
Status: current

## Where we left off

- Repo: /Users/moeedahmed/projects/portfolio-guru
- Branch: chore/telegram-bot-qa-discipline
- Last commit: c8fe80c 2026-05-29 feat: add vNext source-tied clinical extraction
- Uncommitted changes: yes (this slice — not yet committed; see TASK.md addendum)

## Latest vNext slice (2026-05-29)

Added deterministic form recommendation and local preview helpers for the
private vNext bot. `vnext_form_recommender.py` recommends CBD/DOPS/PROC_LOG/
US_CASE/REFLECT_LOG only from captured source-tied facts, or returns a targeted
missing-detail prompt. `vnext_draft_preview.py` builds a local dogfood preview
marked as not a Kaizen draft. `vnext_runner.py` now includes that preview on
OFFER_DRAFT. Public bot and Kaizen filing remain untouched.

## Next step

- Verify and commit this slice.
- Restart the live private bot after verification.
- Dogfood the live private bot on realistic messy cases and compare against the
  current public bot before any public identity migration discussion.

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

- c8fe80c 2026-05-29 feat: add vNext source-tied clinical extraction
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
- ?? backend/tests/test_vnext_draft_preview.py
- ?? backend/tests/test_vnext_form_recommender.py
- ?? backend/vnext_draft_preview.py
- ?? backend/vnext_form_recommender.py
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
