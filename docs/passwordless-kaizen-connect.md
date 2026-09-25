# Passwordless Kaizen connection

**Status:** Built into the beta bot behind a switch; on for the operator only.
**Proven:** 2026-09-23 (draft saved with no password) · **Session lifetime measured:** about 24 hours (2026-09-24)

## Why

Beta users reuse their Kaizen password elsewhere and do not want to hand it
over. The beta stores it (Fernet-encrypted) in `credentials.py`. A stored copy
is the main way a password leaks, so this option never holds one.

## What the user sees

- `/setup`, `/start` and the "connect first" prompt ask for the Kaizen
  username as before, with a second button: **🔒 Connect without sharing my
  password**, and the line "you'll need to sign in again about once a day".
- That button sends **Sign in to Kaizen** (opens `connect.emgurus.com`) and
  **I've signed in**. The bot believes "I've signed in" only after it has opened
  Kaizen with the kept session, and reads the portfolio type as the password
  route does, then continues with the usual profile questions.
- Choosing it deletes any stored password, locally and in the Supabase mirror.
- When Kaizen has ended the session, saving shows **"Kaizen has signed you
  out"** with **🔒 Sign in again**. The draft is kept and saves once they have
  signed in. There are no background messages and no operator alert for this
  expected expiry.
- `/unsigned` and learning your writing style from Kaizen still need a
  password connection. Passwordless users get a plain explanation.

| Connection                   | Stays connected                                       |
| ---------------------------- | ----------------------------------------------------- |
| Username and password        | Until the user deletes their data                     |
| Without sharing the password | About a day, then sign in again (usually when saving) |

## How it works

- `backend/mobile_kaizen_handoff.py` serves the sign-in page on
  `127.0.0.1:8101`. It streams an isolated headless browser showing the real
  RCEM login. Once Kaizen reaches a signed-in route, the browser's cookies are
  encrypted into the existing per-user session cache
  (`kaizen_form_filer.save_session_state`, no username) and the browser closes.
- `backend/run_local.sh` starts it with the bot when
  `PG_ENABLE_PASSWORDLESS_CONNECT` is on. It gets an empty environment plus the
  session-encryption key only, never the bot, payment or AI keys.
- `backend/kaizen_connection.py` answers "how is this user connected?"
  (`password` / `passwordless` / `none`). The choice is stored in
  `userprofile.kaizen_connection`; it is not mirrored to Supabase.
- Saving passes empty credentials; the filer replays the kept session. With no
  password, `_login` never contacts RCEM, so an expired session is reported as
  a login failure and the bot offers a fresh link.
- Links are single-use, expire in 10 minutes, one per user (asking again
  replaces an unused one), and at most two sign-in browsers run at once. The
  link endpoint accepts only a Telegram user id, refuses any other field and
  never echoes rejected input.

## Honest security claim

Say **"we never store your password"**, not "we never see it". Keystrokes pass
through the Portfolio Guru-hosted browser on their way to Kaizen; they are
never written down, logged, or sent to an AI model. The kept session is itself a
short-lived credential, protected like stored passwords.

## Rollout

- `PG_ENABLE_PASSWORDLESS_CONNECT` (default on in `run_local.sh`) and
  `PG_PASSWORDLESS_ALLOWLIST` (default: the operator only; `*` for everyone).
- Needs `connect.emgurus.com → http://127.0.0.1:8101` on the Cloudflare tunnel.
- Legal drafts (`docs/legal/privacy-policy.md`, `docs/legal/dpia.md`) carry
  «REVIEW» paragraphs for the solicitor review that gates the wider beta.

## Operator tool

`scripts/kaizen_passwordless_proof.py link | status | save-test-draft --user-id <id>`
makes a link, checks whether a kept session still opens Kaizen, or saves one
labelled synthetic CBD draft ("SAFE TO DELETE") with no username or password.

## Rollback

Set `PG_ENABLE_PASSWORDLESS_CONNECT=` (empty) and restart the bot: the option
and the sign-in page disappear. Passwordless users are then shown as not
connected and are asked to connect again; nothing else changes.
