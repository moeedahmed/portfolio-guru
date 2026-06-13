# Resume Brief — portfolio-guru

Generated: 2026-06-13T01:14:19+00:00
Status: ready

## Where we left off
- Repo: /Users/moeedahmed/projects/portfolio-guru
- Branch: fix/idle-chat-steering
- Last commit: a819610 2026-06-11 fix(bot): preserve case flow on image OCR failure
- Uncommitted changes: no

## Immediate read before restarting
1. `AGENTS.md` for durable repo context
2. Product hub Status + Brief in Notion only for human-facing status, positioning, launch, or risk context
3. `TASK.md` for active sprint state, if present
4. This resume brief for drift warnings and repo state

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
- a819610 2026-06-11 fix(bot): preserve case flow on image OCR failure
- 946eb83 2026-06-08 fix(portfolio): steer idle chat away from drafting
- f8fe06b 2026-06-08 fix(bot): tighten first-open welcome copy
- e95d86d 2026-06-08 docs: record reset copy dogfood smoke
- f5906d8 2026-06-08 fix(bot): tighten reset reconnect copy
- cbddfe4 2026-06-08 fix(bot): prompt kaizen username after reset
- 044c685 2026-06-08 Remove forced Kaizen setup cancel
- 97828fa 2026-06-08 fix(bot): start kaizen setup from unconnected case

## Repo context snapshot
### AGENTS.md — present, 6 days old
Key headings:
- # Portfolio Guru — AGENTS.md (Claude Code Project Context)
- ## Identity
- ## Current State
- ## Dev / Test Commands
- ## Filing Routing Discipline
- ## Key Known Failure Modes
- ## Safety
- ## Supported Forms

### CLAUDE.md — present, 6 days old
Key headings:
- # Portfolio Guru — AGENTS.md (Claude Code Project Context)
- ## Identity
- ## Current State
- ## Dev / Test Commands
- ## Filing Routing Discipline
- ## Key Known Failure Modes
- ## Safety
- ## Supported Forms

### TASK.md — present, 4 days old
Key headings:
- # Active Task — Kaizen Mapping Sprint
- ## Active Sprint — Kaizen Mapping (2026-06-01)
- ### Scorecard (definition of done)
- ### Proof gate
- ### Out of scope (carried forward)
- ## Objective
- ## Current Slice
- ## Done
- ## Verification
- ## Guardrails (Carried Forward)
- ## UX Polish Slice — Post-Filed Buttons (2026-05-26)
- # 539 passed, 22 skipped, 13 deselected, 3 snapshots passed

### docs/plan.md — present, 6 days old
Key headings:
- # Portfolio Guru - Conversational Router Plan
- ## Goal
- ## Product Decision
- ## Architecture
- ### Layer 1 - Natural conversation intake
- ### Layer 2 - Intent router
- ### Layer 3 - Existing deterministic workflows
- ### Layer 4 - Safety and recovery
- ## Implementation Phases
- ### Phase 0 - Checkpoint and branch
- ### Phase 1 - Router contract and tests
- ### Phase 2 - Passive shadow mode

### WORKFLOWS.md — present, 5 days old
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
Continuity context looks sufficient; proceed with normal product-docs workflow.

If this product has been idle for weeks, do not start implementation from memory. Refresh the product hub Status/Brief first, then create or update `TASK.md` for the next sprint.
