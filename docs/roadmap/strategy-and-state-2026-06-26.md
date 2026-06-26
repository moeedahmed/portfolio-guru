# Strategy & current state — 2026-06-26 (pointers)

The human-readable strategy + current-state dossier lives in Notion (the repo stays
the operational source of truth; Notion is the review/presentation surface).

## Notion (Portfolio Guru → subpages)

- **Architecture-for-Scale Refactor Plan** — https://app.notion.com/p/38bcfc10fc578100a621dc5f1ae1c7a5
  (decompose `bot.py`, make form mappings data-driven, reliable browser-use; do _after_ beta validates demand)
- **Partnership & Positioning Brief** — https://app.notion.com/p/38bcfc10fc5781d6976df20e9a119d08
  (provider ToS + Royal College risks; capture+analytics positioning; assessor wedge; when to seek partnership)
- **Launch Hardening — Completed (2026-06-26)** — https://app.notion.com/p/38bcfc10fc57814daba6c44edaad0f12
- Portfolio Guru main page — https://app.notion.com/p/318cfc10fc578115b752d2d675e01d66

## Current state (summary)

Beta-ready. Clinical AI on **Vertex AI EU (`gemini-3.5-flash`, London)**, dedicated GCP project `portfolio-guru-eu`. **Live billing proven** (£9.99 → upgrade). Deploy gate + smoke/rollback, PII encryption, GDPR erasure, alerting, daily backups all live. Solicitor review of `docs/legal/` gates _public_ launch. Full record: the launch-hardening Notion page + `docs/roadmap/launch-blocker-checklist-2026-06-25.md`.

## The three architecture moves before scaling past beta (not now)

1. Decompose `bot.py` (10.7k lines) into `handlers/`, `flows/`, `keyboards/`.
2. Make form mappings **data** (JSON/YAML), not code — adding a specialty = a config file.
3. Make browser-use (AI filing) reliable enough to be the default → no hand-DOM-mapping per platform.
   Plus: finish the assessor/consultant loop (strongest network-effects + college-trust wedge).
