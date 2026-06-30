"""Focused tests for the Kaizen Portfolio Index v1 storage + conversion.

These tests are deliberately offline:
- The SQLite DB is redirected to a per-test tmp path via ``USAGE_DB_PATH``.
- No Kaizen / Playwright / CDP / credentials / live network.
- The implementing sprint slice owns the actual sync; this module owns the
  storage substrate it writes into.
"""

from __future__ import annotations

import importlib

import pytest

from health_models import EvidenceItem, HealthDomain


@pytest.fixture
def kaizen_index(tmp_path, monkeypatch):
    """Reload ``kaizen_index`` against an isolated SQLite path."""
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "kaizen_index_test.db"))
    import kaizen_index
    return importlib.reload(kaizen_index)


def _evidence_row(kaizen_index, **overrides):
    base = dict(
        id="event-uuid-1",
        user_id="42",
        surface="event",
        event_type="CBD",
        category="Assessments",
        state="complete",
        date_occurred_on="2026-05-20",
        end_date="2026-05-20",
        description="Resus case with senior support",
        linked_kc_tags=["Higher SLO1 KC1", "Higher SLO3 KC2"],
        filled_in_by="Trainee",
        filled_in_on="2026-05-21",
        parent_event_id=None,
        detail_url="https://kaizenep.com/events/view/event-uuid-1",
    )
    base.update(overrides)
    return kaizen_index.EvidenceItemRow(**base)


# ── Storage helpers ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_evidence_item_inserts_then_updates_same_row(kaizen_index):
    await kaizen_index.upsert_evidence_item(_evidence_row(kaizen_index))
    rows = await kaizen_index.list_evidence_items("42")
    assert len(rows) == 1
    assert rows[0].state == "complete"
    assert rows[0].linked_kc_tags == ["Higher SLO1 KC1", "Higher SLO3 KC2"]
    first_seen = rows[0].first_seen_at

    await kaizen_index.upsert_evidence_item(
        _evidence_row(kaizen_index, state="submitted", description="Updated note")
    )
    rows = await kaizen_index.list_evidence_items("42")
    assert len(rows) == 1
    assert rows[0].state == "submitted"
    assert rows[0].description == "Updated note"
    # first_seen_at is preserved; last_seen_at advances.
    assert rows[0].first_seen_at == first_seen
    assert rows[0].last_seen_at >= first_seen


@pytest.mark.asyncio
async def test_list_evidence_items_scopes_to_user(kaizen_index):
    await kaizen_index.upsert_evidence_item(_evidence_row(kaizen_index, id="a", user_id="1"))
    await kaizen_index.upsert_evidence_item(_evidence_row(kaizen_index, id="b", user_id="2"))
    rows_one = await kaizen_index.list_evidence_items("1")
    rows_two = await kaizen_index.list_evidence_items("2")
    assert [r.id for r in rows_one] == ["a"]
    assert [r.id for r in rows_two] == ["b"]


@pytest.mark.asyncio
async def test_count_evidence_items_returns_per_user_total(kaizen_index):
    await kaizen_index.upsert_evidence_item(_evidence_row(kaizen_index, id="a", user_id="9"))
    await kaizen_index.upsert_evidence_item(_evidence_row(kaizen_index, id="b", user_id="9"))
    await kaizen_index.upsert_evidence_item(_evidence_row(kaizen_index, id="c", user_id="10"))
    assert await kaizen_index.count_evidence_items("9") == 2
    assert await kaizen_index.count_evidence_items("10") == 1
    assert await kaizen_index.count_evidence_items("never-synced") == 0


# ── Index run lifecycle ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_index_run_lifecycle_records_start_and_finish(kaizen_index):
    run_id = await kaizen_index.start_index_run("42")
    assert isinstance(run_id, int) and run_id > 0

    running = await kaizen_index.latest_index_run("42")
    assert running is not None
    assert running.status == "running"
    assert running.finished_at is None

    await kaizen_index.finish_index_run(
        run_id,
        "ok",
        rows_seen=5,
        rows_written=4,
        rows_drifted=1,
        notes="ok",
    )
    finished = await kaizen_index.latest_index_run("42")
    assert finished is not None
    assert finished.id == run_id
    assert finished.status == "ok"
    assert finished.rows_seen == 5
    assert finished.rows_written == 4
    assert finished.rows_drifted == 1
    assert finished.finished_at is not None


@pytest.mark.asyncio
async def test_latest_index_run_returns_most_recent(kaizen_index):
    first = await kaizen_index.start_index_run("42")
    await kaizen_index.finish_index_run(first, "ok", rows_written=1)
    second = await kaizen_index.start_index_run("42")
    await kaizen_index.finish_index_run(second, "partial", rows_written=2, notes="auth dropped")

    latest = await kaizen_index.latest_index_run("42")
    assert latest is not None
    assert latest.id == second
    assert latest.status == "partial"
    assert latest.rows_written == 2
    assert latest.notes == "auth dropped"


@pytest.mark.asyncio
async def test_latest_index_run_is_none_when_unsynced(kaizen_index):
    assert await kaizen_index.latest_index_run("never-synced") is None


