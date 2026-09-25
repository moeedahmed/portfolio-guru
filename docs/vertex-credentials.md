# Vertex AI credential lifecycle (staged, not yet activated)

This is the current design. The earlier persistent-file candidate
(`GCP_VERTEX_CREDENTIALS_PATH` pointing at an operator-provisioned key file
on disk, validated by a gate script before export as
`GOOGLE_APPLICATION_CREDENTIALS`) is **rejected and superseded**. It is
preserved only as an archived, inert reference
(`backend/_archive_local/persistent_file_contract_2026-09-10/`, gitignored,
local to the staging checkout) and must not be reintroduced or documented as
current.

## What changed

`backend/run_local.sh` no longer fetches `GCP_VERTEX_SA_JSON` from BWS or
writes any service-account key to disk (no `mktemp`, no
`GOOGLE_APPLICATION_CREDENTIALS`). BWS remains the sole canonical, encrypted
credential source. The Vertex service-account credential now exists in
plaintext only inside the runtime Python process's memory, for the life of
that process — it is never written to a file and never placed in that
process's own environment block.

## Runtime contract (`backend/vertex_credentials.py`)

- `get_credentials(project_id)` fetches exactly one BWS secret (id from the
  nonsecret `PG_VERTEX_SA_SECRET_ID`), validates it, and returns a cached,
  in-memory `google.oauth2.service_account.Credentials` object. Caching is
  per-process, lock-guarded, single-flight, and bound to the
  `(secret_id, project_id)` pair used on first fetch — a later call with a
  different pair is treated as configuration drift and rejected.
- The BWS access token is **never read from this process's own
  environment**. It is read, at call time, from a regular file at the
  absolute path in `PG_VERTEX_BWS_TOKEN_PATH` — a nonsecret config value
  that must point at the **same existing beta credential route** the
  launcher already uses (e.g. `~/.openclaw/.bws-token`, or the canonical
  personal route `~/.hermes/.bws-token`). This module does not choose,
  default, or fall back across those routes; the activation owner sets
  `PG_VERTEX_BWS_TOKEN_PATH` explicitly. The file must be owned by the
  current user, must not be a symlink, must not be group/other readable,
  and is read with a bounded, `O_NOFOLLOW` open. The token is handed only
  to the `bws` child process's environment — it never enters this
  process's own `os.environ`.
- `bws` is invoked at a fixed, guarded absolute path
  (`/Users/moeedahmed/.cargo/bin/bws`) — no `PATH` search, no raw-binary
  fallback — with `shell=False`, a bounded timeout, and a minimal child
  environment (`BWS_ACCESS_TOKEN`, `HOME`, and a fixed minimal `PATH`; no
  other inherited variables). No credential value ever appears in argv.
- The response is validated, in order: non-timeout/non-zero-exit, response
  size, JSON shape, returned `id` matches the requested id, `key` matches
  `GCP_VERTEX_SA_JSON`, required string fields present, `type ==
  "service_account"`, `project_id` matches `GCP_PROJECT_ID`, `client_email`
  matches the nonsecret `PG_VERTEX_SA_CLIENT_EMAIL` (pins the exact existing
  identity — a matching `project_id` alone does not prove "same account"),
  and `token_uri` is exactly `https://oauth2.googleapis.com/token`.
- Every failure path raises `VertexCredentialError` with a fixed, generic
  message. The fetch/parse/validate/build sequence runs behind a single
  boundary inside `get_credentials`: on failure only the fixed message
  string crosses out, and a fresh exception is raised outside the
  `except` clause, so the original exception object — and any secret
  material held in its traceback frames' locals — is not reachable from
  what callers see, log, or could capture via a debugger/handler.

## Eager preflight (`backend/vertex_preflight.py`)

