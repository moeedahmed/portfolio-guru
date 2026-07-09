# Resume Brief — portfolio-guru

Generated: 2026-07-09T00:20:41+00:00
Status: stale_or_needs_review

## Where we left off
- Repo: /Users/moeedahmed/projects/portfolio-guru
- Branch: main
- Last commit: 4e7cce9 2026-07-09 Harden Portfolio Guru channel replies
- Uncommitted changes: yes

## Immediate read before restarting
1. `AGENTS.md` for durable repo context
2. Product hub Status + Brief in Notion only for human-facing status, positioning, launch, or risk context
3. `TASK.md` for active sprint state, if present
4. This resume brief for drift warnings and repo state

## Warnings
- Repo has uncommitted local changes — next agent must review and either commit, revert, or summarise them before building from this checkout

## Practical AGENTS.md check
- Useful for coding-agent/ACP work: yes
- No missing practical sections

## Release-loop readiness
- Verdict: present — deterministic closure entrypoint detected
- Scripts found: scripts/release_loop.sh
- Documented references: none
- Surface hints: telegram_bot: yes
- Live/deploy signals: railway.json, render.yaml, .github/workflows/deploy-mac.yml

## Recent commits
- 4e7cce9 2026-07-09 Harden Portfolio Guru channel replies
- 58e944b 2026-07-08 Protect draft generators from reply envelope
- e0d00aa 2026-07-08 Add Portfolio Guru flexible reply style envelope
- e48903b 2026-07-08 Codify controlled-flexible reply policy
- b116032 2026-07-08 Unify Portfolio Guru channel side replies
- 16b8cd5 2026-07-08 Align Kaizen setup replies across channels
- 5be7366 2026-07-08 Route WhatsApp side questions through intent router
- 583f1ef 2026-07-08 Document Portfolio WhatsApp failure ladder

## Uncommitted change summary
- M backend/message_policy.py
-  M backend/tests/test_portfolio_inbound_bridge.py
-  M backend/webhook_server.py

## Repo context snapshot
### AGENTS.md — present, 2 days old
Key headings:
- # Portfolio Guru — AGENTS.md (Claude Code Project Context)
- ## Identity
- ## Current State
- ## Dev / Test Commands
- ## Filing Routing Discipline
- ## Key Known Failure Modes
- ## Safety
- ## Supported Forms

### CLAUDE.md — present, 2 days old
Key headings:
- # Portfolio Guru — AGENTS.md (Claude Code Project Context)
- ## Identity
- ## Current State
- ## Dev / Test Commands
- ## Filing Routing Discipline
- ## Key Known Failure Modes
- ## Safety
- ## Supported Forms

### TASK.md — present, 11 days old
Key headings:
- # Active Task — Hermes Hackathon Production Cut
- ## Hackathon Objective
- ## Locked Product Decision
- ## Sprint 1 — Onboarding And Trust Surface
- ## Sprint 2 — Activation And Failure Telemetry
- ## Sprint 3 — Dashboard, Portfolio Health, And Stripe Proof
- ## Sprint 4 — Hackathon Business-Agent Ledger
- ## Sprint 4b — Demo / Rehearsal Kit
- ## Demo Case And Recording Plan
- ## Day-By-Day Plan
- ## Hackathon Done Criteria
- # Previous Active Task — Kaizen Mapping Sprint

### docs/plan.md — present, 1 days old
Key headings:
- # Portfolio Guru - Conversational Router Plan
- ## Goal
- ## Product Decision
- ## Channel Architecture Decision
- ## Architecture
- ### Layer 1 - Natural conversation intake
- ### Layer 2 - Intent router
- ### Layer 3 - Existing deterministic workflows
- ### Layer 4 - Safety and recovery
- ## Implementation Phases
- ### Phase 0 - Checkpoint and branch
- ### Phase 1 - Router contract and tests

### WORKFLOWS.md — present, 1 days old
Key headings:
- # Portfolio Guru — Agent Workflow Reference
- ## Channel Boundary — Dedicated Portfolio Guru WhatsApp Connector
- ### First-contact parity (WhatsApp opens like the Telegram bot)
- ## Conversation States
- ## Flow 1 — First-Time User
- ## Flow 2 — Core Filing (Happy Path)
- ## Flow 2A — Assess Ticket (Read-Only Mapping / Planned)
- ## Flow 2B — Portfolio Readiness / ARCP Health (Planned)
- ## Flow 3 — Edit Before Filing
- ## Flow 4 — Edit Previously Filed Draft (v2.1 — NOT YET BUILT)
- ## Flow 5 — Reset / Recovery
- ## Form Type Decision Rules

## Recommended restart path
Refresh the product hub/Brief and repo context before a new build sprint.

If this product has been idle for weeks, do not start implementation from memory. Refresh the product hub Status/Brief first, then create or update `TASK.md` for the next sprint.
