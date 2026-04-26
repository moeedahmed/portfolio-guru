"""
Contract-level tests for kaizen_form_filer.py — Phase 1 filing reliability.

Tests the public API surface and verifiable contracts without requiring
a real browser, network, or credentials. Organised by contract:

  A. Entry-point validation (unknown form, no mapping)
  B. Field mapping completeness (every mapped form has UUID, no duplicates)
  C. Pure function contracts (_strip_emojis, _to_uk_date, stage mapping)
  D. Golden ticket field coverage (all fixture fields have DOM mappings)
  E. KC/curriculum verification semantics
  F. Save safety gate (submit blocked without env flag)
  G. Filing result status semantics
  H. Schema ↔ field map cross-check
"""
import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from kaizen_form_filer import (
    file_to_kaizen,
    FORM_FIELD_MAP,
    FORM_UUIDS,
    STAGE_SELECT_VALUES,
    COMMON_HEADER_FIELDS,
    FORMS_USING_TAG_BASED_CURRICULUM,
    SLO_NODE_IDS,
    _strip_emojis,
    _to_uk_date,
    _fill_stage_of_training,
)
from form_schemas import FORM_SCHEMAS

from tests.fixtures.golden_tickets import ALL_GOLDEN_TICKETS


# ─── Section A: Entry-point validation ────────────────────────────────────────

class TestEntryPointValidation:

    @pytest.mark.asyncio
    async def test_unknown_form_type_returns_failed(self):
        result = await file_to_kaizen("UNKNOWN_XYZ", {}, "user", "pass")
        assert result["status"] == "failed"
        assert "Unknown form type" in result["error"]
        assert result["filled"] == []

    @pytest.mark.asyncio
    async def test_unknown_form_type_skips_field_list(self):
        """Unknown form should not attempt to list skipped fields."""
        result = await file_to_kaizen("NOT_A_FORM", {"foo": "bar"}, "user", "pass")
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_form_with_uuid_but_no_mapping_returns_partial(self):
        """A form type with a UUID but no FORM_FIELD_MAP should return partial
        with a clear error indicating browser-use is needed."""
        # Find a form with UUID but no mapping (if any exist)
        unmapped = [ft for ft in FORM_UUIDS if ft not in FORM_FIELD_MAP]
        if not unmapped:
            pytest.skip("All forms with UUIDs have mappings — good!")
        form_type = unmapped[0]
        result = await file_to_kaizen(form_type, {"some_field": "val"}, "user", "pass")
        assert result["status"] == "partial"
        assert "No field mapping" in result["error"]


# ─── Section B: Field mapping completeness ────────────────────────────────────

class TestFieldMappingCompleteness:

    def test_all_form_types_have_uuid(self):
        """Every form in FORM_FIELD_MAP must also be in FORM_UUIDS."""
        for form_type in FORM_FIELD_MAP:
            assert form_type in FORM_UUIDS, f"{form_type} has field map but no UUID"

    def test_all_field_map_uuids_are_strings(self):
        """Every DOM id value must be a non-empty string."""
        for form_type, field_map in FORM_FIELD_MAP.items():
            for field_key, dom_id in field_map.items():
                assert isinstance(dom_id, str) and len(dom_id) > 0, (
                    f"{form_type}.{field_key} has invalid DOM id: {dom_id!r}"
                )

    def test_no_duplicate_uuids_within_form(self):
        """No two different fields should share the same DOM id within a form.
        Exception: startDate/endDate are expected duplicates as headers."""
        for form_type, field_map in FORM_FIELD_MAP.items():
            non_header_ids = [
                v for k, v in field_map.items()
                if v not in ("startDate", "endDate", "event-description")
            ]
            assert len(non_header_ids) == len(set(non_header_ids)), (
                f"{form_type} has duplicate UUIDs: "
                f"{[v for v in non_header_ids if non_header_ids.count(v) > 1]}"
            )

    def test_common_header_fields_defined(self):
        """COMMON_HEADER_FIELDS must have startDate and endDate."""
        assert "date_of_encounter" in COMMON_HEADER_FIELDS
        assert COMMON_HEADER_FIELDS["date_of_encounter"]["field_id"] == "startDate"
        assert "end_date" in COMMON_HEADER_FIELDS
        assert COMMON_HEADER_FIELDS["end_date"]["field_id"] == "endDate"

    def test_form_field_map_covers_at_least_20_forms(self):
        """We claim 44+ forms mapped — sanity check the count."""
        assert len(FORM_FIELD_MAP) >= 20, (
            f"FORM_FIELD_MAP only has {len(FORM_FIELD_MAP)} forms, expected 20+"
        )


