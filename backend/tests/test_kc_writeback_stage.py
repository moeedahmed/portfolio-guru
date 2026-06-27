"""Regression tests for Key Capability (KC) writeback stage resolution.

Bug: trainees on a non-Higher portfolio (Intermediate / ACCS / PEM) had their
Key Capabilities silently not ticked on Kaizen forms that carry a curriculum
tree but no in-form ``stage_of_training`` selector (Reflective Practice Log,
ESLE Reflection, Teaching Observation, LAT). The shared curriculum-link writer
defaulted the SLO-tree stage prefix to "Higher", so SLO accordions for an
Intermediate trainee never expanded and no KC checkbox was reachable.

These tests pin the shared resolution layer:
  * ``_curriculum_stage_label`` resolves the stage from explicit fields first,
    then the user's profile training level, then a safe "Higher" default.
  * ``_fill_curriculum_links`` builds the SLO-expansion text from that stage,
    so an Intermediate trainee expands "Intermediate SLO3:" not "Higher SLO3:".
  * ``file_to_kaizen`` surfaces the KC tick result instead of discarding it.
"""
import asyncio
import os
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import kaizen_form_filer
from kaizen_form_filer import (
    _curriculum_stage_label,
    _fill_curriculum_for_form,
    _fill_curriculum_links,
    _uses_tag_based_curriculum,
    _verify_filing_qa,
    file_to_kaizen,
)


# ─── _curriculum_stage_label ────────────────────────────────────────────────


def test_explicit_stage_field_wins_over_profile(monkeypatch):
    """A form-supplied stage value is authoritative and never overridden."""
    monkeypatch.setattr(
        "profile_store.get_training_level", lambda uid: "INTERMEDIATE"
    )
    label = _curriculum_stage_label(
        {"stage_of_training": "Higher/ST4-ST6"}, telegram_user_id=42
    )
    assert label == "Higher/ST4-ST6"


def test_stageless_form_falls_back_to_profile_intermediate(monkeypatch):
    """REFLECT_LOG has no stage field — the Intermediate profile must reach the tree."""
    monkeypatch.setattr(
        "profile_store.get_training_level", lambda uid: "INTERMEDIATE"
    )
    label = _curriculum_stage_label({}, telegram_user_id=42)
    assert "intermediate" in label.lower()


@pytest.mark.parametrize(
    "level,expected",
    [
        ("HIGHER", "Higher"),
        ("INTERMEDIATE", "Intermediate"),
        ("ACCS", "ACCS"),
        ("PEM", "PEM"),
        ("ST3", "Intermediate"),
        ("ST5", "Higher"),
    ],
)
def test_profile_levels_map_to_tree_prefix(monkeypatch, level, expected):
    monkeypatch.setattr("profile_store.get_training_level", lambda uid: level)
    assert _curriculum_stage_label({}, telegram_user_id=7) == expected


def test_unknown_profile_defaults_to_higher(monkeypatch):
    """Unknown / SAS / missing profile keeps the historical safe default."""
    monkeypatch.setattr("profile_store.get_training_level", lambda uid: None)
    assert _curriculum_stage_label({}, telegram_user_id=7) == "Higher"


def test_no_user_id_defaults_to_higher():
    assert _curriculum_stage_label({}, telegram_user_id=None) == "Higher"


# ─── _fill_curriculum_links uses the resolved stage prefix ──────────────────


class _RecordingPage:
    """Fake Playwright page that records evaluate() calls and reports success."""

    def __init__(self):
        self.expanded_texts = []
        self.ticked_targets = []

    async def evaluate(self, js, arg=None):
        if js is kaizen_form_filer.EXPAND_SLO_JS or js is kaizen_form_filer.EXPAND_SLO_FALLBACK_JS:
            self.expanded_texts.append(arg)
            return True
        if js is kaizen_form_filer.TICK_KC_JS:
            self.ticked_targets.append(arg)
            return {"found": True, "checked": True, "text": arg}
        if js is kaizen_form_filer.TICK_KC_FALLBACK_JS:
            return {"found": False}
        return None


@pytest.mark.asyncio
async def test_intermediate_stage_expands_intermediate_tree(monkeypatch):
    monkeypatch.setattr(kaizen_form_filer.asyncio, "sleep", AsyncMock())
    page = _RecordingPage()
    ticked, errors = await _fill_curriculum_links(
        page,
        slo_codes=["SLO3", "SLO6"],
        kc_targets=["SLO3 KC2: be expert in fluid management (2025 Update)"],
        stage_label="Intermediate/ST3",
    )
    assert any(t.startswith("Intermediate SLO3") for t in page.expanded_texts)
    assert not any(t.startswith("Higher SLO") for t in page.expanded_texts)
    assert ticked == ["SLO3 KC2: be expert in fluid management (2025 Update)"]
    assert errors == []


