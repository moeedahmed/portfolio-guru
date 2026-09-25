# Operator alerts (`backend/ops_alert.py`)

Operator DMs are a **fixed-template, fail-closed boundary**. A caller selects
an event by `key`; the module sends the literal template for that key or
nothing at all. Caller-supplied text is accepted for compatibility and ignored,
so no user id, form type, error string or exception text can reach the operator
chat even if a future caller passes one.

## Fixed event set

| key                | template                                                                                                        | cooldown |
|--------------------|-----------------------------------------------------------------------------------------------------------------|----------|
| `filing_uncertain` | "Portfolio Guru — support alert / A Kaizen draft save could not be confirmed. Check the filing report and verify the draft in Kaizen before retrying." | 900 s (set by `bot._alert_filing_failure`) |
| `handler_error`    | "Portfolio Guru — support alert / An unexpected bot error needs investigation. Check the service logs; do not assume a filing completed." | 300 s |
| `webhook_fail`     | "Portfolio Guru — support alert / A payment webhook could not be processed. Check the provider dashboard."      | 300 s |
| `webhook_unhandled`| same as `webhook_fail`                                                                                          | 300 s |

Any other key is suppressed and logs exactly
`Operator notification suppressed: unknown event` (module and timestamp come
from the logging config). The key itself is never logged because a key could
carry an identifier. Transport failures log only the exception class name.

The two Stripe keys share one cautious template on purpose: `webhook_unhandled`
covers `action in {error, ignored, user_not_found}`, which does not evidence a
charge or a failed upgrade, so the message asserts neither. Visibility is
unchanged; no billing logic changed.

## Cooldown semantics

Cooldown is per key and in-memory. It is a **dedup, not a queue**: distinct
incidents inside one window (e.g. two different trainees hitting uncertain
saves within 15 minutes) collapse into a single DM. `filing_uncertain` is
deliberately category-wide (not per person or per form). A failed send still
consumes the window. No retries, aggregation or persistence exist.

## When filing pages the operator

`bot._alert_filing_failure(context, form_type=, status=, reason=, user_id=)`
classifies status first, reason second:

| call site              | (status, reason)                         | outcome |
|------------------------|------------------------------------------|---------|
| timeout handler        | `timeout`, `timeout`                     | page — completion unknown |
| exception handler      | `exception`, exception class name        | page — mid-filing exception cannot prove no write |
| uncertain_save branch  | `partial`, classified reason             | page, regardless of reason (including `FORM_UNAVAILABLE`) |
| login recovery branch  | `failed`, `LOGIN_FAILED`                 | quiet |
| generic failed branch  | `failed`, `SAVE_FAILURE`                 | page |
| generic failed branch  | `failed`, `FORM_UNAVAILABLE` / `LOGIN_FAILED` / `FIELD_FAILURE` / `UNKNOWN` | quiet |
| anything else          | unexpected status or reason              | quiet |

`partial` without an error never calls the helper. Routine failures remain
visible through the filing-attempt log (`/filingreport`), the funnel log
(`/funnelreport`) and the affected user's own recovery report; these are
asserted by `backend/tests/test_curriculum_filing_recovery.py`.

## Scope statement

This change is repository-only. It adds no retry, aggregation, cooldown
persistence, new recipient, new service or event beyond the four keys above,
and no release card is issued by it. Tests:
`backend/tests/test_ops_alert.py`, `test_funnel_metrics.py`,
`test_error_handler_network.py`, `test_curriculum_filing_recovery.py`.
