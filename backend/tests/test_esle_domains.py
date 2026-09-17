"""ESLE "Domains of performance" — derivation, widget filling, and the
required-field guard that stops a blank required answer being reported as a
clean save.

All synthetic data. No browser, no network, no credentials.
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from esle_domains import (  # noqa: E402
    ALL_DOMAINS,
    DECISION_MAKING,
    ESLE_DOMAIN_OPTIONS,
    MANAGEMENT_AND_SUPERVISION,
    SITUATIONAL_AWARENESS,
    TEAMWORK_AND_COOPERATION,
    derive_domains_from_source,
    normalise_domains,
    resolve_esle_domains,
)
from form_schemas import FORM_SCHEMAS  # noqa: E402
import kaizen_form_filer as kff  # noqa: E402


# ─── Synthetic sessions ──────────────────────────────────────────────────────

TEAM_DECISION_SESSION = (
    "I reviewed a patient with chest pain, worked through the differential "
    "diagnosis with the nurse in charge and decided to admit. I handed over "
    "to the medical team at the end."
)

WHOLE_SHIFT_SESSION = (
    "My consultant observed me across the whole shift on the shop floor. I "
    "supervised two junior doctors, allocated the cubicles, handed over to "
    "the incoming team, made the decision to start the sepsis pathway early "
    "and kept track of the department workload throughout."
)

NO_DOMAIN_EVIDENCE_SESSION = "Quiet evening. Nothing notable happened."


# ─── Derivation from the session's own content ───────────────────────────────

def test_domains_come_from_the_session_content():
    domains = derive_domains_from_source(TEAM_DECISION_SESSION)
    assert TEAMWORK_AND_COOPERATION in domains
    assert DECISION_MAKING in domains
    # Nothing in this session supervised anyone or tracked the department.
    assert MANAGEMENT_AND_SUPERVISION not in domains
    assert SITUATIONAL_AWARENESS not in domains


def test_department_wide_session_selects_all_domains_alone():
    assert derive_domains_from_source(WHOLE_SHIFT_SESSION) == [ALL_DOMAINS]


def test_session_with_no_domain_evidence_infers_nothing():
    assert derive_domains_from_source(NO_DOMAIN_EVIDENCE_SESSION) == []
    assert derive_domains_from_source("") == []


def test_broad_evidence_without_whole_session_cue_stays_specific():
    """Three domains in one resuscitation is not a department-wide ESLE."""
    text = (
        "I led the team during a cardiac arrest, delegated tasks to the nurses "
        "and decided to stop resuscitation after discussion."
    )
    domains = derive_domains_from_source(text)
    assert ALL_DOMAINS not in domains
    assert MANAGEMENT_AND_SUPERVISION in domains


# ─── "All Domains" exclusivity ───────────────────────────────────────────────

def test_all_domains_is_never_combined_with_individual_domains():
    resolved = normalise_domains([ALL_DOMAINS, DECISION_MAKING, TEAMWORK_AND_COOPERATION])
    assert ALL_DOMAINS not in resolved
    # Returned in the order the options appear on the Kaizen widget.
    assert resolved == [TEAMWORK_AND_COOPERATION, DECISION_MAKING]


def test_all_four_individual_domains_collapse_to_all_domains():
    assert normalise_domains(list(ESLE_DOMAIN_OPTIONS[1:])) == [ALL_DOMAINS]


def test_loose_wording_maps_onto_the_kaizen_option_labels():
    assert normalise_domains("teamwork, decision-making") == [
        TEAMWORK_AND_COOPERATION,
        DECISION_MAKING,
    ]
    assert normalise_domains(["not a domain"]) == []


def test_unsupported_claim_is_narrowed_to_what_the_session_evidences():
    resolved = resolve_esle_domains([ALL_DOMAINS], TEAM_DECISION_SESSION)
    assert ALL_DOMAINS not in resolved
    assert set(resolved) == {TEAMWORK_AND_COOPERATION, DECISION_MAKING}


def test_nothing_evidenced_means_nothing_is_selected():
    assert resolve_esle_domains([ALL_DOMAINS], NO_DOMAIN_EVIDENCE_SESSION) == []


# ─── The doctor's own instruction ────────────────────────────────────────────
#
# "domains_of_performance" is schema-required, so an empty value blocks the
# draft preview and asks the doctor (bot._pre_draft_completeness_gaps). Their
# answer has to be able to end that question, or they are asked forever.

def test_doctor_asking_for_all_domains_is_honoured():
    assert resolve_esle_domains(
        [], NO_DOMAIN_EVIDENCE_SESSION, doctor_text="all domains please"
    ) == [ALL_DOMAINS]
    assert resolve_esle_domains(
        [], NO_DOMAIN_EVIDENCE_SESSION, doctor_text="focus on all four domains"
    ) == [ALL_DOMAINS]


def test_doctor_naming_one_domain_is_enough_to_answer_the_question():
    """A one-word reply to "which domains?" must not come back empty."""
    assert resolve_esle_domains([], "teamwork") == [TEAMWORK_AND_COOPERATION]
    assert resolve_esle_domains([], "situational awareness") == [SITUATIONAL_AWARENESS]


def test_declining_all_domains_is_not_read_as_asking_for_them():
    for phrasing in ("not all domains", "decision making rather than all domains"):
        assert resolve_esle_domains([], phrasing, doctor_text=phrasing) != [ALL_DOMAINS]


def test_model_written_narrative_cannot_claim_all_domains():
    """Only the doctor's words widen the claim — never the model's own prose."""
    inflated = "This session covered all domains of performance comprehensively."
    assert resolve_esle_domains([ALL_DOMAINS], inflated) != [ALL_DOMAINS]


def test_a_correction_that_drops_a_claim_narrows_the_selection():
    """Removing the supervision story removes the supervision domain."""
    before = "I supervised two junior doctors and decided to discharge the patient."
    after = "I decided to discharge the patient."

    assert MANAGEMENT_AND_SUPERVISION in derive_domains_from_source(before)
    assert derive_domains_from_source(after) == [DECISION_MAKING]


def test_negated_wording_is_a_known_limitation_not_a_silent_upgrade():
    """Evidence matching is lexical, so "did not supervise" still reads as
    supervision. It over-offers a checkbox the doctor sees and can uncheck in
    the preview; it must never reach ``All Domains`` on its own.
    """
    domains = derive_domains_from_source(
        "I did not supervise anyone. I reviewed one patient and decided to discharge."
    )
    assert ALL_DOMAINS not in domains
    assert MANAGEMENT_AND_SUPERVISION in domains  # documented limitation


def test_filing_normalisation_enforces_exclusivity_for_the_2021_esle_variant():
    """ESLE_2021 shares the ESLE_PART1_2 field map, so it needs the same rule."""
    normalised = kff.normalise_fields_for_deterministic_filing(
        "ESLE_2021",
        {"domains_of_performance": [ALL_DOMAINS, SITUATIONAL_AWARENESS]},
    )
    assert normalised["domains_of_performance"] == [SITUATIONAL_AWARENESS]


# ─── Schema wiring: blank required field must reach the doctor ───────────────

def test_domains_field_is_required_on_the_esle_schema():
    fields = {field["key"]: field for field in FORM_SCHEMAS["ESLE_ASSESS"]["fields"]}
    domains = fields["domains_of_performance"]
    assert domains["required"] is True
    assert domains["type"] == "multi_select"
    assert domains["options"] == list(ESLE_DOMAIN_OPTIONS)


def test_esle_extraction_polish_leaves_an_unevidenced_session_blank():
    from extractor import _polish_esle_fields

    polished = _polish_esle_fields(
        {"reflection": NO_DOMAIN_EVIDENCE_SESSION, "domains_of_performance": [ALL_DOMAINS]},
        NO_DOMAIN_EVIDENCE_SESSION,
    )
    assert polished["domains_of_performance"] == []


def test_esle_extraction_polish_grounds_domains_in_the_reflection():
    from extractor import _polish_esle_fields

    polished = _polish_esle_fields(
        {"reflection": TEAM_DECISION_SESSION, "domains_of_performance": []},
        "",
    )
    assert set(polished["domains_of_performance"]) == {
        TEAMWORK_AND_COOPERATION,
        DECISION_MAKING,
    }


def test_filing_normalisation_enforces_exclusivity_for_esle():
    normalised = kff.normalise_fields_for_deterministic_filing(
        "ESLE_ASSESS",
        {"domains_of_performance": [ALL_DOMAINS, DECISION_MAKING]},
    )
    assert normalised["domains_of_performance"] == [DECISION_MAKING]


def test_domains_widget_is_mapped_on_the_esle_form():
    assert kff.FORM_FIELD_MAP["ESLE_PART1_2"]["domains_of_performance"] == (
        "7683f17f-cc85-47fe-b0fa-e6ad817f0045"
    )


# ─── Widget handler ──────────────────────────────────────────────────────────

def _widget_page(selected_after_pick):
    """Fake page for the DIV multi-select: records picks, reports selection."""
    page = MagicMock()
    page.picked = []

    async def evaluate(script, arg=None):
        if script == kff._WIDGET_PICK_JS:
            page.picked.append(arg["wanted"])
            return arg["wanted"] in selected_after_pick
        if script == kff._WIDGET_STATE_JS:
            return {
                "missing": False,
                "options": [
                    {"text": option, "selected": option in selected_after_pick}
                    for option in ESLE_DOMAIN_OPTIONS
                ],
                "chips": [],
                "text": " ".join(ESLE_DOMAIN_OPTIONS),
            }
        return None

    page.evaluate = AsyncMock(side_effect=evaluate)
    locator = MagicMock()
    locator.count = AsyncMock(return_value=1)
    locator.click = AsyncMock()
    locator.first = locator
    page.locator = MagicMock(return_value=locator)
    return page


@pytest.fixture
def instant_sleep(monkeypatch):
    async def _noop(*args, **kwargs):
        pass

    monkeypatch.setattr("kaizen_form_filer.asyncio.sleep", _noop)


@pytest.mark.asyncio
async def test_widget_selects_each_wanted_domain(instant_sleep):
    page = _widget_page({TEAMWORK_AND_COOPERATION, DECISION_MAKING})

    filled = await kff._fill_domain_multiselect(
        page, "7683f17f", [TEAMWORK_AND_COOPERATION, DECISION_MAKING]
    )

    assert filled is True
    assert page.picked == [TEAMWORK_AND_COOPERATION, DECISION_MAKING]


@pytest.mark.asyncio
async def test_widget_reports_failure_when_the_option_does_not_stick(instant_sleep):
    """An Angular click that changed nothing must not count as filled."""
    page = _widget_page(set())

    assert await kff._fill_domain_multiselect(page, "7683f17f", [DECISION_MAKING]) is False


@pytest.mark.asyncio
async def test_widget_applies_all_domains_exclusivity_before_clicking(instant_sleep):
    page = _widget_page({ALL_DOMAINS, DECISION_MAKING})

    await kff._fill_domain_multiselect(page, "7683f17f", [ALL_DOMAINS, DECISION_MAKING])

    assert page.picked == [DECISION_MAKING]


@pytest.mark.asyncio
async def test_widget_with_no_value_is_not_filled(instant_sleep):
    page = _widget_page(set())

    assert await kff._fill_domain_multiselect(page, "7683f17f", []) is False
    page.evaluate.assert_not_awaited()


# ─── Required-field guard ────────────────────────────────────────────────────

def _guard_page(required_labels):
    page = MagicMock()

    async def evaluate(script, arg=None):
        if script == kff._REQUIRED_FIELD_MARKER_JS:
            return list(required_labels)
        return None

    page.evaluate = AsyncMock(side_effect=evaluate)
    return page


@pytest.mark.asyncio
async def test_guard_reports_a_required_question_kaizen_still_flags():
    page = _guard_page([
        "Which specific Domains of performance in this session would you like "
        "focused on in this ESLE?"
    ])

    gaps = await kff._required_field_gaps(page, "ESLE_ASSESS")

    assert len(gaps) == 1
    assert gaps[0].startswith("Which specific Domains of performance")


@pytest.mark.asyncio
async def test_guard_is_quiet_when_the_saved_draft_flags_nothing():
    assert await kff._required_field_gaps(_guard_page([]), "ESLE_ASSESS") == []


@pytest.mark.asyncio
async def test_guard_is_scoped_to_esle_for_now():
    page = _guard_page(["Some other required question"])

    assert await kff._required_field_gaps(page, "CBD") == []
    page.evaluate.assert_not_awaited()


# ─── QA bucketing: an untouched widget is not "filled" ───────────────────────

def test_widget_selection_read_ignores_unselected_option_text():
    state = {
        "missing": False,
        "options": [{"text": option, "selected": False} for option in ESLE_DOMAIN_OPTIONS],
        "chips": [],
        "text": " ".join(ESLE_DOMAIN_OPTIONS),
    }
    assert kff._widget_selected_values(state) == []

    state["options"][3]["selected"] = True
    assert kff._widget_selected_values(state) == [ESLE_DOMAIN_OPTIONS[3]]


# ─── Pass 3: the loop the doctor actually experiences ────────────────────────
#
# `domains_of_performance` being schema-required is what makes the blank field
# reach the doctor at all. It is also what makes an unanswerable question an
# infinite one, so the answer path is tested where the draft is built, not only
# at the resolver.

def test_doctors_all_domains_answer_ends_the_question():
    """The gap prompt appends the doctor's reply to the case text and re-extracts.

    A session with no domain evidence plus the reply "all domains" must come
    back with a value; otherwise the same gap is raised and the doctor is asked
    the identical question again.
    """
    from extractor import _polish_esle_fields

    reply_appended = f"{NO_DOMAIN_EVIDENCE_SESSION}\n\nall domains"
    polished = _polish_esle_fields(
        {"reflection": NO_DOMAIN_EVIDENCE_SESSION, "domains_of_performance": []},
        reply_appended,
    )

    assert polished["domains_of_performance"] == [ALL_DOMAINS]


def test_doctors_named_domain_answer_ends_the_question():
    from extractor import _polish_esle_fields

    reply_appended = f"{NO_DOMAIN_EVIDENCE_SESSION}\n\nsituational awareness"
    polished = _polish_esle_fields(
        {"reflection": NO_DOMAIN_EVIDENCE_SESSION, "domains_of_performance": []},
        reply_appended,
    )

    assert polished["domains_of_performance"] == [SITUATIONAL_AWARENESS]


def test_an_unanswered_esle_session_still_blocks_rather_than_guessing():
    """The escape hatch must not become a way to fill the field with anything.

    No evidence and no instruction still returns empty, which is what keeps the
    pre-draft gap firing instead of inventing a claim for the doctor.
    """
    from extractor import _polish_esle_fields

    polished = _polish_esle_fields(
        {"reflection": NO_DOMAIN_EVIDENCE_SESSION, "domains_of_performance": []},
        NO_DOMAIN_EVIDENCE_SESSION,
    )

    assert polished["domains_of_performance"] == []


# ─── Exclusivity under repeated filing ───────────────────────────────────────

@pytest.mark.parametrize("form_type", ["ESLE_ASSESS", "ESLE", "ESLE_2021", "ESLE_PART1_2"])
def test_exclusivity_holds_on_every_esle_variant(form_type):
    normalised = kff.normalise_fields_for_deterministic_filing(
        form_type,
        {"domains_of_performance": [ALL_DOMAINS, DECISION_MAKING]},
    )
    assert normalised["domains_of_performance"] == [DECISION_MAKING]


@pytest.mark.parametrize("form_type", ["ESLE_ASSESS", "ESLE_2021"])
def test_exclusivity_survives_re_entrant_filing(form_type):
    """Re-filing a saved/restored draft must not reintroduce a forbidden pair."""
    fields = {"domains_of_performance": [ALL_DOMAINS]}
    for _ in range(3):
        fields = kff.normalise_fields_for_deterministic_filing(form_type, fields)
        assert fields["domains_of_performance"] == [ALL_DOMAINS]

    fields = {"domains_of_performance": [ALL_DOMAINS, TEAMWORK_AND_COOPERATION]}
    for _ in range(3):
        fields = kff.normalise_fields_for_deterministic_filing(form_type, fields)
        assert fields["domains_of_performance"] == [TEAMWORK_AND_COOPERATION]
        assert ALL_DOMAINS not in fields["domains_of_performance"]


@pytest.mark.asyncio
async def test_widget_never_clicks_all_domains_alongside_an_individual_domain(instant_sleep):
    """Last line of defence: even a bad value reaching the filler is resolved."""
    page = _widget_page(set(ESLE_DOMAIN_OPTIONS))

    await kff._fill_domain_multiselect(
        page, "7683f17f", [ALL_DOMAINS, DECISION_MAKING, SITUATIONAL_AWARENESS]
    )

    assert ALL_DOMAINS not in page.picked
    assert page.picked == [DECISION_MAKING, SITUATIONAL_AWARENESS]


# ─── Variant resolution: the class behind the ESLE_2021 misses ───────────────

def test_every_esle_variant_resolves_to_the_form_it_actually_drives():
    for form_type in ("ESLE", "ESLE_ASSESS", "ESLE_2021", "ESLE_PART1_2"):
        assert kff.filing_form_base(form_type) == "ESLE_PART1_2"


@pytest.mark.asyncio
async def test_required_field_guard_covers_the_2021_variant():
    """ESLE_2021 files through the ESLE_PART1_2 DOM, so it must be guarded too.

    Before this, canonical_form_type left ESLE_2021 unresolved, the guard
    returned early, and a saved 2021 draft with a blank required question was
    reported as a clean save.
    """
    flagged = ["Which specific Domains of performance in this session would you like focused on in this ESLE?"]

    for form_type in ("ESLE", "ESLE_ASSESS", "ESLE_2021", "ESLE_PART1_2"):
        gaps = await kff._required_field_gaps(_guard_page(flagged), form_type)
        assert len(gaps) == 1, f"{form_type} was not guarded"
        assert gaps[0].startswith("Which specific Domains of performance")


@pytest.mark.asyncio
async def test_required_field_guard_still_ignores_unguarded_forms():
    assert await kff._required_field_gaps(_guard_page(["Something"]), "MINI_CEX") == []
    assert await kff._required_field_gaps(_guard_page(["Something"]), "MINI_CEX_2021") == []


# ─── Class-level sweep: no required ESLE field can reach Kaizen blank ────────

def test_no_required_esle_field_can_be_filed_blank():
    """Every schema-required ESLE field is either mapped, defaulted, or asked for.

    The original complaint was a required field arriving blank on the saved
    draft. This pins the class: a required field added to the ESLE schema
    later fails here unless it is wired to the DOM, and a date-like mapped
    field fails unless the header defaults fill it.
    """
    from bot import _pre_draft_completeness_gaps

    schema_required = [
        field for field in FORM_SCHEMAS["ESLE_ASSESS"]["fields"]
        if field.get("required") and field.get("type") != "kc_tick"
    ]
    assert schema_required, "ESLE schema lost its required fields"

    for form_type in ("ESLE_ASSESS", "ESLE_2021"):
        field_map = kff.FORM_FIELD_MAP[form_type]

        # 1. Every required field has somewhere on the form to go.
        for field in schema_required:
            assert field["key"] in field_map, (
                f"{form_type}: required field {field['key']} has no DOM mapping"
            )

        # 2. Every mapped date field is filled without asking, on both variants.
        filled, meta = kff.apply_common_header_defaults(form_type, {}, field_map)
        for key in field_map:
            if "date" in key:
                assert filled.get(key), f"{form_type}: {key} left blank by header defaults"
        assert "date_of_esle" in meta["defaulted_fields"]

        # 3. Everything else required is asked for before a draft is ever shown.
        context = MagicMock()
        context.user_data = {}
        draft = {"form_type": form_type, "fields": {}}
        gap_keys = {
            gap["key"] for gap in _pre_draft_completeness_gaps(context, draft, form_type)
        }
        for field in schema_required:
            if field["type"] == "date":
                continue
            assert field["key"] in gap_keys, (
                f"{form_type}: required field {field['key']} is neither defaulted nor asked for"
            )