@pytest.mark.asyncio
async def test_higher_stage_still_expands_higher_tree(monkeypatch):
    monkeypatch.setattr(kaizen_form_filer.asyncio, "sleep", AsyncMock())
    page = _RecordingPage()
    await _fill_curriculum_links(
        page,
        slo_codes=["SLO6"],
        kc_targets=["SLO6 KC1: identify when key EM skills are indicated (2025 Update)"],
        stage_label="Higher/ST4-ST6",
    )
    assert page.expanded_texts == ["Higher SLO6:"]


# ─── _uses_tag_based_curriculum routing classification ──────────────────────
#
# Forms whose curriculum lives ONLY in the Add tags modal (no usable in-form KC
# tree) MUST route through the tag path. If they fall through to the in-form
# tree writer, it ticks nothing yet reports success — silently dropping the
# trainee's curriculum evidence. CBD/DOPS/PROC_LOG were live-verified as
# modal-only (2026-04-22/23, commit 395393e) but were regressed by a 2026-05-22
# file recovery; these tests pin them so the regression cannot recur.


@pytest.mark.parametrize(
    "form_type",
    [
        # Live-verified modal-only WPBA / procedural forms (the regression).
        "CBD", "DOPS", "PROC_LOG",
        # 2021 variants must inherit their base form's routing.
        "CBD_2021", "DOPS_2021", "PROC_LOG_2021",
        # Reflective entry verified tag-based (schema flag + set).
        "REFLECT_LOG", "REFLECT_LOG_2021",
        # Management / governance family — no in-form curriculum tree.
        "CRIT_INCIDENT", "CLIN_GOV", "MGMT_PROJECT", "MGMT_ROTA",
        "MGMT_RISK", "MGMT_REPORT", "APPRAISAL", "BUSINESS_CASE",
        "COST_IMPROVE", "EQUIP_SERVICE", "COMPLAINT", "SERIOUS_INC",
    ],
)
def test_tag_based_forms_route_through_add_tags(form_type):
    assert _uses_tag_based_curriculum(form_type) is True


@pytest.mark.parametrize(
    "form_type",
    [
        # US_CASE has a genuine inline kz-tree where KCs must be ticked to count
        # as curriculum evidence (verified 2026-04-23) — never route via tags.
        "US_CASE", "US_CASE_2021",
        # TEACH (2025 Update) carries its own in-form curriculum tree; the
        # schema flag tag_based_curriculum=False pins this deliberately.
        "TEACH", "TEACH_2021",
    ],
)
def test_inline_tree_forms_do_not_route_through_add_tags(form_type):
    assert _uses_tag_based_curriculum(form_type) is False


def test_schema_flag_overrides_default_set(monkeypatch):
    """An explicit tag_based_curriculum flag is authoritative over the set."""
    # Flag wins when the form is NOT in the default set.
    monkeypatch.setitem(
        kaizen_form_filer.FORM_SCHEMAS, "US_CASE", {"tag_based_curriculum": True}
    )
    assert _uses_tag_based_curriculum("US_CASE") is True
    # Flag wins when the form IS in the default set.
    monkeypatch.setitem(
        kaizen_form_filer.FORM_SCHEMAS, "CBD", {"tag_based_curriculum": False}
    )
    assert _uses_tag_based_curriculum("CBD") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("form_type", ["CBD", "DOPS", "PROC_LOG"])
async def test_modal_only_forms_dispatch_to_tag_filler(monkeypatch, form_type):
    """Restored modal-only forms reach _fill_curriculum_tags, not the tree writer."""
    page = MagicMock()
    tag_fill = AsyncMock(return_value=(["SLO3 KC3"], []))
    in_form_fill = AsyncMock(return_value=([], ["wrong route"]))
    monkeypatch.setattr(kaizen_form_filer, "_fill_curriculum_tags", tag_fill)
    monkeypatch.setattr(kaizen_form_filer, "_fill_curriculum_links", in_form_fill)

    ticked, errors = await _fill_curriculum_for_form(
        page, form_type, ["SLO3"], ["SLO3 KC3"], "Higher"
    )

    assert ticked == ["SLO3 KC3"]
    tag_fill.assert_awaited_once()
    in_form_fill.assert_not_awaited()