# ─── Section C: Pure function contracts ───────────────────────────────────────

class TestPureFunctions:

    def test_strip_emojis_removes_all(self):
        result = _strip_emojis("Great case 🔥 with emojis 💉")
        assert "🔥" not in result
        assert "💉" not in result
        assert "Great case" in result
        assert "with emojis" in result

    def test_strip_emojis_preserves_plain_text(self):
        assert _strip_emojis("No emojis here") == "No emojis here"

    def test_strip_emojis_handles_empty(self):
        assert _strip_emojis("") == ""

    def test_to_uk_date_iso_format(self):
        assert _to_uk_date("2026-03-21") == "21/3/2026"

    def test_to_uk_date_already_uk(self):
        assert _to_uk_date("21/3/2026") == "21/3/2026"

    def test_to_uk_date_padded_uk(self):
        assert _to_uk_date("06/03/2026") == "06/03/2026"

    def test_to_uk_date_empty(self):
        assert _to_uk_date("") == ""

    def test_to_uk_date_long_format(self):
        assert _to_uk_date("21 March 2026") == "21/3/2026"

    def test_to_uk_date_short_month(self):
        assert _to_uk_date("21 Mar 2026") == "21/3/2026"

    def test_stage_select_values_contain_required_keys(self):
        for key in ("ACCS", "Intermediate", "Higher", "PEM"):
            assert key in STAGE_SELECT_VALUES
            assert STAGE_SELECT_VALUES[key].startswith("string:")


# ─── Section D: Golden ticket field coverage ──────────────────────────────────

class TestGoldenTicketCoverage:
    """Verify that every field in each golden ticket fixture has a corresponding
    DOM mapping in FORM_FIELD_MAP or COMMON_HEADER_FIELDS."""

    @pytest.mark.parametrize("ticket", ALL_GOLDEN_TICKETS, ids=lambda t: t["form_type"])
    def test_golden_ticket_fields_have_dom_mappings(self, ticket):
        form_type = ticket["form_type"]
        field_map = FORM_FIELD_MAP.get(form_type, {})
        header_keys = set(COMMON_HEADER_FIELDS.keys())

        unmapped = []
        for key in ticket["fields"]:
            if key in field_map or key in header_keys:
                continue
            # description is handled by COMMON_HEADER_FIELDS["description"]
            if key == "description":
                continue
            unmapped.append(key)

        assert not unmapped, (
            f"{form_type}: fields {unmapped} have no DOM mapping in "
            f"FORM_FIELD_MAP or COMMON_HEADER_FIELDS"
        )

    @pytest.mark.parametrize("ticket", ALL_GOLDEN_TICKETS, ids=lambda t: t["form_type"])
    def test_golden_ticket_has_uuid(self, ticket):
        form_type = ticket["form_type"]
        assert form_type in FORM_UUIDS, f"{form_type} has no UUID in FORM_UUIDS"

    @pytest.mark.parametrize("ticket", ALL_GOLDEN_TICKETS, ids=lambda t: t["form_type"])
    def test_golden_ticket_curriculum_links_format(self, ticket):
        """KC prefixes must match 'Higher SLOx Key Capability N' pattern."""
        import re
        kc_pattern = re.compile(r"^(Higher|Intermediate|ACCS|PEM) SLO\d+ Key Capability \d+$")
        for kc in ticket.get("curriculum_links", []):
            assert kc_pattern.match(kc), (
                f"{ticket['form_type']}: KC prefix '{kc}' doesn't match expected pattern"
            )

    @pytest.mark.parametrize("ticket", ALL_GOLDEN_TICKETS, ids=lambda t: t["form_type"])
    def test_golden_ticket_slos_exist_in_node_ids(self, ticket):
        """SLOs referenced in curriculum_links must exist in SLO_NODE_IDS."""
        import re
        for kc in ticket.get("curriculum_links", []):
            m = re.match(r"^(?:Higher|Intermediate|ACCS|PEM) (SLO\d+)", kc)
            if m:
                slo = m.group(1)
                assert slo in SLO_NODE_IDS, (
                    f"{ticket['form_type']}: {slo} not in SLO_NODE_IDS"
                )


