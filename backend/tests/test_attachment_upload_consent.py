"""Consent gate on uploading an attachment to Kaizen.

De-identification only ever touched text. The file itself was uploaded
byte-for-byte via `route_filing(attachment_path=...)` -> `_attach_file`, so a
photo of a report put the patient's name into the portfolio as pixels. Kaizen
gives no way to review a file once it is on a draft, so this cannot be a
warning the doctor can miss — nothing uploads without an explicit choice.

Coverage is per pathway, because all three (image, document, video) converge on
the same `attachment_path` and each could regress independently.
"""

import pytest

from bot import _attachment_confirmation_reason


def _context(**user_data):
    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.user_data = dict(user_data)
    return ctx


CLEAN_CASE = (
    "58 year old with chest pain in resus. I led the assessment, escalated to "
    "cardiology, and learned to request the echo earlier."
)

# The real echo report OCR that caused the incident.
PHI_CASE = (
    "Transthoracic Echocardiography. report of patient (E R) MUNAWAR AHMED. "
    "Impression: Mild concentric LVH with SWMA."
)


class TestImageAndDocumentPathways:
    @pytest.mark.parametrize("kind", ["image", "document"])
    def test_identifiers_in_extracted_text_trigger_a_confirmation(self, kind):
        reason = _attachment_confirmation_reason(
            _context(attachment_path="/tmp/f", attachment_kind=kind, case_text=PHI_CASE)
        )
        assert reason is not None
        assert "patient name" in reason

    @pytest.mark.parametrize("kind", ["image", "document"])
    def test_clean_case_uploads_without_interruption(self, kind):
        """A doctor attaching properly anonymised evidence must not be nagged."""
        reason = _attachment_confirmation_reason(
            _context(attachment_path="/tmp/f", attachment_kind=kind, case_text=CLEAN_CASE)
        )
        assert reason is None

    @pytest.mark.parametrize("kind", ["image", "document"])
    def test_no_extracted_text_means_no_signal_so_it_asks(self, kind):
        """An 'attach only' file was never read. Silence is not evidence of safety."""
        reason = _attachment_confirmation_reason(
            _context(attachment_path="/tmp/f", attachment_kind=kind, case_text="")
        )
        assert reason is not None


class TestVideoPathway:
    def test_video_always_asks_even_with_a_clean_transcript(self):
        """The audio transcript says nothing about a wristband in frame."""
        reason = _attachment_confirmation_reason(
            _context(attachment_path="/tmp/v.mp4", attachment_kind="video", case_text=CLEAN_CASE)
        )
        assert reason is not None
        assert "video" in reason


class TestGateMechanics:
    def test_no_attachment_means_no_gate(self):
        assert _attachment_confirmation_reason(_context(case_text=PHI_CASE)) is None

    def test_a_recorded_choice_is_not_asked_again(self):
        reason = _attachment_confirmation_reason(
            _context(
                attachment_path="/tmp/f",
                attachment_kind="image",
                case_text=PHI_CASE,
                attachment_upload_confirmed=True,
            )
        )
        assert reason is None

    @pytest.mark.asyncio
    async def test_declining_drops_the_file_but_still_saves_the_draft(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        import bot

        context = _context(
            attachment_path="/tmp/f.jpg", attachment_name="f.jpg", attachment_kind="image"
        )
        update = MagicMock()
        update.callback_query.data = "ATTACH|no"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_reply_markup = AsyncMock()

        with patch.object(bot, "handle_approval_approve", new=AsyncMock(return_value=0)) as resume:
            await bot.handle_attachment_confirm(update, context)

        assert context.user_data.get("attachment_path") is None
        assert context.user_data["attachment_upload_confirmed"] is True
        resume.assert_awaited(), "Declining the file must still save the draft"

    @pytest.mark.asyncio
    async def test_accepting_keeps_the_file_and_resumes(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        import bot

        context = _context(
            attachment_path="/tmp/f.jpg", attachment_name="f.jpg", attachment_kind="image"
        )
        update = MagicMock()
        update.callback_query.data = "ATTACH|yes"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_reply_markup = AsyncMock()

        with patch.object(bot, "handle_approval_approve", new=AsyncMock(return_value=0)) as resume:
            await bot.handle_attachment_confirm(update, context)

        assert context.user_data["attachment_path"] == "/tmp/f.jpg"
        assert context.user_data["attachment_upload_confirmed"] is True
        resume.assert_awaited()

    def test_the_confirm_callbacks_are_routed_in_the_approval_state(self):
        """A gate whose buttons are not registered would strand the save."""
        import inspect

        import bot

        source = inspect.getsource(bot.build_application)
        assert "handle_attachment_confirm" in source
        assert r"^ATTACH\|(?:yes|no)$" in source