@pytest.mark.asyncio
async def test_reflect_log_routes_curriculum_through_tag_modal(monkeypatch):
    """Reflective Practice Log has no reliable in-form KC tree; use Add Tags."""
    page = MagicMock()
    tag_fill = AsyncMock(return_value=(["SLO3 KC3"], []))
    in_form_fill = AsyncMock(return_value=([], ["wrong route"]))
    monkeypatch.setattr(kaizen_form_filer, "_fill_curriculum_tags", tag_fill)
    monkeypatch.setattr(kaizen_form_filer, "_fill_curriculum_links", in_form_fill)

    ticked, errors = await _fill_curriculum_for_form(
        page,
        "REFLECT_LOG",
        ["SLO3"],
        ["SLO3 KC3"],
        "Higher",
    )

    assert ticked == ["SLO3 KC3"]
    assert errors == []
    tag_fill.assert_awaited_once()
    in_form_fill.assert_not_awaited()


@pytest.mark.asyncio
async def test_tag_based_qa_reads_tag_count_not_kc_checkbox(monkeypatch):
    """Saved tag-based forms should not be reported as kc_not_ticked."""
    page = MagicMock()
    page.url = "https://kaizenep.com/events/fillin/test"

    async def fake_evaluate(js, arg=None):
        if js is kaizen_form_filer.TAG_COUNT_JS:
            return 1
        if js is kaizen_form_filer._QA_READ_KC_JS:
            raise AssertionError("tag-based QA must not read in-form KC checkboxes")
        return None

    page.evaluate = AsyncMock(side_effect=fake_evaluate)

    result = await _verify_filing_qa(
        page,
        "REFLECT_LOG",
        {
            "key_capabilities": [
                "SLO3 KC3: manage life-threatening conditions (2025 Update)"
            ]
        },
        {},
    )

    assert result["gaps"] == []
    assert any(item.startswith("tag:SLO3 KC3") for item in result["filled"])


# ─── file_to_kaizen surfaces (does not discard) the KC result ───────────────


@pytest.mark.asyncio
async def test_reflect_log_intermediate_ticks_kcs_and_reports(monkeypatch):
    """End-to-end through file_to_kaizen: an Intermediate REFLECT_LOG must
    resolve the Intermediate tree and report the ticked KCs in `filled`."""
    monkeypatch.setattr(kaizen_form_filer.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        "profile_store.get_training_level", lambda uid: "INTERMEDIATE"
    )

    captured = {}

    async def fake_fill_curriculum_for_form(page, form_type, slo_codes, kc_targets, stage_label):
        captured["form_type"] = form_type
        captured["stage_label"] = stage_label
        captured["kc_targets"] = list(kc_targets)
        return list(kc_targets), []

    monkeypatch.setattr(
        kaizen_form_filer, "_fill_curriculum_for_form", fake_fill_curriculum_for_form
    )
    monkeypatch.setattr(
        kaizen_form_filer, "connect_cdp_browser", AsyncMock(return_value=(MagicMock(), MagicMock()))
    )
    monkeypatch.setattr(kaizen_form_filer, "KAIZEN_USE_CDP", True)
    monkeypatch.setattr(kaizen_form_filer, "_login", AsyncMock(return_value=True))
    monkeypatch.setattr(kaizen_form_filer, "use_cached_session", AsyncMock(return_value=False))
    monkeypatch.setattr(kaizen_form_filer, "save_session_state", AsyncMock())
    monkeypatch.setattr(kaizen_form_filer, "_fill_field_legacy", AsyncMock(return_value=True))
    monkeypatch.setattr(kaizen_form_filer, "_save_form", AsyncMock(return_value=True))
    monkeypatch.setattr(kaizen_form_filer, "_verify_entry_saved", AsyncMock(return_value=True))
    monkeypatch.setattr(kaizen_form_filer, "_verify_filing_qa", AsyncMock(return_value=None))

    page = MagicMock()
    page.goto = AsyncMock()
    page.url = "https://kaizenep.com/events/new-section/uuid"
    page.locator = MagicMock(return_value=MagicMock(count=AsyncMock(return_value=0)))
    page.evaluate = AsyncMock(return_value=None)
    page.inner_text = AsyncMock(return_value="")
    monkeypatch.setattr(
        kaizen_form_filer, "connect_cdp_browser", AsyncMock(return_value=(page, MagicMock()))
    )

    kcs = ["SLO3 KC2: be expert in fluid management (2025 Update)"]
    result = await file_to_kaizen(
        form_type="REFLECT_LOG",
        fields={
            "reflection": "I managed a shocked patient and reflected on fluid strategy.",
            "key_capabilities": kcs,
            "curriculum_links": ["SLO3"],
        },
        username="u",
        password="p",
        telegram_user_id=42,
    )

    # Resolved the Intermediate tree from the profile, not the "Higher" default.
    assert captured["form_type"] == "REFLECT_LOG"
    assert "intermediate" in captured["stage_label"].lower()
    assert captured["kc_targets"] == kcs
    # KC result is surfaced, not silently discarded.
    assert any("curriculum_links" in f for f in result["filled"])