@pytest.mark.asyncio
async def test_get_kaizen_sync_status_bundles_run_and_count(kaizen_index):
    status = await kaizen_index.get_kaizen_sync_status("42")
    assert status.is_unset
    assert status.items_indexed == 0

    await kaizen_index.upsert_evidence_item(_evidence_row(kaizen_index))
    run_id = await kaizen_index.start_index_run("42")
    await kaizen_index.finish_index_run(run_id, "ok", rows_written=1)

    status = await kaizen_index.get_kaizen_sync_status("42")
    assert not status.is_unset
    assert status.last_run is not None
    assert status.last_run.status == "ok"
    assert status.items_indexed == 1


# ── Pure conversion ─────────────────────────────────────────────────────────


def test_evidence_row_to_health_item_maps_form_to_clinical_wpba(kaizen_index):
    row = _evidence_row(kaizen_index)
    item = kaizen_index.evidence_row_to_health_item(row)
    assert isinstance(item, EvidenceItem)
    assert item.domain == HealthDomain.clinical
    assert item.evidence_type == "wpba"
    assert item.form_type == "CBD"
    assert item.status == "filed"
    assert item.source == "kaizen_filed"
    assert item.source_ref == "https://kaizenep.com/events/view/event-uuid-1"


def test_evidence_row_to_health_item_maps_draft_surface_to_drafted_pg_draft(kaizen_index):
    row = _evidence_row(kaizen_index, surface="draft", state="pending", id="draft-1")
    item = kaizen_index.evidence_row_to_health_item(row)
    assert item.status == "drafted"
    assert item.source == "pg_draft"


def test_evidence_row_to_health_item_maps_qiat_to_qi_audit(kaizen_index):
    row = _evidence_row(kaizen_index, event_type="QIAT", state="complete", id="qiat-1")
    item = kaizen_index.evidence_row_to_health_item(row)
    assert item.domain == HealthDomain.qi
    assert item.evidence_type == "audit"
    assert item.status == "filed"


def test_evidence_row_to_health_item_handles_returned_for_amendment(kaizen_index):
    row = _evidence_row(
        kaizen_index, state="Returned for amendment", id="amend-1"
    )
    item = kaizen_index.evidence_row_to_health_item(row)
    assert item.status == "needs_work"


def test_evidence_rows_to_health_items_preserves_order(kaizen_index):
    rows = [
        _evidence_row(kaizen_index, id="a", event_type="CBD"),
        _evidence_row(kaizen_index, id="b", event_type="QIAT"),
        _evidence_row(kaizen_index, id="c", event_type="TEACH"),
    ]
    items = kaizen_index.evidence_rows_to_health_items(rows)
    assert [it.id for it in items] == ["a", "b", "c"]
    assert [it.domain for it in items] == [
        HealthDomain.clinical,
        HealthDomain.qi,
        HealthDomain.teaching,
    ]


def test_evidence_row_with_unknown_event_type_is_unclassified_not_clinical(kaizen_index):
    row = _evidence_row(kaizen_index, event_type="SOMETHING_NEW", id="x")
    item = kaizen_index.evidence_row_to_health_item(row)
    # Unknown event types must never default to clinical — they go to the
    # visible-but-unscored unclassified bucket.
    assert item.domain == HealthDomain.unclassified
    assert item.evidence_type == "other"
    # Preserve the unknown upstream code for audit/debug.
    assert item.form_type == "SOMETHING_NEW"


# ── Real Kaizen display-name canonicalisation ────────────────────────────────


