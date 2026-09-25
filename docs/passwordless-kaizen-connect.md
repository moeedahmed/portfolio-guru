# Passwordless Kaizen connection — proof stage

**Status:** Proof on the operator's own account. Not wired into the beta bot.
**Date:** 2026-09-23

## Why

Beta users reuse their Kaizen password elsewhere and do not want to hand it
over. The beta today stores it (Fernet-encrypted) in `credentials.py`. A stored
copy is the main way a password leaks, so the goal is to never hold one.

## How it works

1. A one-time link (10 minutes, single use, token kept only as a SHA-256
   digest) is created through the loopback, key-protected broker.
2. The clinician opens it on their phone and signs into the real RCEM login
   page, which runs in an isolated headless browser beside Portfolio Guru and
   is streamed to the phone.
3. When Kaizen reaches a known signed-in route, the browser's session cookies
   are encrypted into the beta's existing per-user session cache
   (`kaizen_form_filer.save_session_state`) with **no username**. The browser
   is then closed.
4. The filer already replays that cache before any login. Called with no
   username, it looks up exactly the file the link wrote
   (`_session_cache_path(uid, "") == _session_cache_path(uid)`, pinned by a
   test), so it can save drafts without a password.

When Kaizen eventually ends the session, the filer bounces to login and fails
cleanly; the fix is a fresh link, not a password.

## Honest security claim

Say **"your password is not stored"**, not "we never see it". Keystrokes pass
through the Portfolio Guru-controlled browser on their way to Kaizen. They are
never written to disk, logged, or sent to an AI model. The kept session is a
bearer credential in its own right, protected with the same Fernet encryption
as today's stored passwords.

Link requests accept only a Telegram user id; any extra field is refused, and
refusals never echo the rejected input.

## Running the proof (operator, own account)

```bash
bash scripts/mobile_kaizen_handoff_test.sh start
backend/venv/bin/python3 scripts/kaizen_passwordless_proof.py link --user-id <your id>
# open the link on your phone and sign in; the page says "Kaizen connected"
backend/venv/bin/python3 scripts/kaizen_passwordless_proof.py status --user-id <your id>
backend/venv/bin/python3 scripts/kaizen_passwordless_proof.py save-test-draft --user-id <your id>
```

`save-test-draft` writes one synthetic CBD draft labelled
"PORTFOLIO GURU PASSWORDLESS TEST DRAFT - SAFE TO DELETE" using no username or
password. It refuses to run without a kept session, so it can never fall
through to a login with empty credentials. Delete the draft in Kaizen after
checking it.

`status` appends to `~/.openclaw/data/portfolio-guru/mobile-handoff/session-lifetime.jsonl`.
Running it every few hours until it reports `EXPIRED` measures how long a
Kaizen session lasts — the number that decides whether this is usable.

The proof session is stored as `<uid>.encrypted`, separate from a password
user's `<uid>-<fingerprint>.encrypted`, so it neither disturbs nor borrows from
an existing beta login.

## Not done yet (gates before users see it)

- One real sign-in and draft save on the operator's account.
- Measured session lifetime.
- A stable, protected hostname. The TryCloudflare tunnel changes on restart and
  is for this proof only.
- Beta wiring: `/setup` offer, filing gate accepting a kept session instead of
  `get_credentials`, and an expiry message that sends a fresh link.
- Legal/DPIA wording for typing through a Portfolio Guru-hosted browser.

## Rollback

`bash scripts/mobile_kaizen_handoff_test.sh stop`, then delete
`~/.openclaw/data/portfolio-guru/sessions/<uid>.encrypted`. The beta bot is
unchanged by this stage.
