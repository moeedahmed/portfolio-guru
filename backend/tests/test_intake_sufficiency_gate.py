"""Permanent regression fixtures for the intake sufficiency-gate rebuild.

Owner's decision (18 Sep 2026): the intake must facilitate the doctor to a
portfolio draft; it must not refuse a real clinical case because of a
keyword/source list. These are the run card's three hard fixtures:

  Case A — a real doctor message must reach form recommendation on every
           source: text, voice, mixed, photo-with-no-caption.
  Case B — photo-only, no caption, vision-extracted notes text must be
           accepted as the case and proceed to form recommendation.
  Case C — "hi", "ok", or an empty message must still get the short
           "tell me what happened" ask, and must not call the model.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from bot import AWAIT_FORM_CHOICE, ConversationHandler, _process_case_text
from extractor import FORM_UUIDS
from models import FormTypeRecommendation
from tests.bot_simulator import BotSimulator


CASE_A_TEXT = (
    "8 yrs old beought to ed after rtc, was in passenger seat when struck by a trolley, "
    "complaining of headache, pain over neck and severe abd pain requiring morphone. "
    "Fast scan was done which was nad, despite negative fast scan given pain proportion "
    "and mechanism a ct was arranged, which came back normal.\n\n"
    "Ultrasound use and doind fast scan helped us identify any life threatening or "
    "immediate concerns"
)

CASE_B_PHOTO_NOTES_TEXT = (
    "8M brought in post RTC, struck by trolley as passenger. C/O headache, neck pain, "
    "severe abdo pain requiring morphine. FAST scan NAD. CT abdo arranged despite "
    "negative FAST given mechanism/pain — normal. Discussed use of FAST to rule out "
    "life-threatening injury."
)


def _recommendations() -> list[FormTypeRecommendation]:
    return [
        FormTypeRecommendation(
            form_type="CBD",
            rationale="Polytrauma case with imaging decision-making.",
            uuid=FORM_UUIDS.get("CBD"),
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("input_source", ["text", "voice", "mixed"])
async def test_case_a_reaches_form_recommendation_on_every_text_source(input_source):
    sim = BotSimulator()
    context = sim._make_context()
    message = sim._make_text_update("").message

    with patch("bot.recommend_form_types", new=AsyncMock(return_value=_recommendations())) as recommend, \
         patch("bot.get_training_level", return_value="ST5"), \
         patch("bot.get_curriculum", return_value="2025"):
        result = await _process_case_text(message, context, 12345, CASE_A_TEXT, input_source)

    assert result == AWAIT_FORM_CHOICE
    recommend.assert_awaited_once()
    assert "clinical detail" not in (sim.get_last_text() or "").lower()


@pytest.mark.asyncio
async def test_case_a_reaches_form_recommendation_from_photo_no_caption():
    """Case A sent as a bare photo (vision-extracted text, no caption) must
    still reach form recommendation — a photo is a first-class case source."""
    sim = BotSimulator()
    context = sim._make_context()
    message = sim._make_text_update("").message

    with patch("bot.recommend_form_types", new=AsyncMock(return_value=_recommendations())) as recommend, \
         patch("bot.get_training_level", return_value="ST5"), \
         patch("bot.get_curriculum", return_value="2025"):
        result = await _process_case_text(message, context, 12345, CASE_A_TEXT, "photo")

    assert result == AWAIT_FORM_CHOICE
    recommend.assert_awaited_once()


@pytest.mark.asyncio
async def test_case_b_photo_only_notes_accepted_as_the_case():
    """A photo of the doctor's own notes, no caption, is accepted as the
    case directly and reaches form recommendation."""
    sim = BotSimulator()
    context = sim._make_context()
    message = sim._make_text_update("").message

    with patch("bot.recommend_form_types", new=AsyncMock(return_value=_recommendations())) as recommend, \
         patch("bot.get_training_level", return_value="ST5"), \
         patch("bot.get_curriculum", return_value="2025"):
        result = await _process_case_text(message, context, 12345, CASE_B_PHOTO_NOTES_TEXT, "photo")

    assert result == AWAIT_FORM_CHOICE
    recommend.assert_awaited_once()
    assert context.user_data["case_text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_text", ["hi", "ok", ""])
async def test_case_c_genuinely_empty_input_asks_without_calling_the_model(raw_text):
    sim = BotSimulator()
    context = sim._make_context()
    message = sim._make_text_update("").message

    with patch("bot.recommend_form_types", new=AsyncMock()) as recommend:
        result = await _process_case_text(message, context, 12345, raw_text, "text")

    assert result == ConversationHandler.END
    recommend.assert_not_awaited()
    text = (sim.get_last_text() or "").lower()
    assert "clinical detail" in text or "what happened" in text