@pytest.mark.parametrize(
    "label, form_type, domain, evidence_type",
    [
        ("DOPS - (ST3-ST6 - 2025 update)", "DOPS", HealthDomain.clinical, "wpba"),
        ("Mini-CEX (2025 Update)", "MINI_CEX", HealthDomain.clinical, "wpba"),
        ("CBD - Case Based Discussion (2025 update)", "CBD", HealthDomain.clinical, "wpba"),
        ("Procedural Log (ACCS)", "PROC_LOG", HealthDomain.clinical, "wpba"),
        ("Procedural Log - ST3-ST6 (2025 Update)", "PROC_LOG", HealthDomain.clinical, "wpba"),
        ("ACAT (2025 update)", "ACAT", HealthDomain.clinical, "wpba"),
        ("ACAF", "ACAF", HealthDomain.clinical, "wpba"),
        ("LAT", "LAT", HealthDomain.clinical, "wpba"),
        ("ESLE - Extended Supervised Learning Event", "ESLE", HealthDomain.clinical, "wpba"),
        ("Multi-Source Feedback (MSF)", "MSF", HealthDomain.clinical, "wpba"),
        ("Multiple Consultant Report (MCR)", "MCR", HealthDomain.clinical, "wpba"),
        ("QIAT - Quality Improvement Activity", "QIAT", HealthDomain.qi, "audit"),
        ("Audit Activity", "AUDIT", HealthDomain.qi, "audit"),
        ("Research Activity", "RESEARCH", HealthDomain.qi, "project"),
        ("EDU_ACT", "EDU_ACT", HealthDomain.cpd, "course"),
        ("FORMAL_COURSE", "FORMAL_COURSE", HealthDomain.cpd, "course"),
        ("Educational Activity Attended", "EDU_ACT", HealthDomain.cpd, "course"),
        ("Educational Meeting", "EDU_ACT", HealthDomain.cpd, "course"),
        ("RCEM Learning", "EDU_ACT", HealthDomain.cpd, "course"),
        ("Teaching Delivered By Trainee", "TEACH", HealthDomain.teaching, "teaching_session"),
        ("Teaching Observation (TO)", "TEACH_OBS", HealthDomain.teaching, "teaching_session"),
        ("STAT - Simulation Teaching", "STAT", HealthDomain.teaching, "teaching_session"),
        ("TEACH_CONFID", "TEACH_CONFID", HealthDomain.teaching, "teaching_session"),
        ("Reflective Practice Log", "REFLECT_LOG", HealthDomain.reflection, "reflection_log"),
        ("Self-directed Learning Reflection", "REFLECT_LOG", HealthDomain.reflection, "reflection_log"),
        ("PDP - Personal Development Plan", "PDP", HealthDomain.reflection, "reflection_log"),
        ("JCF Form", "JCF", HealthDomain.reflection, "reflection_log"),
        ("Complaint", "COMPLAINT", HealthDomain.leadership, "other"),
        ("Serious Incident Investigation", "SERIOUS_INCIDENT", HealthDomain.leadership, "other"),
        ("SERIOUS_INC", "SERIOUS_INCIDENT", HealthDomain.leadership, "other"),
        ("MGMT_REPORT", "MGMT_REPORT", HealthDomain.leadership, "other"),
        ("CLIN_GOV", "MGMT_REPORT", HealthDomain.leadership, "other"),
        ("Management and Leadership Activity", "MGMT_REPORT", HealthDomain.leadership, "other"),
        ("ARCP Form", "ARCP", HealthDomain.leadership, "other"),
        ("ESR - Educational Supervisor Report", "ESR", HealthDomain.leadership, "other"),
        ("End of Placement Report", "END_OF_PLACEMENT", HealthDomain.leadership, "other"),
        ("FEGS", "STR", HealthDomain.leadership, "other"),
    ],
)
def test_real_kaizen_labels_canonicalise(kaizen_index, label, form_type, domain, evidence_type):
    row = _evidence_row(kaizen_index, event_type=label, id=f"id-{label[:8]}")
    item = kaizen_index.evidence_row_to_health_item(row)
    assert item.form_type == form_type
    assert item.domain == domain
    assert item.evidence_type == evidence_type


def test_educational_supervisor_report_beats_generic_cpd_rule(kaizen_index):
    # "Educational Supervisor Report" contains "EDUCATIONAL" but must classify as
    # the supervisor/leadership report, not a CPD educational activity.
    row = _evidence_row(kaizen_index, event_type="Educational Supervisor Report", id="esr-1")
    item = kaizen_index.evidence_row_to_health_item(row)
    assert item.form_type == "ESR"
    assert item.domain == HealthDomain.leadership


def test_file_upload_surface_is_unclassified_file_source(kaizen_index):
    row = _evidence_row(
        kaizen_index, surface="file", event_type="Some Uploaded Certificate.pdf", id="file-1"
    )
    item = kaizen_index.evidence_row_to_health_item(row)
    assert item.domain == HealthDomain.unclassified
    assert item.evidence_type == "other"
    assert item.source == "file_upload"
    assert item.form_type is None


def test_document_upload_label_is_unclassified(kaizen_index):
    row = _evidence_row(kaizen_index, event_type="Document Upload", id="doc-1")
    item = kaizen_index.evidence_row_to_health_item(row)
    assert item.domain == HealthDomain.unclassified
    assert item.form_type is None


# ── KC-tag → SLO coverage derivation ─────────────────────────────────────────


@pytest.mark.parametrize(
    "tag, expected",
    [
        ("Higher SLO1 KC1", {1}),
        ("SLO 12", {12}),
        ("Higher SLO8 KC2", {8}),
        ("SLO1 KC1: chest pain assessment", {1}),
        ("SLO01 KC3", {1}),
        ("not a curriculum tag", set()),
        ("SLO13 KC1", set()),  # outside the SLO1–12 EM curriculum range
        ("", set()),
    ],
)
def test_slo_numbers_from_kc_tag(kaizen_index, tag, expected):
    assert kaizen_index.slo_numbers_from_kc_tag(tag) == expected


def test_slo_coverage_from_evidence_rows_aggregates_across_rows(kaizen_index):
    rows = [
        _evidence_row(kaizen_index, id="a", linked_kc_tags=["Higher SLO1 KC1", "Higher SLO3 KC2"]),
        _evidence_row(kaizen_index, id="b", linked_kc_tags=["SLO 12"]),
        _evidence_row(kaizen_index, id="c", linked_kc_tags=[]),
    ]
    assert kaizen_index.slo_coverage_from_evidence_rows(rows) == {1, 3, 12}
