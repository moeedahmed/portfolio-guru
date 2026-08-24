# Hermes profile shim — tracked source

This folder is the repo-tracked source for the small set of files that
must live under the Portfolio Guru Hermes profile. The command shim simply
delegates every command to the repo-owned CLI
(`backend/hermes_pg_cli.py`); the profile contains no portfolio logic
of its own.

## Layout

| Profile path                                                      | Repo source                 |
| ----------------------------------------------------------------- | --------------------------- |
| `~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin/pg` | `scripts/hermes-profile/pg` |
| `~/.hermes/profiles/portfolio-guru/plugins/portfolio-guru-engine-dispatch/` | `scripts/hermes-profile/plugins/portfolio-guru-engine-dispatch/` |

Everything else under that profile path (the old `recommend.py`,
`draft.py`, `health.py`, `save.py`, `__init__.py`) is **archived**. It
is not loaded by the shim and must not be reintroduced.

## Install (or refresh after a profile rebuild)

```bash
PROFILE=~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin
REPO=~/projects/portfolio-guru
install -m 711 "$REPO/scripts/hermes-profile/pg" "$PROFILE/pg"
```

That is the entire command-shim install. The shim resolves the repo via
`$PORTFOLIO_GURU_REPO` (default `~/projects/portfolio-guru`) and the
Python interpreter via `backend/venv/bin/python3`.

Install the profile-local dispatch plugin from the tracked source as a unit:

```bash
PLUGIN=~/.hermes/profiles/portfolio-guru/plugins/portfolio-guru-engine-dispatch
REPO=~/projects/portfolio-guru
mkdir -p "$PLUGIN"
install -m 644 "$REPO/scripts/hermes-profile/plugins/portfolio-guru-engine-dispatch/__init__.py" "$PLUGIN/__init__.py"
install -m 644 "$REPO/scripts/hermes-profile/plugins/portfolio-guru-engine-dispatch/plugin.yaml" "$PLUGIN/plugin.yaml"
```

The plugin is deliberately profile-local and patches no Hermes core.

It registers one `pre_gateway_dispatch` hook and one narrow toolset,
`portfolio_guru`, containing `portfolio_case_analyze`,
`portfolio_draft_preview`, and `portfolio_handoff_create`. Enable that toolset
for the Portfolio Guru testing profile so the agent can call them.

On Telegram the hook deliberately takes **no** dispatch decision: ordinary
authorised private text goes to the normal Hermes agent, which owns the
conversation. The hook's only job there is to notice the approval phrase in
the trainee's own words, which is what lets `portfolio_handoff_create` prove
approval without trusting the model. On WhatsApp the deterministic reply path
is unchanged.

The plugin holds one piece of state: bounded, in-memory preview receipts
(15-minute TTL, 20 max, single-use) that tie an approved handoff to the exact
draft the trainee read. It does not import the production `bot.py` runtime.

## Smoke check

```bash
PG=~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin/pg
$PG status

# The Telegram tool surface, offline and Kaizen-free:
echo '{"channel":"telegram","conversation_id":"tg:smoke","gateway_user_id":"1",
 "scope":"direct","private":true,"text":"62M chest pain in ED, NSTEMI","media":[]}' \
  | $PG case-analyze --payload-file -
```

Expected: a single JSON object with `"status": "ok"` and an
`engine_version` field (`status`), or `form_type`/`clarification_options`
(`case-analyze`). If you see `"status": "error"`, the shim could not find the
repo or the venv — see the `hint` field in the response.

## Why a shim, not vendored logic?

The profile copy used to ship its own `recommend.py` etc. with a small
keyword-scoring heuristic. That meant the test bot answered with rules
that did not match the live engine — quietly drifting over time. Keeping
the shim thin and the logic in `backend/` makes the test bot reflect
exactly what the live engine does, and lets the repo CI catch
regressions.
