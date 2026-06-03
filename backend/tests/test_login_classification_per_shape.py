"""Per-shape login classification offline tests (P1.a).

Plan: ``docs/roadmap/filing-reliability-readiness-sprint-2026-06.md`` §4 P1.a.

The invariant this slice protects: the bot's login-outcome classification
(credential failure vs infrastructure failure vs success vs auth-required)
must not silently degrade based on the detected Kaizen role. Every shape in
our trusted-tester pool ends up classified the same way for the same
physical outcome, so a regression that mis-categorises (e.g. an infra failure
gets re-bucketed as ``login failed`` only on the SAS shape) is loud, not
silent.

Shapes covered — kept **separate** on purpose:

- ``hst``                            — HST / CCT pathway shape.
- ``accs``                           — DREAM Pathway ACCS-only Kaizen role.
- ``intermediate``                   — DREAM Pathway Intermediate-only role.
- ``accs_intermediate_dual_access``  — one trainee with **both** ACCS and
                                       Intermediate Portfolio access. The
                                       provider returns the
                                       ``accs_intermediate`` portfolio_type
                                       for this shape today, which collapses
                                       dual access into a single storage
                                       bucket. This test pins that
                                       implementation/storage behaviour;
                                       it does **not** assert that
                                       ``accs_intermediate`` is a standalone
                                       Kaizen portfolio type.
- ``sas_cesr``                       — SAS / CESR Portfolio Pathway shape.
                                       The provider returns ``"sas"`` for
                                       SAS / CESR / Non-trainee landings
                                       where no stage signal is visible.
- ``non_training_higher``            — Non-training shape whose Kaizen
                                       surface labels the portfolio
                                       ``Non-Trainee Higher``. The provider
                                       returns the explicit
                                       ``non_training_higher`` string so
                                       the user-visible label retains the
                                       Higher stage rather than collapsing
                                       to the generic SAS bucket.

Boundary: no live Kaizen, no CDP, no BWS, no Telegram, no network. Same
stub style as ``test_kaizen_login_reliability.py`` for the provider-level
paths, and ``test_kaizen_sync.py`` for the bootstrap-level auth_required
path.
"""

from __future__ import annotations

import importlib

import pytest


# Map: P1.a shape test-id → provider.portfolio_type the role probe sets when
# a dashboard landing succeeds for that account.
#
# ``accs_intermediate_dual_access`` deliberately maps to the
# ``accs_intermediate`` provider string — that is the dual-access storage
# behaviour we want pinned, not a claim that ``accs_intermediate`` is a
# portfolio type in its own right.
SHAPE_TO_PROVIDER_ROLE = {
    "hst": "hst",
    "accs": "accs",
    "intermediate": "intermediate",
    "accs_intermediate_dual_access": "accs_intermediate",
    "sas_cesr": "sas",
    "non_training_higher": "non_training_higher",
}

SHAPES = tuple(SHAPE_TO_PROVIDER_ROLE)


# Distinct integer user ids per shape so the sync-layer ``index_runs`` rows
# don't collide across parametrised runs. Well above the synthetic-user
# threshold (99999999) is intentional — these are throw-away ids used
# against the per-test ``USAGE_DB_PATH`` SQLite DB.
SHAPE_TO_USER_ID = {
    "hst": 9100001,
    "accs": 9100002,
    "intermediate": 9100003,
    "accs_intermediate_dual_access": 9100004,
    "sas_cesr": 9100005,
    "non_training_higher": 9100006,
}


# ─── credential_failure per shape ───────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES, ids=SHAPES)
async def test_credential_failure_classifies_as_login_failed_per_shape(
    monkeypatch, shape
):
    """A wrong-password rejection must classify as ``credential_failure``
    (the wrapper returns ``False``) for every shape — never as
    ``infra_failure``, which would tell the user the network is broken when
    their password is in fact wrong."""
    from bot import _test_kaizen_login

    class FakeProvider:
        portfolio_type = "unknown"

        def __init__(self, *_args, **_kwargs):
            pass

        def connect(self):
            return False

        def disconnect(self):
            pass

    monkeypatch.setattr("engine.providers.kaizen.KaizenProvider", FakeProvider)
    result = await _test_kaizen_login("doctor@example.com", "wrong-pw")
    assert result is False, (
        f"{shape}: credential rejection must classify as False "
        f"(credential_failure); got {result!r}"
    )


