"""Per-shape detected-role → ``training_level`` mapping (P1.b).

Plan: ``docs/roadmap/filing-reliability-readiness-sprint-2026-06.md`` §4 P1.b.

The invariant this slice protects: the setup/login path's detected-role →
local portfolio-profile bucket map keeps the **separate** portfolio types
separate. ACCS and Intermediate are distinct Kaizen portfolio types —
collapsing them silently into one bucket would route Intermediate trainees
to ACCS-only forms, or vice versa, with no test surface to catch it.

Harris is the dual-access edge case: one trainee with access to **both**
ACCS and the Intermediate Portfolio. The bot's current storage collapses
that dual access into a single ``accs_intermediate`` Kaizen role string and
maps it to the ``INTERMEDIATE`` ``training_level`` bucket. This file pins
that storage-alias behaviour. It does **not** assert that
``accs_intermediate`` is a standalone Kaizen portfolio type.

Separately, ``profile_store.store_kaizen_role`` preserves the raw detected
role verbatim. The detected-role → ``training_level`` map lives on the
setup/login path in ``backend/bot.py`` and is applied via
``store_training_level``. The two surfaces must stay decoupled so a future
split (e.g. surfacing both ACCS and Intermediate access for Harris) only
needs to update the map, not the raw-role storage.

Boundary: offline-only. No live Kaizen, no CDP, no BWS, no Telegram, no
network. Reuses the in-memory ``profile_store`` engine pattern from
``test_profile_store_kaizen_role.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine


# Map: P1.b shape test-id → (raw detected role string the provider returns,
# expected ``training_level`` bucket the setup/login path stores).
#
# Kept identical in spirit to the P1.a shape table — separate ACCS and
# Intermediate entries make a silent collapse loud, not silent. The
# ``accs_intermediate_dual_access`` row mapping to ``INTERMEDIATE`` is the
# current dual-access storage alias, **not** a claim that
# ``accs_intermediate`` is a standalone portfolio type.
SHAPE_TO_ROLE_AND_LEVEL = {
    "hst": ("hst", "HIGHER"),
    "accs": ("accs", "ACCS"),
    "intermediate": ("intermediate", "INTERMEDIATE"),
    "accs_intermediate_dual_access": ("accs_intermediate", "INTERMEDIATE"),
    "sas_cesr": ("sas", "SAS"),
}

SHAPES = tuple(SHAPE_TO_ROLE_AND_LEVEL)


# Distinct integer user ids per shape so the round-trip test below produces
# one row per shape and a cross-contamination regression surfaces as the
# wrong row, not a collision. Well above the synthetic-user threshold
# (99999999) is intentional — these are throw-away ids against the
# in-memory profile_store engine.
SHAPE_TO_USER_ID = {
    "hst": 9200001,
    "accs": 9200002,
    "intermediate": 9200003,
    "accs_intermediate_dual_access": 9200004,
    "sas_cesr": 9200005,
}


def _memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def profile_store_module(monkeypatch):
    import profile_store

    engine = _memory_engine()
    monkeypatch.setattr(profile_store, "engine", engine)
    SQLModel.metadata.create_all(engine)
    return profile_store


# ─── detected_role_to_training_level helper, per shape ──────────────────────


@pytest.mark.parametrize("shape", SHAPES, ids=SHAPES)
def test_detected_role_maps_to_expected_training_level_per_shape(shape):
    """The setup/login role map keeps each shape's bucket distinct.

    ACCS-only and Intermediate-only are separate Kaizen portfolio types.
    The dual-access ``accs_intermediate`` row pins current storage
    behaviour (Harris): both accesses collapse to the ``INTERMEDIATE``
    bucket. A future split would need to update this test alongside the
    storage change.
    """
    from bot import detected_role_to_training_level

    role, expected_level = SHAPE_TO_ROLE_AND_LEVEL[shape]
    assert detected_role_to_training_level(role) == expected_level, (
        f"{shape}: detected role {role!r} must map to {expected_level!r}"
    )


def test_accs_and_intermediate_levels_are_distinct_not_collapsed():
    """ACCS-only and Intermediate-only are separate portfolio types.

    A regression that collapses them into the same bucket would silently
    route Intermediate-only trainees to ACCS forms (or vice versa), with
    no other test catching it. Pin distinctness explicitly so the
    collapse is loud.
    """
    from bot import detected_role_to_training_level

    assert detected_role_to_training_level("accs") == "ACCS"
    assert detected_role_to_training_level("intermediate") == "INTERMEDIATE"
    assert (
        detected_role_to_training_level("accs")
        != detected_role_to_training_level("intermediate")
    )


def test_accs_intermediate_dual_access_pins_intermediate_bucket():
    """Harris's dual access surfaces today as the single
    ``accs_intermediate`` role string and stores into ``INTERMEDIATE``.

    This is the current storage alias for dual access. It is **not** an
    assertion that ``accs_intermediate`` is a standalone Kaizen portfolio
    type. A future change that surfaces ACCS and Intermediate access
    separately for Harris will need to update this pin alongside the
    storage change.
    """
    from bot import detected_role_to_training_level

    assert detected_role_to_training_level("accs_intermediate") == "INTERMEDIATE"


def test_unknown_role_maps_to_none_so_setup_falls_through_to_picker():
    """An unrecognised detected role must return ``None`` so the setup
    flow falls through to the manual portfolio-profile picker, instead of
    silently storing a guessed bucket."""
    from bot import detected_role_to_training_level

    assert detected_role_to_training_level("") is None
    assert detected_role_to_training_level("unknown") is None
    assert detected_role_to_training_level(None) is None


# ─── Consultant / supervisor (Ahmed) UX continuity fallback ─────────────────


def test_assessor_role_maps_to_higher_bucket_for_ux_continuity_only():
    """Ahmed's consultant/supervisor account has no personal trainee portfolio.

    The bot's role detector returns ``"assessor"`` when MyTimeline shows the
    ``"You cannot create any events!"`` barrier. The setup/login path then
    maps that to the ``HIGHER`` ``training_level`` bucket so the UI keeps a
    coherent profile label rather than rendering ``Unknown``. The supervisor
    workflow keys off the raw ``"assessor"`` role string in ``profile_store``,
    **not** off the ``HIGHER`` bucket — keeping the two surfaces decoupled is
    what lets a future split surface a dedicated supervisor profile without
    leaking HST forms to a consultant.

    Pin both halves of that contract: the bucket is ``HIGHER`` today, but the
    raw role stays ``assessor``. A regression that lets ``store_kaizen_role``
    overwrite the bucket, or that silently drops the assessor fallback to
    ``None``, would be loud here.
    """
    from bot import detected_role_to_training_level

    assert detected_role_to_training_level("assessor") == "HIGHER"


def test_assessor_role_and_training_level_stay_decoupled(profile_store_module):
    """Storing the raw ``assessor`` role must not mutate ``training_level``,
    and storing ``HIGHER`` must not mutate the raw role. The supervisor
    workflow depends on the raw role staying ``assessor``.
    """
    ahmed_user_id = 9200006

    profile_store_module.store_training_level(ahmed_user_id, "HIGHER")
    profile_store_module.store_kaizen_role(ahmed_user_id, "assessor")

    assert profile_store_module.get_kaizen_role(ahmed_user_id) == "assessor"
    assert profile_store_module.get_training_level(ahmed_user_id) == "HIGHER"


# ─── store_kaizen_role preserves raw role per shape ─────────────────────────


@pytest.mark.parametrize("shape", SHAPES, ids=SHAPES)
def test_store_kaizen_role_preserves_raw_role_per_shape(
    profile_store_module, shape
):
    """``store_kaizen_role`` stores the detected role verbatim for every
    shape. Raw-role storage and the portfolio-profile bucket map are
    distinct surfaces; one must not mutate the other.
    """
    raw_role, _level = SHAPE_TO_ROLE_AND_LEVEL[shape]
    user_id = SHAPE_TO_USER_ID[shape]

    profile_store_module.store_kaizen_role(user_id, raw_role)

    assert profile_store_module.get_kaizen_role(user_id) == raw_role, (
        f"{shape}: raw role {raw_role!r} must round-trip without mutation"
    )


@pytest.mark.parametrize("shape", SHAPES, ids=SHAPES)
def test_store_kaizen_role_does_not_mutate_training_level_per_shape(
    profile_store_module, shape
):
    """Setting a Kaizen role must never touch the user's stored
    ``training_level``. Bucket choice belongs to the setup/login path's
    role map; raw-role storage is a separate surface.
    """
    raw_role, expected_level = SHAPE_TO_ROLE_AND_LEVEL[shape]
    user_id = SHAPE_TO_USER_ID[shape]

    profile_store_module.store_training_level(user_id, expected_level)
    profile_store_module.store_kaizen_role(user_id, raw_role)

    assert (
        profile_store_module.get_training_level(user_id) == expected_level
    ), (
        f"{shape}: store_kaizen_role must not overwrite training_level"
    )
    assert profile_store_module.get_kaizen_role(user_id) == raw_role


# ─── multi-user round-trip across all shapes, single DB ─────────────────────


def test_multi_shape_round_trip_does_not_cross_contaminate(
    profile_store_module,
):
    """Five users, one per shape, written into the same in-memory profile
    store. Each user's raw role and ``training_level`` must stay isolated
    — no last-write-wins, no shared-row collision, no silent overwrite of
    one shape's bucket by another's.
    """
    from bot import detected_role_to_training_level

    # Write phase — interleaved on purpose so a stray shared-row bug
    # would surface as a wrong final read for at least one shape.
    for shape in SHAPES:
        raw_role, expected_level = SHAPE_TO_ROLE_AND_LEVEL[shape]
        user_id = SHAPE_TO_USER_ID[shape]

        level = detected_role_to_training_level(raw_role)
        assert level == expected_level, (
            f"{shape}: helper returned {level!r}, expected {expected_level!r}"
        )

        profile_store_module.store_training_level(user_id, level)
        profile_store_module.store_kaizen_role(user_id, raw_role)

    # Read phase — every user keeps its own raw role and bucket.
    for shape in SHAPES:
        raw_role, expected_level = SHAPE_TO_ROLE_AND_LEVEL[shape]
        user_id = SHAPE_TO_USER_ID[shape]

        assert (
            profile_store_module.get_kaizen_role(user_id) == raw_role
        ), f"{shape}: raw role cross-contaminated to {profile_store_module.get_kaizen_role(user_id)!r}"
        assert (
            profile_store_module.get_training_level(user_id) == expected_level
        ), f"{shape}: training_level cross-contaminated to {profile_store_module.get_training_level(user_id)!r}"

    # Explicit ACCS vs Intermediate distinctness check on the persisted
    # rows — protects against a regression that writes the same row for
    # both shapes (e.g. a shared primary key bug).
    accs_uid = SHAPE_TO_USER_ID["accs"]
    int_uid = SHAPE_TO_USER_ID["intermediate"]
    assert (
        profile_store_module.get_training_level(accs_uid) == "ACCS"
    )
    assert (
        profile_store_module.get_training_level(int_uid) == "INTERMEDIATE"
    )
    assert profile_store_module.get_kaizen_role(accs_uid) == "accs"
    assert profile_store_module.get_kaizen_role(int_uid) == "intermediate"
