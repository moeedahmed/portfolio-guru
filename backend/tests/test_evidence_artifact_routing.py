"""Certificate/award evidence must not be routed as a clinical case.

Regression cover for the live defect where a "Registrar of the Month" PDF was
offered as case material, then answered with an invented SLO mapping and
unverified Kaizen/ARCP platform rules.
"""

import os
import re
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot import (
    AWAIT_CASE_INPUT,
    AWAIT_DOC_INTENT,
    AWAIT_FORM_CHOICE,
    handle_case_input,
    handle_document_intent,
    handle_mid_conversation_text,
)
from evidence_artifact import (
    classify_evidence_artifact,
    evidence_artifact_answer,
    evidence_artifact_text_message,
    evidence_artifact_upload_message,
    looks_like_evidence_artifact,
)
from tests.bot_simulator import BotSimulator


CERTIFICATE_FILE = "ED Registrar of the Month.pdf"

CERTIFICATE_TEXT = (
    "Certificate of Recognition\n"
    "Awarded to the ED Registrar of the Month for outstanding contribution "
    "to the department."
)

CLINICAL_CASE_WITH_CERTIFICATE = (
    "68-year-old patient presented with central chest pain radiating to the jaw. "
    "I assessed them in resus, requested an ECG and bloods, gave aspirin and "
    "referred to cardiology. I've also attached the certificate from the ACS "
    "study day I did last week."
)


def _all_visible_text(sim: BotSimulator) -> str:
    return "\n".join(text for _, text, _ in sim.messages_sent if isinstance(text, str))


# --- Classifier -------------------------------------------------------------


@pytest.mark.parametrize(
    "file_name",
    [
        "ED Registrar of the Month.pdf",
        "certificate-of-attendance.pdf",
        "Trust_Award_Letter.docx",
        "thank you letter from patient relatives.pdf",
        "highly-commended-nomination.pdf",
    ],
)
def test_artifact_filenames_are_detected(file_name):
    assert looks_like_evidence_artifact(file_name=file_name) is True


@pytest.mark.parametrize(
    "file_name",
    [
        "clinical-notes.pdf",
        "ed case chest pain.docx",
        "cbd-discussion-summary.pdf",
    ],
)
def test_case_filenames_are_not_artifacts(file_name):
    assert looks_like_evidence_artifact(file_name=file_name) is False


def test_certificate_text_is_detected():
    signal = classify_evidence_artifact(text=CERTIFICATE_TEXT)
    assert signal.is_artifact is True
    assert signal.source == "text"


def test_clinical_case_mentioning_a_certificate_is_not_an_artifact():
    assert looks_like_evidence_artifact(text=CLINICAL_CASE_WITH_CERTIFICATE) is False
    # Even with an artifact-shaped filename, real clinical content wins.
    assert (
        looks_like_evidence_artifact(
            file_name=CERTIFICATE_FILE,
            text=CLINICAL_CASE_WITH_CERTIFICATE,
        )
        is False
    )


def test_course_the_doctor_says_they_completed_is_not_an_artifact():
    """A described activity is fileable work, even when the file is a certificate."""
    assert (
        looks_like_evidence_artifact(
            file_name="atls-certificate.pdf",
            text="I completed ATLS and have a certificate.",
        )
        is False
    )


def test_clinical_paperwork_named_certificate_is_not_an_award():
    assert looks_like_evidence_artifact(file_name="death-certificate-copy.pdf") is False


# --- Copy honesty -----------------------------------------------------------


FABRICATED_PLATFORM_CLAIMS = (
    r"slo\s*\d",
    r"key capability",
    r"kaizen (does not|doesn't) have",
    r"you must use",
    r"(may|will) be missed",
    r"arcp panels? (will|may|won't|cannot)",
    r"maps? to slo",
)


@pytest.mark.parametrize(
    "copy",
    [
        evidence_artifact_upload_message(),
        evidence_artifact_text_message(),
        evidence_artifact_answer(),
    ],
)
def test_artifact_copy_asserts_no_platform_rules(copy):
    lowered = copy.lower()
    for claim in FABRICATED_PLATFORM_CLAIMS:
        assert not re.search(claim, lowered), f"fabricated platform claim: {claim}"
    assert "self-directed learning" not in lowered


def test_artifact_copy_states_the_real_product_capability():
    for copy in (evidence_artifact_upload_message(), evidence_artifact_text_message()):
        assert "drafts" in copy.lower()
        assert "can't upload a standalone file to kaizen" in copy.lower()