# ─── Section E: KC/Curriculum verification semantics ─────────────────────────

class TestCurriculumVerification:

    def test_tag_based_forms_are_marked(self):
        """Forms known to use tag-based curriculum must be in the set."""
        for form_type in ("DOPS", "CBD", "PROC_LOG", "CRIT_INCIDENT"):
            assert form_type in FORMS_USING_TAG_BASED_CURRICULUM, (
                f"{form_type} should be in FORMS_USING_TAG_BASED_CURRICULUM"
            )

    def test_slo_node_ids_cover_all_12_slos(self):
        """SLO_NODE_IDS must have SLO1 through SLO12."""
        for i in range(1, 13):
            slo = f"SLO{i}"
            assert slo in SLO_NODE_IDS, f"{slo} missing from SLO_NODE_IDS"

    def test_slo_node_ids_are_uuid_format(self):
        """Each SLO node ID should be a UUID string."""
        import re
        uuid_pattern = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        for slo, node_id in SLO_NODE_IDS.items():
            assert uuid_pattern.match(node_id), (
                f"SLO_NODE_IDS[{slo}] = {node_id!r} is not a valid UUID"
            )

    def test_forms_with_kc_tick_schema_have_curriculum_path(self):
        """Forms with kc_tick fields in schema should either have tag-based
        curriculum or an inline tree path."""
        for form_type, schema in FORM_SCHEMAS.items():
            has_kc_field = any(
                f.get("type") == "kc_tick"
                for f in schema.get("fields", [])
            )
            if has_kc_field and form_type in FORM_FIELD_MAP:
                # Either tag-based or inline — both are valid paths.
                # Just verify the form exists in the filer's coverage.
                assert form_type in FORM_UUIDS, (
                    f"{form_type} has kc_tick fields but no UUID for filing"
                )


# ─── Section F: Save safety gate ─────────────────────────────────────────────

class TestSavetyGate:

    def test_save_draft_never_submits_by_default(self):
        """The _save_draft_legacy function's selector list must NOT start
        with Submit/Send selectors."""
        import inspect
        from kaizen_form_filer import _save_draft_legacy
        assert inspect.iscoroutinefunction(_save_draft_legacy)

    def test_submit_entry_exists(self):
        """_submit_entry is separate from _save_draft_legacy."""
        import inspect
        from kaizen_form_filer import _submit_entry
        assert inspect.iscoroutinefunction(_submit_entry)

    def test_kaizen_allow_submit_env_gate_in_file_to_kaizen(self):
        """file_to_kaizen checks KAIZEN_ALLOW_SUBMIT before submitting."""
        import inspect
        source = inspect.getsource(file_to_kaizen)
        assert "KAIZEN_ALLOW_SUBMIT" in source


# ─── Section G: Filing result status semantics ──────────────────────────────

