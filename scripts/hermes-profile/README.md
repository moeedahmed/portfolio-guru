# Hermes profile shim — tracked source

This folder is the repo-tracked source for the small set of files that
must live under
`~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin/`. The
shim simply delegates every command to the repo-owned CLI
(`backend/hermes_pg_cli.py`); the profile contains no portfolio logic
of its own.

## Layout

| Profile path                                                      | Repo source                 |
| ----------------------------------------------------------------- | --------------------------- |
| `~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin/pg` | `scripts/hermes-profile/pg` |

Everything else under that profile path (the old `recommend.py`,
`draft.py`, `health.py`, `save.py`, `__init__.py`) is **archived**. It
is not loaded by the shim and must not be reintroduced.

## Install (or refresh after a profile rebuild)

```bash
PROFILE=~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin
REPO=~/projects/portfolio-guru
install -m 711 "$REPO/scripts/hermes-profile/pg" "$PROFILE/pg"
```

That is the entire profile-side install. The shim resolves the repo via
`$PORTFOLIO_GURU_REPO` (default `~/projects/portfolio-guru`) and the
Python interpreter via `backend/venv/bin/python3`.

## Smoke check

```bash
~/.hermes/profiles/portfolio-guru/scripts/portfolio-guru/bin/pg status
```

Expected: a single JSON object with `"status": "ok"` and an
`engine_version` field. If you see `"status": "error"`, the shim could
not find the repo or the venv — see the `hint` field in the response.

## Why a shim, not vendored logic?

The profile copy used to ship its own `recommend.py` etc. with a small
keyword-scoring heuristic. That meant the test bot answered with rules
that did not match the live engine — quietly drifting over time. Keeping
the shim thin and the logic in `backend/` makes the test bot reflect
exactly what the live engine does, and lets the repo CI catch
regressions.