# --- Document upload --------------------------------------------------------


def _document_update(sim: BotSimulator, file_name: str):
    update = sim._make_text_update("")
    document = MagicMock()
    document.file_name = file_name
    document.mime_type = "application/pdf"
    file_obj = MagicMock()
    file_obj.download_to_drive = AsyncMock()
    document.get_file = AsyncMock(return_value=file_obj)

    update.message.text = None
    update.message.voice = None
    update.message.audio = None
    update.message.photo = []
    update.message.document = document
    return update


@pytest.mark.asyncio
async def test_certificate_upload_is_not_offered_as_case_material():
    sim = BotSimulator()
    context = sim._make_context()
    update = _document_update(sim, CERTIFICATE_FILE)

    with patch("bot.has_credentials", return_value=True), patch(
        "bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))
    ), patch("bot.extract_from_document", new=AsyncMock()) as extract_mock, patch(
        "bot.recommend_form_types", new=AsyncMock(return_value=[])
    ) as recommend_mock:
        result = await handle_case_input(update, context)

    assert result == AWAIT_DOC_INTENT
    extract_mock.assert_not_called()
    recommend_mock.assert_not_called()

    buttons = sim.get_last_buttons()
    assert ("📎 Attach as evidence", "DOCUSE|attach") in buttons
    assert not any(data == "DOCUSE|info" for _, data in buttons)
    assert not any(data == "DOCUSE|both" for _, data in buttons)

    visible = _all_visible_text(sim)
    assert "certificate or award rather than a clinical case" in visible
    assert "reflection" in visible.lower()
    assert "SLO" not in visible
    assert "Self-directed Learning" not in visible

    path = context.user_data["_pending_doc"]["path"]
    if os.path.exists(path):
        os.unlink(path)


@pytest.mark.asyncio
async def test_ordinary_document_upload_keeps_the_case_choices():
    sim = BotSimulator()
    context = sim._make_context()
    update = _document_update(sim, "clinical-notes.pdf")

    with patch("bot.has_credentials", return_value=True), patch(
        "bot.check_can_file", new=AsyncMock(return_value=(True, 0, 10, "free"))
    ), patch("bot.extract_from_document", new=AsyncMock()):
        result = await handle_case_input(update, context)

    assert result == AWAIT_DOC_INTENT
    buttons = sim.get_last_buttons()
    assert ("📝 Use as case", "DOCUSE|info") in buttons
    assert ("📎 Read + attach", "DOCUSE|both") in buttons

    path = context.user_data["_pending_doc"]["path"]
    if os.path.exists(path):
        os.unlink(path)


@pytest.mark.asyncio
async def test_certificate_text_read_from_a_document_never_reaches_form_recommendation():
    """The filename can be anything; what was read decides too."""
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("DOCUSE|info")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy pdf content")
    context.user_data["_pending_doc"] = {"path": temp_path, "name": "scan001.pdf"}

    with patch(
        "bot.extract_from_document", new=AsyncMock(return_value=CERTIFICATE_TEXT)
    ), patch("bot.recommend_form_types", new=AsyncMock(return_value=[])) as recommend_mock:
        result = await handle_document_intent(update, context)

    assert result == AWAIT_CASE_INPUT
    recommend_mock.assert_not_called()
    assert "case_text" not in context.user_data
    visible = _all_visible_text(sim)
    assert "certificate or award rather than a clinical case" in visible
    assert "SLO" not in visible

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_read_and_attach_certificate_never_claims_a_kaizen_entry_exists():
    """Read + attach only queues the file for a later form; it does not save it."""
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("DOCUSE|both")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy pdf content")
    context.user_data["_pending_doc"] = {"path": temp_path, "name": "scan001.pdf"}

    with patch(
        "bot.extract_from_document", new=AsyncMock(return_value=CERTIFICATE_TEXT)
    ), patch("bot.recommend_form_types", new=AsyncMock(return_value=[])) as recommend_mock:
        result = await handle_document_intent(update, context)

    assert result == AWAIT_CASE_INPUT
    recommend_mock.assert_not_called()
    visible = _all_visible_text(sim).lower()
    assert "ready to attach after you choose a form" in visible
    assert "nothing has been saved to kaizen" in visible
    assert "attached to this entry already" not in visible
    assert context.user_data["attachment_path"] == temp_path

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_clinical_document_mentioning_a_certificate_still_files_normally():
    sim = BotSimulator()
    context = sim._make_context()
    update = sim._make_callback_update("DOCUSE|info")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        temp_path = f.name
        f.write(b"dummy pdf content")
    context.user_data["_pending_doc"] = {"path": temp_path, "name": "case-and-certificate.pdf"}

    recommend_mock = AsyncMock(return_value=[])
    with patch(
        "bot.extract_from_document",
        new=AsyncMock(return_value=CLINICAL_CASE_WITH_CERTIFICATE),
    ), patch("bot.recommend_form_types", new=recommend_mock), patch(
        "bot.get_training_level", return_value="ST5"
    ), patch("bot.get_curriculum", return_value="2025"), patch(
        "bot._gathering_enabled", return_value=False
    ):
        await handle_document_intent(update, context)

    assert context.user_data.get("case_text") == CLINICAL_CASE_WITH_CERTIFICATE
    recommend_mock.assert_awaited()

    if os.path.exists(temp_path):
        os.unlink(temp_path)