# ─── infra_failure per shape ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES, ids=SHAPES)
async def test_infra_failure_classifies_as_infrastructure_error_per_shape(
    monkeypatch, shape
):
    """Browser-harness / CDP / subprocess failure must surface as
    ``KaizenInfrastructureError`` for every shape — never silently downgrade
    to a ``False`` return, which would re-create the misclassification bug
    that trains users to retype passwords that are actually fine."""
    from bot import _test_kaizen_login
    from engine.providers.kaizen import KaizenInfrastructureError

    class FakeProvider:
        def __init__(self, *_args, **_kwargs):
            pass

        def connect(self):
            raise KaizenInfrastructureError(f"{shape}: subprocess died")

    monkeypatch.setattr("engine.providers.kaizen.KaizenProvider", FakeProvider)
    with pytest.raises(KaizenInfrastructureError):
        await _test_kaizen_login("doctor@example.com", "pw")


# ─── success per shape (positive baseline) ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES, ids=SHAPES)
async def test_dashboard_landing_classifies_as_success_per_shape(
    monkeypatch, shape
):
    """A successful login must classify as success and bind the bot's
    detected role string to the role that shape produces today.

    For ``accs_intermediate_dual_access`` this pins the storage collapse:
    the dual-access shape's dual access surfaces as the single ``accs_intermediate`` role
    string. A future split into per-portfolio roles will need to update this
    test alongside the storage change.
    """
    from bot import _test_kaizen_login

    expected_role = SHAPE_TO_PROVIDER_ROLE[shape]

    class FakeProvider:
        portfolio_type = expected_role

        def __init__(self, *_args, **_kwargs):
            pass

        def connect(self):
            return True

        def disconnect(self):
            pass

    monkeypatch.setattr("engine.providers.kaizen.KaizenProvider", FakeProvider)
    result = await _test_kaizen_login("doctor@example.com", "pw")
    assert result == expected_role, (
        f"{shape}: dashboard landing must classify as {expected_role!r}, "
        f"got {result!r}"
    )


# ─── auth_required per shape (the SAS / CESR 2026-06-02 reproducer) ────────


@pytest.fixture
def sync_modules(tmp_path, monkeypatch):
    """Reload kaizen_index + kaizen_sync against a throw-away usage DB so
    the parametrised auth_required cases never touch any real index."""
    monkeypatch.setenv(
        "USAGE_DB_PATH", str(tmp_path / "login_classification_test.db")
    )
    import kaizen_index
    import kaizen_sync

    kaizen_index = importlib.reload(kaizen_index)
    kaizen_sync = importlib.reload(kaizen_sync)
    return kaizen_index, kaizen_sync


class _FakeContext:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class _FakePlaywright:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


class _FakeAuthPage:
    """Stub Kaizen page that mimics the 2026-06-02 SAS / CESR outcome: every
    navigation lands on the auth host, no portfolio rows ever materialise."""

    def __init__(self):
        self.url = "https://auth.kaizenep.com/interaction/login"
        self.context = _FakeContext()

    async def goto(self, url, **_kwargs):
        self.url = url

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    async def evaluate(self, *_args, **_kwargs):
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES, ids=SHAPES)
async def test_non_portfolio_landing_is_auth_required_per_shape(
    sync_modules, monkeypatch, shape
):
    """Bootstrap that ends on a non-portfolio page (the exact 2026-06-02
    SAS / CESR outcome) must classify as ``auth_required`` for every shape —
    never as ``ok`` and never as ``failed``.

    A future regression that maps the SAS landing to ``ok`` (because the
    role probe returns ``"sas"`` and we short-circuit before checking the
    URL) is exactly what this slice prevents."""
    _, kaizen_sync = sync_modules
    page = _FakeAuthPage()
    pw = _FakePlaywright()

    async def fake_open():
        return page, pw

    async def fake_cached(arg_page, arg_uid):
        return False

    def fake_creds(uid):
        return ("doctor@example.com", "pw")

    async def fake_login(arg_page, username, password):
        # Mirror the 2026-06-02 SAS / CESR shape: login ran, Kaizen redirected
        # back to /auth instead of /portfolio, so the helper returns False
        # and the bootstrap turns that into auth_required.
        return False

    monkeypatch.setattr(kaizen_sync, "_open_kaizen_session_page", fake_open)
    monkeypatch.setattr(kaizen_sync, "_restore_cached_session", fake_cached)
    monkeypatch.setattr(kaizen_sync, "_load_user_credentials", fake_creds)
    monkeypatch.setattr(kaizen_sync, "_login_kaizen_page", fake_login)

    user_id = SHAPE_TO_USER_ID[shape]
    result = await kaizen_sync.sync_kaizen_portfolio_index_for_user(
        user_id,
        categories=("Assessments",),
        include_activities=False,
    )

    assert result.status == "auth_required", (
        f"{shape}: non-portfolio landing must classify as auth_required, "
        f"got {result.status!r}"
    )
    assert result.rows_written == 0
    assert page.context.closed
    assert pw.stopped
