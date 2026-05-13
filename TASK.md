# TASK: Portfolio Guru continuity refresh — product hub + repo truth

## Goal

Make Portfolio Guru restartable after a break from durable records, not chat memory. The product hub should tell Moeed what exists and what is next; repo context should let agents safely continue the build.

## Scope

1. Refresh product skill standard for solo-builder continuity.
2. Update `CLAUDE.md` so it reflects the current live bot: launchd runtime, Mac Mini deploy, current feature state, disabled commands, evidence-first restart protocol.
3. Refresh the Notion Portfolio Guru hub Status/Architecture so stale MVP/CBD-first/Builder-era text no longer reads as current truth.
4. Record the current limitations clearly:
   - live Kaizen filing confidence still partially verified
   - `/bulk`, `/unsigned`, `/chase` are coming soon despite legacy code paths
   - Notion Brief may retain historical March framing unless rewritten separately
5. Verify with direct inspection: git diff/status, launchd status, current docs, and Notion readback.

## Out of scope

- New bot features
- Live Kaizen filing
- Public beta launch
- Rewriting the full Notion Brief into a polished product narrative
- Removing legacy disabled command code

## Definition of done

- Product skill contains the Solo Builder Continuity Standard.
- `CLAUDE.md` is short, present-tense, and accurate enough for coding agents.
- Notion hub Status/Architecture reflect current product state.
- Change is logged locally.
- Remaining product gaps are explicit, not hidden.