# --- Standalone text --------------------------------------------------------


@pytest.mark.asyncio
async def test_award_only_message_gets_honest_routing_not_a_form():
    from bot import _process_case_text

    sim = BotSimulator()
    context = sim._make_context()
    message = sim._make_text_update("").message
    text = "I've just been given the ED Registrar of the Month award certificate."

    recommend_mock = AsyncMock(return_value=[])
    with patch("bot.recommend_form_types", new=recommend_mock), patch(
        "bot.get_training_level", return_value="ST5"
    ):
        result = await _process_case_text(message, context, sim.user_id, text, "text")

    assert result == AWAIT_CASE_INPUT
    recommend_mock.assert_not_called()
    assert "case_text" not in context.user_data
    visible = _all_visible_text(sim)
    assert "certificate or award rather than a clinical case" in visible
    assert "SLO" not in visible
    assert "Self-directed Learning" not in visible


@pytest.mark.asyncio
async def test_clinical_case_mentioning_a_certificate_still_reaches_recommendation():
    from bot import _process_case_text

    sim = BotSimulator()
    context = sim._make_context()
    message = sim._make_text_update("").message

    recommend_mock = AsyncMock(return_value=[])
    with patch("bot.recommend_form_types", new=recommend_mock), patch(
        "bot.get_training_level", return_value="ST5"
    ), patch("bot._gathering_enabled", return_value=False):
        await _process_case_text(
            message, context, sim.user_id, CLINICAL_CASE_WITH_CERTIFICATE, "text"
        )

    recommend_mock.assert_awaited()
    assert context.user_data["case_text"] == CLINICAL_CASE_WITH_CERTIFICATE


# --- Question handling ------------------------------------------------------


@pytest.mark.asyncio
async def test_reflection_or_upload_question_is_answered_deterministically():
    from extractor import answer_question

    with patch("extractor._generate", new=AsyncMock(side_effect=AssertionError("no model call"))):
        answer = await answer_question(
            "Should I add this as a reflection or just upload the file?",
            document_name=CERTIFICATE_FILE,
        )

    lowered = answer.lower()
    assert "reflection" in lowered
    assert "attach" in lowered
    # Distinguishes evidence attachment from a reflection with real substance,
    # without pushing a form or asserting platform rules.
    assert "what you'd change" in lowered
    assert "self-directed learning" not in lowered
    assert not re.search(r"slo\s*\d", lowered)


@pytest.mark.asyncio
async def test_award_question_does_not_manufacture_a_form_choice_continuation():
    sim = BotSimulator()
    context = sim._make_context()
    context.user_data.update(
        {
            "case_text": CERTIFICATE_TEXT,
            "document_name": CERTIFICATE_FILE,
        }
    )

    from workflow_turn_policy import WorkflowTurnKind

    with patch("bot.decide_workflow_turn") as turn_mock:
        turn_mock.return_value = MagicMock(kind=WorkflowTurnKind.SIDE_QUESTION)
        update = sim._make_text_update(
            "Should I record this as a reflection or just upload the file?"
        )
        with patch(
            "extractor._generate",
            new=AsyncMock(side_effect=AssertionError("no model call")),
        ):
            result = await handle_mid_conversation_text(update, context)

    assert result == AWAIT_CASE_INPUT
    assert result != AWAIT_FORM_CHOICE
    visible = _all_visible_text(sim)
    assert "case is still in progress" not in visible
    assert "SLO" not in visible
    assert "Self-directed Learning" not in visible