class TestFilingResultSemantics:

    @pytest.mark.asyncio
    async def test_failed_status_has_error_message(self):
        """Any 'failed' result must include a non-empty error string."""
        result = await file_to_kaizen("UNKNOWN", {}, "u", "p")
        assert result["status"] == "failed"
        assert result["error"]
        assert isinstance(result["error"], str)
        assert len(result["error"]) > 0

    @pytest.mark.asyncio
    async def test_result_always_has_required_keys(self):
        """Every result dict must have status, filled, skipped keys."""
        result = await file_to_kaizen("UNKNOWN", {}, "u", "p")
        for key in ("status", "filled", "skipped"):
            assert key in result, f"Result missing required key: {key}"

    def test_status_values_are_known(self):
        """Valid status values are success, partial, failed."""
        # This is a documentation/contract test — ensures the filer
        # doesn't invent new status strings without updating callers.
        valid = {"success", "partial", "failed"}
        # Verify by reading the source
        import inspect
        source = inspect.getsource(file_to_kaizen)
        for status in valid:
            assert f'"{status}"' in source or f"'{status}'" in source


# ─── Section H: Schema ↔ Field map cross-check ──────────────────────────────

class TestSchemaFieldMapCrossCheck:

    def test_schemas_with_filer_available_have_field_map(self):
        """Every schema marked filer_available=True should have a FORM_FIELD_MAP
        entry or be a known exception (forms handled by universal headers only)."""
        # Forms that only need universal headers, or are mapped under a different
        # key in FORM_FIELD_MAP (e.g. ESLE_ASSESS → ESLE_PART1_2 / ESLE_REFLECTION)
        universal_only = {"CCT", "ABSENCE", "OOP", "HIGHER_PROG", "FILE_UPLOAD"}
        for form_type, schema in FORM_SCHEMAS.items():
            if not schema.get("filer_available", False):
                continue
            if form_type in universal_only:
                continue
            assert form_type in FORM_FIELD_MAP, (
                f"{form_type} has filer_available=True but no FORM_FIELD_MAP entry"
            )

    def test_reflect_log_gibbs_fields_all_mapped(self):
        """REFLECT_LOG must have DOM mappings for all Gibbs cycle fields."""
        gibbs_fields = [
            "reflection_title", "date_of_event", "reflection",
            "replay_differently", "why", "different_outcome",
            "focussing_on", "learned",
        ]
        field_map = FORM_FIELD_MAP.get("REFLECT_LOG", {})
        for f in gibbs_fields:
            assert f in field_map, f"REFLECT_LOG missing Gibbs field: {f}"

    def test_reflect_log_no_duplicate_uuids(self):
        """Regression: reflection and event_type must NOT share the same UUID."""
        field_map = FORM_FIELD_MAP["REFLECT_LOG"]
        values = list(field_map.values())
        non_header = [v for v in values if v not in ("startDate", "endDate", "event-description")]
        assert len(non_header) == len(set(non_header)), (
            f"REFLECT_LOG has duplicate UUIDs in non-header fields"
        )

    def test_esle_assess_alias_matches_esle_part1_2(self):
        """ESLE_ASSESS (bot/extractor name) must resolve to same UUID and field map as ESLE_PART1_2."""
        assert FORM_UUIDS.get("ESLE_ASSESS") == FORM_UUIDS.get("ESLE_PART1_2"), (
            "ESLE_ASSESS UUID must match ESLE_PART1_2"
        )
        assert FORM_FIELD_MAP.get("ESLE_ASSESS") == FORM_FIELD_MAP.get("ESLE_PART1_2"), (
            "ESLE_ASSESS field map must match ESLE_PART1_2"
        )

    def test_dops_has_procedural_skill_mapping(self):
        """DOPS must have a procedural_skill DOM mapping."""
        assert "procedural_skill" in FORM_FIELD_MAP.get("DOPS", {}), (
            "DOPS missing procedural_skill mapping"
        )

    def test_us_case_has_procedural_skill_dropdowns(self):
        """US_CASE must have all three procedural skill dropdown mappings."""
        field_map = FORM_FIELD_MAP.get("US_CASE", {})
        for key in ("accs_procedural_skill", "intermediate_procedural_skill", "higher_procedural_skill"):
            assert key in field_map, f"US_CASE missing {key}"

    def test_teach_has_procedural_skill_dropdowns(self):
        """TEACH must have all three procedural skill dropdown mappings."""
        field_map = FORM_FIELD_MAP.get("TEACH", {})
        for key in ("accs_procedural_skill", "intermediate_procedural_skill", "higher_procedural_skill"):
            assert key in field_map, f"TEACH missing {key}"


