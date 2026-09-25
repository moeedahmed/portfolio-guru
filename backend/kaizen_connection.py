"""How a user is connected to Kaizen: with a stored password, or passwordless.

Password users have encrypted credentials in ``credentials.py`` and stay
connected until they delete their data. Passwordless users signed in on the
Connect Kaizen page (``mobile_kaizen_handoff``); only their signed-in session is
kept, in the per-user session cache the filer already replays, and Kaizen ends
it after about a day. They reconnect with a fresh link, never a password.

Every "is this user connected?" gate in the bot asks this module rather than
``has_credentials`` directly, so a passwordless user is not treated as
disconnected.
"""

from __future__ import annotations

import os

from store import has_credentials

PASSWORD = "password"
PASSWORDLESS = "passwordless"
NONE = "none"


def connection_state(telegram_user_id: int) -> str:
    """``password`` | ``passwordless`` | ``none``.

    A stored password wins: it is what the user most recently chose, because
    choosing passwordless deletes the stored password.
    """
    if has_credentials(telegram_user_id):
        return PASSWORD
    from profile_store import get_kaizen_connection

    if get_kaizen_connection(telegram_user_id) == PASSWORDLESS:
        return PASSWORDLESS
    return NONE


def is_connected(telegram_user_id: int) -> bool:
    return connection_state(telegram_user_id) != NONE


def is_passwordless(telegram_user_id: int) -> bool:
    return connection_state(telegram_user_id) == PASSWORDLESS


def passwordless_offered_to(telegram_user_id: int) -> bool:
    """Whether the passwordless option is switched on for this user.

    ``PG_ENABLE_PASSWORDLESS_CONNECT`` turns the feature on;
    ``PG_PASSWORDLESS_ALLOWLIST`` is ``*`` for everyone or comma-separated ids.
    """
    enabled = os.environ.get("PG_ENABLE_PASSWORDLESS_CONNECT", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return False
    allow = os.environ.get("PG_PASSWORDLESS_ALLOWLIST", "").strip()
    if allow == "*":
        return True
    return str(int(telegram_user_id)) in {part.strip() for part in allow.split(",") if part.strip()}


def has_kept_session(telegram_user_id: int) -> bool:
    """True when a passwordless sign-in has left a session to replay.

    Only proves the session was saved, not that Kaizen still honours it; the
    filer finds that out and reports "session expired" when it has lapsed.
    """
    from kaizen_form_filer import load_session_state

    state = load_session_state(telegram_user_id)
    return bool(state and state.get("cookies"))


def mark_passwordless(telegram_user_id: int) -> None:
    """Record a completed passwordless connection and drop any stored password."""
    from credentials import delete_credentials
    from profile_store import store_kaizen_connection

    delete_credentials(telegram_user_id)
    store_kaizen_connection(telegram_user_id, PASSWORDLESS)


def clear_passwordless(telegram_user_id: int) -> None:
    """Forget the passwordless choice (the user moved to a stored password)."""
    from profile_store import store_kaizen_connection

    store_kaizen_connection(telegram_user_id, None)
