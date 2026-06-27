# KC Route Evidence — 20260627

Captured via profile-aware and stage-aware read-only DOM inspection of Kaizen new-section pages.
No saves, submissions, or mutations performed.

## Route Classification Key

- **INLINE_TREE_PRIMARY** — `kz-tree` inline curriculum tree rendered (initially or after stage selection).
- **ADD_TAGS_ONLY** — No inline `kz-tree` rendered; Add Tags button present.
- **FALLBACK_INLINE_THEN_TAGS** — Try inline tree first, fallback to Add Tags modal if no checkboxes found.
- **NO_CURRICULUM_SURFACE** — Neither inline tree nor Add Tags button detected.
- **UNKNOWN** — Inspection failed or inconclusive.

## Canonical Form Route Table

| Canonical Form | Route Classification | ACCS Route | Intermediate Route | HST Route | Notes / Evidence |
|----------------|----------------------|------------|--------------------|-----------|------------------|
| CBD | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | ACCS: dropdown set ACCS (before kz=0, after kz=1, slos=4); Intermediate: dropdown set Intermediate (before kz=0, after kz=1, slos=4); HST: dropdown set Higher (before kz=0, after kz=1, slos=14) |
| DOPS | FALLBACK_INLINE_THEN_TAGS | ADD_TAGS_ONLY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | ACCS standard DOPS showed no inline tree after stage selection; Intermediate and HST standard DOPS rendered inline trees. ACCS also has separate DOPS_ACCS inline form. Route inline first, then Add Tags fallback. |
| DOPS_ACCS | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | FAIL | No stage selector |
| MINI_CEX | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | ACCS: dropdown set ACCS (before kz=0, after kz=1, slos=4); Intermediate: dropdown set Intermediate (before kz=0, after kz=1, slos=4); HST: dropdown set Higher (before kz=0, after kz=1, slos=14) |
| ACAT | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | No stage selector |
| LAT | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | No stage selector |
| QIAT | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | No stage selector |
| STAT | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | ACCS: dropdown set ACCS (before kz=1, after kz=1, slos=4); Intermediate: dropdown set Intermediate (before kz=1, after kz=1, slos=4); HST: dropdown set Higher (before kz=1, after kz=1, slos=14) |
| JCF | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | No stage selector |
| TEACH | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | No stage selector |
| PROC_LOG | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | ACCS: dropdown set ACCS (before kz=1, after kz=1, slos=4); Intermediate: dropdown set Intermediate (before kz=1, after kz=1, slos=4); HST: dropdown set Higher (before kz=1, after kz=1, slos=14) |
| PROCEDURAL_LOG_ACCS | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | FAIL | No stage selector |
| SDL | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | No stage selector |
| US_CASE | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | No stage selector |
| ESLE_PART1_2 | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | ACCS: dropdown set ACCS (before kz=1, after kz=1, slos=4); Intermediate: dropdown set Intermediate (before kz=1, after kz=1, slos=4); HST: dropdown set Higher (before kz=1, after kz=1, slos=14) |
| REFLECT_LOG | ADD_TAGS_ONLY | ADD_TAGS_ONLY | ADD_TAGS_ONLY | ADD_TAGS_ONLY | No stage selector |
| TEACH_OBS | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | No stage selector |
| TEACH_CONFID | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | ACCS: dropdown set ACCS (before kz=1, after kz=1, slos=4); Intermediate: dropdown set Intermediate (before kz=1, after kz=1, slos=4); HST: dropdown set Higher (before kz=1, after kz=1, slos=14) |
| COMPLAINT | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | No stage selector |
| SERIOUS_INC | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | No stage selector |
| EDU_ACT | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | No stage selector |
| FORMAL_COURSE | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | INLINE_TREE_PRIMARY | ACCS: dropdown set ACCS (before kz=1, after kz=1, slos=4); Intermediate: dropdown set Intermediate (before kz=1, after kz=1, slos=4); HST: dropdown set Higher (before kz=1, after kz=1, slos=14) |
| AUDIT | UNKNOWN | FAIL | FAIL | FAIL | No stage selector |
| RESEARCH | ADD_TAGS_ONLY | ADD_TAGS_ONLY | ADD_TAGS_ONLY | ADD_TAGS_ONLY | No stage selector |
| PDP | ADD_TAGS_ONLY | ADD_TAGS_ONLY | ADD_TAGS_ONLY | ADD_TAGS_ONLY | No stage selector |

## Raw Profile-Specific Evidence

### Profile: ACCS

