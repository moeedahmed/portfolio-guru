"""Offline dogfood matrix for the trusted-filing sprint.

Scores the ten high-value RCEM Kaizen forms across the five reliability
dimensions from the sprint brief, fully offline (no Kaizen, no network, no
credentials):

  1. first-message handling       - explicit "file a X" locks the right form
  2. draft/recommendation path    - the form resolves to a real public name + schema
  3. missing-field handling       - required fields are detectable on an empty draft
  4. deterministic save readiness - a deterministic Kaizen target with a field map
  5. incomplete-draft recovery    - a complaint re-enters amend mode, never resets

Dimensions 1-4 are pure data assertions over the live form schema, so the
matrix stays meaningful as the schema evolves. Dimension 5 is a
generic-over-form behavioural check driven through the real conversation
handler.

Known finding (documented, not silently passed): explicit keyword form-lock
currently covers 7 of the 10 named forms. REFLECT_LOG, PROC_LOG and TEACH do
not lock from a bare "file a ..." phrase and instead resolve through the
recommendation path; ``KEYWORD_LOCK_FORMS`` and ``RECOMMENDATION_ONLY_FORMS``
pin that split so a regression in either direction is caught.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import bot
from form_display import public_form_name
from filer_router import PLATFORM_REGISTRY
from kaizen_form_filer import FORM_FIELD_MAP
from models import FormDraft
from tests.bot_simulator import BotSimulator

# (form_type, expected public name, explicit "file a X" phrase)
DOGFOOD_FORMS = [
    ("CBD", "Case-Based Discussion", "can you file a cbd"),
    ("MINI_CEX", "Mini-Clinical Evaluation Exercise", "file a mini-cex"),
    ("DOPS", "Direct Observation of Procedural Skills", "create a dops"),
    ("ACAT", "Acute Care Assessment Tool", "file an acat"),
    ("SDL", "Self-directed Learning Reflection", "file a self-directed learning"),
    ("REFLECT_LOG", "Reflective Practice Log", "file a reflective practice log"),
    ("PROC_LOG", "Procedural Log", "log a procedure"),
    ("TEACH", "Teaching Delivered By Trainee", "file a teaching session"),
    ("QIAT", "Quality Improvement Assessment Tool", "file a qiat"),
    ("EDU_ACT", "Educational Activity", "log an educational activity"),
]

DOGFOOD_CODES = [code for code, _name, _phrase in DOGFOOD_FORMS]

# Forms whose explicit "file a X" phrase locks via keyword today.
KEYWORD_LOCK_FORMS = {"CBD", "MINI_CEX", "DOPS", "ACAT", "SDL", "QIAT", "EDU_ACT"}
# Forms that currently reach their form type via the recommendation path only.
RECOMMENDATION_ONLY_FORMS = {"REFLECT_LOG", "PROC_LOG", "TEACH"}


def _kaizen() -> dict:
    return PLATFORM_REGISTRY["kaizen"]


def _required_fields(form_type: str) -> list:
    required, _optional = bot._template_requirements(form_type)
    return required


def _last_text(sim: BotSimulator) -> str:
    return sim.get_last_text() or ""


def test_matrix_covers_exactly_the_ten_named_forms():
    assert len(DOGFOOD_CODES) == 10
    assert set(DOGFOOD_CODES) == set(KEYWORD_LOCK_FORMS) | set(RECOMMENDATION_ONLY_FORMS)


# ---------------------------------------------------------------------------
# Dimension 1 - first-message handling (explicit form-lock)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("form_type", "phrase"), [
    (code, phrase) for code, _name, phrase in DOGFOOD_FORMS if code in KEYWORD_LOCK_FORMS
])
def test_first_message_locks_keyword_forms(form_type, phrase):
    assert bot._explicit_form_start_request(phrase) == form_type


@pytest.mark.parametrize(("form_type", "phrase"), [
    (code, phrase) for code, _name, phrase in DOGFOOD_FORMS if code in RECOMMENDATION_ONLY_FORMS
])
def test_recommendation_only_forms_are_documented_gap(form_type, phrase):
    """These do not keyword-lock today; they must still be reachable as a
    deterministic Kaizen target so the recommendation path can surface them."""
    assert bot._explicit_form_start_request(phrase) is None
    assert form_type in _kaizen()["supported_forms"]


# ---------------------------------------------------------------------------
# Dimension 2 - draft/recommendation path quality (public name + schema)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("form_type", "expected_name"), [
    (code, name) for code, name, _phrase in DOGFOOD_FORMS
])
def test_public_name_is_correct_and_channel_safe(form_type, expected_name):
    name = public_form_name(form_type)
    assert name == expected_name
    # Channel-safe: no raw markdown emphasis leaks into user-visible copy.
    assert "*" not in name
    assert "_" not in name


def test_sdl_and_reflect_log_never_alias():
    """Regression guard from the SDL dogfood report: SDL is not relabelled
    "Reflective Practice Log"."""
    assert public_form_name("SDL") == "Self-directed Learning Reflection"
    assert public_form_name("REFLECT_LOG") == "Reflective Practice Log"
    assert public_form_name("SDL") != public_form_name("REFLECT_LOG")


# ---------------------------------------------------------------------------
# Dimension 3 - missing-field handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form_type", DOGFOOD_CODES)
def test_required_fields_exist_for_each_form(form_type):
    required = _required_fields(form_type)
    assert required, f"{form_type} has no required template fields"


@pytest.mark.parametrize("form_type", DOGFOOD_CODES)
def test_empty_draft_reports_all_required_fields_missing(form_type):
    draft = FormDraft(form_type=form_type, uuid="u", fields={})
    missing_required = bot._missing_template_fields(draft, form_type)[0]
    required_keys = {f["key"] for f in _required_fields(form_type)}
    missing_keys = {f["key"] for f in missing_required}
    assert missing_keys == required_keys


# ---------------------------------------------------------------------------
# Dimension 4 - deterministic Kaizen-save readiness (offline-provable)
# ---------------------------------------------------------------------------


def test_kaizen_platform_is_deterministic():
    assert _kaizen()["deterministic"] is True


@pytest.mark.parametrize("form_type", DOGFOOD_CODES)
def test_form_is_a_deterministic_save_target(form_type):
    assert form_type in _kaizen()["supported_forms"]
    assert form_type in FORM_FIELD_MAP
    assert FORM_FIELD_MAP[form_type], f"{form_type} has an empty field map"


# ---------------------------------------------------------------------------
# Dimension 5 - recovery after incomplete-draft complaint (generic over form)
# ---------------------------------------------------------------------------


def _filed_context(sim: BotSimulator, form_type: str) -> None:
    """Reproduce the user-data state left after a successful save: live draft
    cleared, amend snapshot + last_filing_* markers preserved."""
    draft = FormDraft(form_type=form_type, uuid=f"uuid-{form_type.lower()}", fields={})
    case_text = f"{public_form_name(form_type).lower()} dogfood case"
    sim.user_data.clear()
    sim.user_data["last_filing_status"] = "success"
    sim.user_data["last_filing_form_name"] = public_form_name(form_type)
    sim.user_data["last_amend_draft"] = bot._serialise_draft(draft)
    sim.user_data["last_amend_case_text"] = case_text
    sim.user_data["last_amend_chosen_form"] = form_type
    sim.user_data["last_filed_case_text"] = case_text
    sim.user_data["last_filed_form_type"] = form_type


@pytest.mark.asyncio
@pytest.mark.parametrize("form_type", ["CBD", "SDL", "REFLECT_LOG", "QIAT"])
async def test_incomplete_complaint_recovers_generically(form_type):
    """A complaint about an incomplete saved draft re-enters amend mode and
    never resets to generic idle copy - for any supported form type."""
    sim = BotSimulator()
    context = sim._make_context()
    _filed_context(sim, form_type)
    update = sim._make_text_update("you didn't fill the rest of the details for this ticket")

    with patch("bot.has_credentials", return_value=True), \
         patch("bot.check_can_file", new=AsyncMock(return_value=(True, 1, 10, "free"))), \
         patch("bot.classify_intent", new=AsyncMock(return_value="chitchat")), \
         patch("bot.answer_question", new=AsyncMock(return_value="should not be used")):
        result = await bot.handle_case_input(update, context)

    text = _last_text(sim)
    assert result == bot.AWAIT_APPROVAL
    assert context.user_data.get("amend_mode") is True
    assert bot._load_draft(context) is not None
    assert "Ready when you are" not in text
    # Channel-safe recovery copy.
    assert "*" not in text


def test_incomplete_draft_complaint_detection_is_generic():
    """The complaint detector is form-independent."""
    assert bot._is_incomplete_draft_complaint("you didn't fill the rest of the details") is True
    assert bot._is_incomplete_draft_complaint("this draft is incomplete") is True
    assert bot._is_incomplete_draft_complaint("thanks, that's perfect") is False
    assert bot._is_incomplete_draft_complaint("file another case for me") is False