`run_local.sh` runs `"$PYTHON" -m vertex_preflight` once, after the venv is
selected and before the webhook server starts, before Telegram polling
starts, and before any readiness/health signal. It fetches the credential
and performs a single `credentials.refresh()` against the real Google token
endpoint (no model call, no public send) and asserts
`GOOGLE_APPLICATION_CREDENTIALS` is absent (Vertex mode never falls back to
Application Default Credentials). Any failure exits the launcher non-zero
before any traffic is admitted, instead of surfacing on the first clinical
request. This preflight's credential object does not cross the process
boundary — the bot and webhook processes each call
`vertex_credentials.get_credentials()` again, once, in their own process,
via `gemini_client.make_client()`.

`run_local.sh` also fails closed, before any Vertex-scoped BWS lookup, if
`PG_USE_VERTEX` is truthy but `GCP_PROJECT_ID` is not set.

## `gemini_client.make_client()`

In Vertex mode, calls `vertex_credentials.get_credentials(project)` and
passes the result straight to `genai.Client(vertexai=True, project=...,
location=..., credentials=...)`. There is no ADC fallback: a credential
fetch failure raises rather than silently routing clinical data off-region
or authenticating as an ambient identity. Developer-API mode (flag off) is
unchanged. All shared factory callers (text, vision, voice, document
extraction via the webhook path) continue to work unmodified.

## Configuration (all nonsecret; set via BWS `get_secret_by_key`, same as
## other launcher config)

| Variable | Purpose |
| --- | --- |
| `PG_USE_VERTEX` | Enables Vertex routing (requires `GCP_PROJECT_ID`). |
| `GCP_PROJECT_ID` | Existing GCP project id. |
| `GCP_VERTEX_LOCATION` | Existing region (default `europe-west2`). |
| `GEMINI_VERTEX_MODEL` | Optional model override. |
| `PG_VERTEX_SA_SECRET_ID` | BWS secret id of the existing `GCP_VERTEX_SA_JSON` secret. Not the credential itself. |
| `PG_VERTEX_SA_CLIENT_EMAIL` | Expected existing service-account email, pinned at validation time. Not a secret. |
| `PG_VERTEX_BWS_TOKEN_PATH` | Absolute path to the *same existing* beta BWS token file the launcher already reads. Not a secret (a path, not a credential). |

**None of these are created, generated, guessed, or defaulted by this
design.** `PG_VERTEX_SA_SECRET_ID` and `PG_VERTEX_SA_CLIENT_EMAIL` identify
the existing `GCP_VERTEX_SA_JSON` secret and its existing account — no new
service-account, no new BWS secret. `PG_VERTEX_BWS_TOKEN_PATH` must be the
existing beta token route, not a new or alternate one.

## Known limits

- Plaintext in process memory is not protection against a sufficiently
  privileged host: ptrace/debugger attachment, core dumps, or swap can
  still expose the decrypted service-account key (including its private
  key material) for as long as the process runs. This design only removes
  the credential from the filesystem and from the process's environment
  variables — it does not add memory protection beyond what the OS/SDK
  already provide. This is not zero disk/memory exposure and is not
  claimed to be.
- The single-flight lock serializes concurrent credential construction only
  during the very first fetch in a process's lifetime; the credential is
  cached after that.
- Token-refresh lifetime/behaviour is entirely the SDK's
  (`google.oauth2.service_account.Credentials`); this design does not alter
  it.

## Activation dependencies (not performed by this design)

1. Confirm the existing `GCP_VERTEX_SA_JSON` BWS secret id and set
   `PG_VERTEX_SA_SECRET_ID` to it. No new secret or account is created.
2. Set `PG_VERTEX_SA_CLIENT_EMAIL` to that same existing service account's
   email.
3. Set `PG_VERTEX_BWS_TOKEN_PATH` to the absolute path of the same existing
   beta BWS token file the launcher already uses.
4. Restart the bot under the sanctioned activation owner to pick this up —
   not performed by this design.

This design is staged only. It has not been activated, deployed, or run
against real credentials, and does not instruct creating any account,
secret, or policy exception.