| Form | Status | Stage Dropdown | Before kz | After kz | After SLOs | After KCs | Add Tags | Route |
|------|--------|----------------|-----------|----------|------------|-----------|----------|-------|
| CBD | ok | Yes | 0 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| DOPS | ok | Yes | 0 | 0 | 0 | 0 | True | ADD_TAGS_ONLY |
| DOPS_ACCS | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| MINI_CEX | ok | Yes | 0 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| ACAT | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| LAT | ok | No | 3 | 3 | 20 | 17 | True | INLINE_TREE_PRIMARY |
| QIAT | ok | No | 2 | 2 | 14 | 12 | True | INLINE_TREE_PRIMARY |
| STAT | ok | Yes | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| JCF | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| TEACH | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| PROC_LOG | ok | Yes | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| PROCEDURAL_LOG_ACCS | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| SDL | ok | No | 2 | 2 | 12 | 10 | True | INLINE_TREE_PRIMARY |
| US_CASE | ok | No | 2 | 2 | 10 | 8 | True | INLINE_TREE_PRIMARY |
| ESLE_PART1_2 | ok | Yes | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| REFLECT_LOG | ok | No | 0 | 0 | 0 | 0 | True | ADD_TAGS_ONLY |
| TEACH_OBS | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| TEACH_CONFID | ok | Yes | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| COMPLAINT | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| SERIOUS_INC | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| EDU_ACT | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| FORMAL_COURSE | ok | Yes | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| AUDIT | redirect_not_form (Landed at: https://kaizenep.com/events/list) | - | - | - | - | - | - | - |
| RESEARCH | ok | No | 0 | 0 | 0 | 0 | True | ADD_TAGS_ONLY |
| PDP | ok | No | 0 | 0 | 0 | 0 | True | ADD_TAGS_ONLY |

### Profile: Intermediate

| Form | Status | Stage Dropdown | Before kz | After kz | After SLOs | After KCs | Add Tags | Route |
|------|--------|----------------|-----------|----------|------------|-----------|----------|-------|
| CBD | ok | Yes | 0 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| DOPS | ok | Yes | 0 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| DOPS_ACCS | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| MINI_CEX | ok | Yes | 0 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| ACAT | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| LAT | ok | No | 3 | 3 | 20 | 17 | True | INLINE_TREE_PRIMARY |
| QIAT | ok | No | 2 | 2 | 14 | 12 | True | INLINE_TREE_PRIMARY |
| STAT | ok | Yes | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| JCF | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| TEACH | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| PROC_LOG | ok | Yes | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| PROCEDURAL_LOG_ACCS | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| SDL | ok | No | 2 | 2 | 12 | 10 | True | INLINE_TREE_PRIMARY |
| US_CASE | ok | No | 2 | 2 | 10 | 8 | True | INLINE_TREE_PRIMARY |
| ESLE_PART1_2 | ok | Yes | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| REFLECT_LOG | ok | No | 0 | 0 | 0 | 0 | True | ADD_TAGS_ONLY |
| TEACH_OBS | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| TEACH_CONFID | ok | Yes | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| COMPLAINT | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| SERIOUS_INC | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| EDU_ACT | ok | No | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| FORMAL_COURSE | ok | Yes | 1 | 1 | 4 | 3 | True | INLINE_TREE_PRIMARY |
| AUDIT | redirect_not_form (Landed at: https://kaizenep.com/events/list) | - | - | - | - | - | - | - |
| RESEARCH | ok | No | 0 | 0 | 0 | 0 | True | ADD_TAGS_ONLY |
| PDP | ok | No | 0 | 0 | 0 | 0 | True | ADD_TAGS_ONLY |

### Profile: HST

| Form | Status | Stage Dropdown | Before kz | After kz | After SLOs | After KCs | Add Tags | Route |
|------|--------|----------------|-----------|----------|------------|-----------|----------|-------|
| CBD | ok | Yes | 0 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| DOPS | ok | Yes | 0 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| DOPS_ACCS | redirect_not_form (Landed at: https://kaizenep.com/events/list) | - | - | - | - | - | - | - |
| MINI_CEX | ok | Yes | 0 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| ACAT | ok | No | 1 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| LAT | ok | No | 3 | 3 | 20 | 17 | True | INLINE_TREE_PRIMARY |
| QIAT | ok | No | 2 | 2 | 24 | 22 | True | INLINE_TREE_PRIMARY |
| STAT | ok | Yes | 1 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| JCF | ok | No | 1 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| TEACH | ok | No | 1 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| PROC_LOG | ok | Yes | 1 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| PROCEDURAL_LOG_ACCS | redirect_not_form (Landed at: https://kaizenep.com/events/list) | - | - | - | - | - | - | - |
| SDL | ok | No | 2 | 2 | 22 | 20 | True | INLINE_TREE_PRIMARY |
| US_CASE | ok | No | 2 | 2 | 20 | 18 | True | INLINE_TREE_PRIMARY |
| ESLE_PART1_2 | ok | Yes | 1 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| REFLECT_LOG | ok | No | 0 | 0 | 0 | 0 | True | ADD_TAGS_ONLY |
| TEACH_OBS | ok | No | 1 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| TEACH_CONFID | ok | Yes | 1 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| COMPLAINT | ok | No | 1 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| SERIOUS_INC | ok | No | 1 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| EDU_ACT | ok | No | 1 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| FORMAL_COURSE | ok | Yes | 1 | 1 | 14 | 13 | True | INLINE_TREE_PRIMARY |
| AUDIT | redirect_not_form (Landed at: https://kaizenep.com/events/list) | - | - | - | - | - | - | - |
| RESEARCH | ok | No | 0 | 0 | 0 | 0 | True | ADD_TAGS_ONLY |
| PDP | ok | No | 0 | 0 | 0 | 0 | True | ADD_TAGS_ONLY |