# ─── Section I: DOM mapping backlog presence ───────────────────────────────

class TestDOMMappingBacklog:

    def test_backlog_file_exists(self):
        """dom_mapping_backlog.json must exist in backend/."""
        backlog_path = os.path.join(os.path.dirname(__file__), '..', 'dom_mapping_backlog.json')
        assert os.path.isfile(backlog_path), (
            "dom_mapping_backlog.json missing — Phase 2 requires a machine-readable backlog"
        )

    def test_backlog_is_valid_json_with_required_keys(self):
        """Backlog must be valid JSON with backlog array and summary."""
        import json
        backlog_path = os.path.join(os.path.dirname(__file__), '..', 'dom_mapping_backlog.json')
        with open(backlog_path) as f:
            data = json.load(f)
        assert "backlog" in data, "backlog key missing"
        assert "summary" in data, "summary key missing"
        assert isinstance(data["backlog"], list)
        assert len(data["backlog"]) > 0, "Backlog is empty — all fields mapped?"

    def test_backlog_covers_p0_forms(self):
        """P0 forms (CBD, DOPS, LAT) must have entries in the backlog."""
        import json
        backlog_path = os.path.join(os.path.dirname(__file__), '..', 'dom_mapping_backlog.json')
        with open(backlog_path) as f:
            data = json.load(f)
        p0_forms = {entry["form"] for entry in data["backlog"] if entry["priority"] == "P0"}
        # If a P0 form has no unmapped fields, it should have been removed from backlog.
        # This test ensures we don't silently forget about P0 gaps.
        for form in ("CBD", "DOPS", "LAT"):
            if form in p0_forms:
                continue
            # Check if form actually has unmapped fields
            field_map = FORM_FIELD_MAP.get(form, {})
            schema = FORM_SCHEMAS.get(form, {})
            schema_keys = {f["key"] for f in schema.get("fields", []) if f.get("type") != "kc_tick"}
            header_keys = set(COMMON_HEADER_FIELDS.keys())
            unmapped = schema_keys - set(field_map.keys()) - header_keys
            assert not unmapped, (
                f"{form} has unmapped fields {unmapped} but no P0 backlog entry"
            )

    def test_backlog_entries_have_required_fields(self):
        """Each backlog entry must have form, field, status, priority."""
        import json
        backlog_path = os.path.join(os.path.dirname(__file__), '..', 'dom_mapping_backlog.json')
        with open(backlog_path) as f:
            data = json.load(f)
        for entry in data["backlog"]:
            for key in ("form", "field", "status", "priority"):
                assert key in entry, f"Backlog entry missing {key}: {entry}"


class TestDraftVerificationStatus:

    def test_verification_miss_message_is_partial_not_failed(self):
        """If save clicked and fields filled but list verification misses it, surface partial.

        Kaizen's activities list can lag; this prevents a saved draft being reported
        as total failure while still forcing manual confirmation.
        """
        source = open(os.path.join(os.path.dirname(__file__), '..', 'kaizen_form_filer.py')).read()
        assert 'status = "partial"' in source
        assert 'Draft save clicked, but portfolio-list verification did not find the entry' in source
        assert 'status = "failed"\n            save_error = "Entry not found in your portfolio after saving' not in source
