import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

from tests import telegram_live_harness as harness
from tests.telegram_live_policy import command_expectation

REPO_ROOT = Path(__file__).resolve().parents[2]
BOT_QA = REPO_ROOT / "scripts" / "telegram_bot_qa.sh"
TARGET_REFUSED_EXIT = 21


@pytest.fixture
def wider_journey_harness(monkeypatch):
    from collections import deque
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from tests import test_e2e as journeys

    replies, clicks, artifacts = deque(), [], {}
    client = SimpleNamespace(get_messages=AsyncMock(return_value=[]),
                             send_message=AsyncMock(return_value=SimpleNamespace(id=1)),
                             send_file=AsyncMock(return_value=SimpleNamespace(id=2)))

    def message(text, *controls):
        buttons = []
        for label, payload in controls:
            async def click(value=payload):
                clicks.append(value)
            buttons.append(SimpleNamespace(text=label, data=payload.encode(), click=click))
        return SimpleNamespace(id=10, raw_text=text, buttons=[buttons])

    async def wait(*args, **kwargs):
        reply = replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        assert kwargs.get('reject_fingerprint') != harness.message_fingerprint(reply)
        return reply

    monkeypatch.setattr(journeys, 'assert_live_telegram_guardrails', lambda target: None)
    monkeypatch.setattr(journeys, 'BOT_USERNAME', 'portfolio_guru_test_bot')
    monkeypatch.setattr(journeys, 'allowed_bot_usernames', lambda: {'portfolio_guru_test_bot'})
    monkeypatch.setattr(journeys, 'wait_for_matching_message', wait)
    monkeypatch.setattr(journeys, 'write_transcript_artifact',
                        lambda transcript, *, filename: artifacts.update({filename: list(transcript)}))
    return journeys, client, replies, clicks, artifacts, message


KC_PREVIEW = "\n• SLO3 — Resuscitation\n  ↳ KC3: assessment\n  ↳ KC5: leadership\n• SLO7 — Complex situations\n  ↳ KC1: communication"


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'stale', 'stale-history', 'duplicate', 'dead-end', 'missing-back'])
@pytest.mark.parametrize('draft_in_place', [False, True])
async def test_form_switching_screen_checks_and_cleanup(
    wider_journey_harness, monkeypatch, fault, draft_in_place,
):
    from copy import copy
    import bot
    from models import FormTypeRecommendation
    journeys, client, replies, clicks, artifacts, message = wider_journey_harness
    monkeypatch.setattr(bot, '_get_allowed_forms', lambda _: ['CBD', 'MINI_CEX', 'DOPS', 'LAT',
                                                              'REFLECT_LOG', 'TEACH', 'QIAT'])
    monkeypatch.setattr(bot, '_effective_curriculum', lambda _: '2025')
    history, next_id = [], 10

    def screen(text, markup=None, *, replace=False):
        nonlocal next_id
        if replace:
            next_id += 1
        reply = message(text, *((b.text, b.callback_data) for row in
                                (markup.inline_keyboard if markup else []) for b in row))
        reply.id = next_id
        return reply

    def recommendation():
        return screen('Best fit: Case-Based Discussion. Pick a form.',
                      bot._build_form_choice_keyboard([
                          FormTypeRecommendation(form_type='CBD', rationale='Discussion', uuid='cbd')]))

    def capture():
        replies.extend([screen('Case captured', bot._gathering_done_keyboard(), replace=True), recommendation()])

    def categories():
        return screen('Browse supported forms (2025 curriculum):', bot._build_category_picker_keyboard(123))

    def category(slug):
        return screen(f'{bot._SLUG_TO_CAT[slug]} — pick a form:', bot._build_category_forms_keyboard(123, slug))

    def draft(code):
        return screen(f'📋 {bot._form_display_name(code)} — Draft\n' + _ready_text(),
                      bot._build_approval_keyboard(), replace=not draft_in_place)

    def cancelled():
        return screen("↩️ Cancelled. Send an anonymised case when you're ready.", replace=True)

    capture()
    replies.append(categories())
    offered = [b.callback_data.split('cat_')[1] for row in bot._build_category_picker_keyboard(123).inline_keyboard
               for b in row if 'cat_' in b.callback_data]
    for slug in offered:
        replies.extend([category(slug), categories()])
    replies.extend([recommendation(), categories(), category('CLINICAL'), draft('MINI_CEX'), cancelled()])
    capture()
    replies.extend([categories(), category('CLINICAL'), draft('CBD'), cancelled()])
    capture()
    replies.extend([cancelled(), screen('Cancelled cleanup', replace=True)])
    fake_wait = journeys.wait_for_matching_message

    async def wait(*args, **kwargs):
        reply = await fake_wait(*args, **kwargs)
        old = [m for m in history if m.id != reply.id]
        if '— Draft' in reply.raw_text and not draft_in_place:
            old = old[1:]  # _show_draft_review deletes its progress/menu bubble
        for m in old:
            m.buttons = []  # real replacement retires the previous keyboard
        history[:] = [reply, *old]
        if reply.raw_text.startswith('Browse supported') and fault:
            if fault == 'stale':
                reply.buttons[0].append(message('', ('Restart', 'CANCEL|form')).buttons[0][0])
            elif fault == 'stale-history':
                stale = message('Previous recommendation', ('Restart', 'CANCEL|form'))
                stale.id = reply.id - 1
                history.append(stale)
            elif fault == 'duplicate':
                duplicate = copy(reply)
                duplicate.id = reply.id + 100
                history.append(duplicate)
            elif fault == 'dead-end':
                reply.buttons = []
            else:
                reply.buttons[0] = [b for b in reply.buttons[0] if journeys._payload(b) != 'FORM|back']
            replies.clear()
            replies.append(screen('Cancelled cleanup', replace=True))
        client.get_messages.return_value = list(history)
        return reply

    monkeypatch.setattr(journeys, 'wait_for_matching_message', wait)
    if fault:
        reason = {'stale': 'Stale', 'stale-history': 'Stale', 'duplicate': 'Duplicate',
                  'dead-end': 'Dead end', 'missing-back': 'Dead end'}[fault]
        with pytest.raises(AssertionError, match=reason):
            await journeys._form_switching_to_cancel(client)
    else:
        await journeys._form_switching_to_cancel(client)
        assert clicks.count('CANCEL|draft') == 2
        assert clicks[-1] == 'CANCEL|form'
        assert {'FORM|CBD', 'FORM|MINI_CEX', 'FORM|back'} <= set(clicks)
        assert {f'FORM|cat_{slug}' for slug in offered} <= set(clicks)
        transitions = [e.step for e in artifacts['portfolio-guru-form-switching-transcript.json']
                       if e.step.startswith('screen:')]
        assert 'screen:edited-in-place' in transitions
        assert ('screen:edited-in-place' if draft_in_place else 'screen:replaced') in transitions
        assert not replies
    sends = [call.args[1] for call in client.send_message.call_args_list]
    assert sends.count('/cancel') == 1 and sends[-1] == '/cancel'
    if not fault:
        assert sends.count(sends[0]) == 3  # same single invented case throughout
    assert artifacts['portfolio-guru-form-switching-transcript.json'][-1].received == 'Cancelled cleanup'
    assert not any(value.startswith(('APPROVE|', 'ACTION|setup', 'ACTION|reset', 'REMIND|')) for value in clicks)


@pytest.mark.asyncio
@pytest.mark.parametrize('target,allowed', [
    ('portfolio_guru_bot', {'portfolio_guru_bot'}),
    ('portfolio_guru_test_bot', {'portfolio_guru_test_bot', 'portfolio_guru_bot'}),
])
async def test_form_switching_refuses_non_test_envelope(wider_journey_harness, monkeypatch, target, allowed):
    journeys, client, _, clicks, _, _ = wider_journey_harness
    monkeypatch.setattr(journeys, 'BOT_USERNAME', target)
    monkeypatch.setattr(journeys, 'allowed_bot_usernames', lambda: allowed)
    with pytest.raises(AssertionError, match='test-bot-only'):
        await journeys._form_switching_to_cancel(client)
    client.send_message.assert_not_awaited()
    assert not clicks


def _ready_text(date='17 Mar 2026'):
    return f'Here is your draft.\n📅 Date: {date}\nReply to change any field.' + KC_PREVIEW


@pytest.mark.asyncio
@pytest.mark.parametrize('recommendation', ['target', 'best', 'wrong-best', 'list'])
@pytest.mark.parametrize('variant', ['', '_2021'])
async def test_form_variety_selects_target_and_cancels(wider_journey_harness, recommendation, variant):
    journeys, client, replies, clicks, artifacts, message = wider_journey_harness
    target = 'FORM|TEACH' + variant
    offered = ('Teaching Session', target) if recommendation == 'target' else (
        '👨‍🏫 Teaching Session' if recommendation == 'best' else 'CBD', 'FORM|best')
    replies.extend([message('Case captured', ('Choose form', 'GATHER|done')),
                    message('Forms that fit', offered, ('Forms', 'FORM|show_all'))])
    use_list = recommendation in {'wrong-best', 'list'}
    if use_list:
        replies.extend([message('Categories', ('Learning', 'FORM|cat_TEACHING')),
                        message('Learning forms', ('Teaching Session', target))])
    replies.extend([message(_ready_text(), ('Save to Kaizen', 'APPROVE|draft|abc123'),
                            ('Cancel', 'CANCEL|draft|abc123')), message('Cancelled cleanup')])
    await journeys._form_variety_ready_draft_to_cancel(
        client, 'TEACH', 'Synthetic teaching session.', prefer_recommendation=recommendation != 'list')
    assert clicks == ['GATHER|done'] + (
        ['FORM|show_all', 'FORM|cat_TEACHING', target] if use_list else
        [target if recommendation == 'target' else 'FORM|best'])
    assert [call.args[1] for call in client.send_message.call_args_list] == [
        'Synthetic teaching session.', '/cancel']
    assert artifacts['portfolio-guru-form-variety-TEACH-transcript.json'][-1].received == 'Cancelled cleanup'
    assert not replies


@pytest.mark.asyncio
@pytest.mark.parametrize('gap', ['Level of supervision',
                                'your reflection (what you learned or would do differently)',
                                'Level of supervision and patient identifier', None])
async def test_form_variety_gap_allowlist_and_failure_cleanup(wider_journey_harness, gap):
    journeys, client, replies, clicks, artifacts, message = wider_journey_harness
    replies.extend([message('Case captured', ('Choose form', 'GATHER|done')),
                    message('Teaching', ('Teaching Session', 'FORM|TEACH')),
                    TimeoutError('draft timeout') if gap is None else
                    message(f'Draft. Still needed: {gap}. Reply with this detail.',
                            ('Save draft to Kaizen', 'APPROVE|draft'), ('Cancel', 'ACTION|cancel'))])
    known = gap in {'Level of supervision', 'your reflection (what you learned or would do differently)'}
    if known:
        replies.append(message(_ready_text(), ('Save to Kaizen', 'APPROVE|draft'), ('Cancel', 'ACTION|cancel')))
    replies.append(message('Cancelled cleanup'))
    if known:
        await journeys._form_variety_ready_draft_to_cancel(client, 'TEACH', 'Synthetic teaching.', prefer_recommendation=True)
    else:
        with pytest.raises((AssertionError, TimeoutError)):
            await journeys._form_variety_ready_draft_to_cancel(client, 'TEACH', 'Synthetic teaching.', prefer_recommendation=True)
    sends = [call.args[1] for call in client.send_message.call_args_list]
    assert sends.count('/cancel') == 1 and sends[-1] == '/cancel'
    assert len(sends) == (3 if known else 2)
    assert clicks == ['GATHER|done', 'FORM|TEACH']
    assert artifacts['portfolio-guru-form-variety-TEACH-transcript.json'][-1].received == 'Cancelled cleanup'
    assert not replies


@pytest.mark.asyncio
async def test_form_variety_pdf_uses_document_intent_and_non_cbd_form(wider_journey_harness, tmp_path):
    journeys, client, replies, clicks, artifacts, message = wider_journey_harness
    text = 'Synthetic teaching: I taught a simulation session on team briefing.'
    pdf = journeys._synthetic_pdf(tmp_path / 'teaching.pdf', text)
    replies.extend([message('Read this document?', ('Read text', 'DOCUSE|info')),
                    message('Case captured', ('Choose form', 'GATHER|done')),
                    message('Forms that fit', ('Forms', 'FORM|show_all')),
                    message('Categories', ('Learning', 'FORM|cat_TEACHING')),
                    message('Learning forms', ('Teaching Session', 'FORM|TEACH')),
                    message(_ready_text(), ('Save to Kaizen', 'APPROVE|draft'), ('Cancel', 'ACTION|cancel')),
                    message('Cancelled cleanup')])
    await journeys._form_variety_ready_draft_to_cancel(
        client, 'TEACH', text, prefer_recommendation=False, document=pdf)
    assert clicks == ['DOCUSE|info', 'GATHER|done', 'FORM|show_all', 'FORM|cat_TEACHING', 'FORM|TEACH']
    assert [call.args[1] for call in client.send_message.call_args_list] == ['/cancel']
    assert client.send_file.call_args.args[1] == str(pdf)
    assert client.send_file.call_args.kwargs['force_document'] is True
    reader = pytest.importorskip('pypdf').PdfReader(pdf)
    assert text in reader.pages[0].extract_text()
    assert journeys.SYNTHETIC_CASE not in reader.pages[0].extract_text()
    assert artifacts['portfolio-guru-form-variety-TEACH-pdf-transcript.json'][-1].received == 'Cancelled cleanup'


@pytest.mark.asyncio
@pytest.mark.parametrize('target,allowed', [
    ('portfolio_guru_bot', {'portfolio_guru_bot'}),
    ('portfolio_guru_test_bot', {'portfolio_guru_test_bot', 'portfolio_guru_bot'}),
])
async def test_form_variety_direct_call_refuses_non_test_envelope(wider_journey_harness, monkeypatch, target, allowed):
    journeys, client, _, clicks, _, _ = wider_journey_harness
    monkeypatch.setattr(journeys, 'BOT_USERNAME', target)
    monkeypatch.setattr(journeys, 'allowed_bot_usernames', lambda: allowed)
    with pytest.raises(AssertionError, match='test-bot-only'):
        await journeys._form_variety_ready_draft_to_cancel(client, 'TEACH', 'Synthetic teaching.', prefer_recommendation=True)
    client.send_message.assert_not_awaited()
    client.send_file.assert_not_awaited()
    assert not clicks


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['LAT', 'TEACH', 'QIAT', 'MGMT_ROTA', 'SERIOUS_INC',
                                 'PROC_LOG', 'US_CASE', 'FORMAL_COURSE', 'REFLECT_LOG'])
@pytest.mark.parametrize('curriculum', ['2025', '2021'])
async def test_form_variety_navigates_real_bot_keyboards(wider_journey_harness, monkeypatch, code, curriculum):
    import bot
    journeys, client, replies, clicks, _, message = wider_journey_harness
    actual = bot._form_type_for_curriculum(code, curriculum)
    monkeypatch.setattr(bot, '_get_allowed_forms', lambda _: [actual])
    monkeypatch.setattr(bot, '_effective_curriculum', lambda _: curriculum)
    category = journeys.FORM_VARIETY_CASES[code][0]

    def keyboard(text, markup):
        return message(text, *((b.text, b.callback_data) for row in markup.inline_keyboard for b in row))

    replies.extend([message('Case captured', ('Choose form', 'GATHER|done')),
                    keyboard('Forms that fit', bot._build_form_choice_keyboard([], curriculum)),
                    keyboard('Categories', bot._build_category_picker_keyboard(123)),
                    keyboard('Forms', bot._build_category_forms_keyboard(123, category)),
                    message(_ready_text(), ('Save to Kaizen', 'APPROVE|draft'), ('Cancel', 'ACTION|cancel')),
                    message('Cancelled cleanup')])
    await journeys._form_variety_ready_draft_to_cancel(
        client, code, journeys._form_variety_case(code), prefer_recommendation=True)
    assert clicks == ['GATHER|done', 'FORM|show_all', f'FORM|cat_{category}', f'FORM|{actual}']
    assert client.send_message.call_args.args[1] == '/cancel'
    assert not replies


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['text', 'photo', 'voice', 'document'])
@pytest.mark.parametrize('gap', [False, True])
async def test_wider_clinical_edits_a_field_before_cleanup(wider_journey_harness, tmp_path, kind, gap):
    journeys, client, replies, clicks, artifacts, message = wider_journey_harness
    if kind in {'photo', 'document'}:
        replies.append(message('Read this note?', ('Read text', 'DOCUSE|info')))
    replies.extend([message('Case captured', ('Choose form', 'GATHER|done')),
                    message('Forms that fit', ('CBD', 'FORM|CBD'))])
    if gap:
        replies.append(message('Draft. Still needed: Level of supervision. Reply with this detail.',
                               ('Save draft to Kaizen', 'APPROVE|draft'), ('Cancel', 'ACTION|cancel')))
    replies.extend([message(_ready_text(), ('Save to Kaizen', 'APPROVE|draft|abc123'), ('Cancel', 'CANCEL|draft|abc123')),
                    message(_ready_text('18 Mar 2026'), ('Save to Kaizen', 'APPROVE|draft'), ('Cancel', 'ACTION|cancel')),
                    message('Cancelled cleanup')])
    await journeys._media_ready_draft_to_cancel(client, tmp_path / 'synthetic-media', kind)
    assert clicks == (['DOCUSE|info'] if kind in {'photo', 'document'} else []) + ['GATHER|done', 'FORM|CBD']
    sends = [call.args[1] for call in client.send_message.call_args_list]
    assert sends.count('/cancel') == 1 and sends[-1] == '/cancel'
    assert sum(value.startswith('Change only the encounter date') for value in sends) == 1
    assert any(value.startswith('Level of supervision: indirect') for value in sends) == gap
    if kind == 'text':
        assert sends[0] == journeys.SYNTHETIC_CASE
        client.send_file.assert_not_awaited()
    else:
        assert client.send_file.call_args.kwargs['voice_note'] == (kind == 'voice')
        assert client.send_file.call_args.kwargs['force_document'] == (kind == 'document')
    transcript = artifacts[f'portfolio-guru-{kind}-transcript.json']
    assert any(exchange.action.startswith('edit:') and '18 Mar 2026' in exchange.received for exchange in transcript)
    assert transcript[-1].received == 'Cancelled cleanup'
    assert not replies


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['photo', 'document', 'voice'])
@pytest.mark.parametrize('gathering', [True, False])
@pytest.mark.parametrize('edit_in_place', [True, False])
async def test_wider_media_real_reply_shapes_and_history_matching(
    wider_journey_harness, monkeypatch, tmp_path, kind, gathering, edit_in_place,
):
    """Use the real poller: progress edits, capture, form choice, review.

    Reply shapes come from handle_document_intent, handle_case_input,
    _gathering_reply and _build_explicit_form_keyboard in bot.py.
    """
    from unittest.mock import AsyncMock
    from message_policy import render_message
    journeys, client, _, clicks, artifacts, message = wider_journey_harness
    histories = []

    def observed(text, *controls, id=10):
        reply = message(text, *controls)
        reply.id, reply.out, reply.reply_markup = id, False, None
        return reply

    reset = observed('↩️ Cancelled. Send an anonymised case when you’re ready.')
    histories.append([reset])
    current_id = 10 if edit_in_place else 20
    if kind != 'voice':
        intent = observed(
            '📷 Image received — how would you like to use it?' if kind == 'photo'
            else '📄 How would you like to use this document?',
            ('📝 Use as case', 'DOCUSE|info'),
            ('📎 Attach as evidence', 'DOCUSE|attach'), ('📎 Read + attach', 'DOCUSE|both'),
            ('❌ Remove file', 'DOCUSE|ignore'), id=current_id,
        )
        histories.append([intent, reset])
    else:
        intent = reset
    progress = observed('🎙️ Voice note read. Finding matching forms…' if kind == 'voice'
                        else f'📄 Reading {kind}…', id=current_id)
    # Old read buttons are still returned during processing; they must not
    # cause a second click. Progress itself has no actionable buttons.
    histories.append([progress, intent])
    if gathering:
        captured = observed(render_message('gathering_captured'),
                            ('📋 Choose form', 'GATHER|done'),
                            ('❌ Cancel', 'ACTION|cancel'), id=current_id)
        histories.append([captured, progress])
    form_id = current_id if edit_in_place else current_id + 1
    choice = observed('I’ll use *Case-Based Discussion* for this entry.\n\n'
                      'Select the form below to draft from what you sent.',
                      ('🩺 Case-based discussion', 'FORM|CBD_2021'),
                      ('📋 Forms', 'FORM|show_all'), ('❌ Cancel', 'ACTION|cancel'), id=form_id)
    histories.append([choice, progress])
    review = observed(_ready_text(),
                      ('💾 Save to Kaizen', 'APPROVE|draft'), ('❌ Cancel', 'ACTION|cancel'),
                      id=form_id if edit_in_place else form_id + 1)
    if gathering:
        refreshed = observed(_ready_text('18 Mar 2026'), ('💾 Save to Kaizen', 'APPROVE|draft'),
                             ('❌ Cancel', 'ACTION|cancel'), id=review.id if edit_in_place else review.id + 1)
        histories.extend([[review, choice], [refreshed, review]])
    histories.append([observed('↩️ Cancelled. Cleanup.', id=40)])
    client.get_messages = AsyncMock(side_effect=histories)
    monkeypatch.setattr(journeys, 'wait_for_matching_message', harness.wait_for_matching_message)
    if gathering:
        await journeys._media_ready_draft_to_cancel(client, tmp_path / 'synthetic-media', kind)
    else:
        with pytest.raises(AssertionError, match='GATHER'):
            await journeys._media_ready_draft_to_cancel(client, tmp_path / 'synthetic-media', kind)
    assert clicks == ((['DOCUSE|info'] if kind != 'voice' else [])
                      + (['GATHER|done', 'FORM|CBD_2021'] if gathering else []))
    assert client.get_messages.call_count == len(histories)
    assert artifacts[f'portfolio-guru-{kind}-transcript.json'][-1].received.endswith('Cleanup.')


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_reply', ['wrong-save', 'wrong-cancel', 'extra-control', 'unknown-gap', 'timeout'])
async def test_wider_media_failure_retains_transcript_and_cleans_up(wider_journey_harness, tmp_path, bad_reply):
    journeys, client, replies, clicks, artifacts, message = wider_journey_harness
    controls = [('Save to Kaizen', 'APPROVE|draft'), ('Cancel', 'ACTION|cancel')]
    text = 'Ready draft'
    if bad_reply == 'wrong-save': controls[0] = ('Save to Kaizen', 'APPROVE|submit')
    if bad_reply == 'wrong-cancel': controls[1] = ('Cancel', 'ACTION|delete')
    if bad_reply == 'extra-control': controls.append(('Submit', 'APPROVE|submit'))
    if bad_reply == 'unknown-gap':
        text = 'Still needed: Patient presentation. Reply with this detail.'
        controls[0] = ('Save draft to Kaizen', 'APPROVE|draft')
    replies.extend([message('Case captured', ('Choose form', 'GATHER|done')),
                    message('Choose CBD', ('CBD', 'FORM|CBD')),
                    TimeoutError() if bad_reply == 'timeout' else message(text, *controls),
                    message('Cancelled cleanup')])
    with pytest.raises((AssertionError, TimeoutError)):
        await journeys._media_ready_draft_to_cancel(client, tmp_path / 'synthetic-media', 'photo')
    assert clicks == ['GATHER|done', 'FORM|CBD']
    assert [call.args[1] for call in client.send_message.call_args_list] == ['/cancel']
    assert artifacts['portfolio-guru-photo-transcript.json'][-1].received == 'Cancelled cleanup'


@pytest.mark.asyncio
async def test_wider_settings_traverses_views_and_back_only(wider_journey_harness):
    journeys, client, replies, clicks, artifacts, message = wider_journey_harness
    def settings():
        return message('Settings', ('Portfolio defaults', 'ACTION|portfolio_defaults'),
                       ('Reminders', 'REMIND|menu'), ('Writing style', 'ACTION|voice'),
                       ('Reset data', 'ACTION|delete'), ('Connect Kaizen', 'ACTION|setup'))
    def defaults():
        return message('Portfolio defaults', ('Portfolio', 'ACTION|change_level'),
                       ('Pathway', 'ACTION|change_pathway'), ('Curriculum', 'ACTION|change_curriculum'),
                       ('Back', 'ACTION|settings'))
    def sources():
        return message('Writing style setup', ('Manual examples', 'VOICE|path_manual'),
                       ('Kaizen entries', 'VOICE|path_kaizen'), ('Remove profile', 'VOICE|remove'),
                       ('Back', 'VOICE|back_to_settings'))
    replies.extend([settings(), defaults()])
    for title, option in [('Portfolio', 'SETLEVEL|HIGHER'), ('Pathway', 'PATHWAY_SETTINGS|training'),
                          ('Curriculum', 'SET_CURRICULUM|2021')]:
        replies.extend([message(title, ('Change value', option), ('Back', 'ACTION|portfolio_defaults')), defaults()])
    replies.extend([settings(), message('Reminders', ('On/Off', 'REMIND|toggle'), ('Back', 'ACTION|settings')),
                    settings(), sources(), message('Add examples manually', ('Back', 'VOICE|back_to_choice')),
                    sources(), settings(), message('Cancelled cleanup')])
    await journeys.test_e2e_settings_read_only_journey(client)
    assert clicks == ['ACTION|portfolio_defaults', 'ACTION|change_level', 'ACTION|portfolio_defaults',
                      'ACTION|change_pathway', 'ACTION|portfolio_defaults', 'ACTION|change_curriculum',
                      'ACTION|portfolio_defaults', 'ACTION|settings', 'REMIND|menu', 'ACTION|settings',
                      'ACTION|voice', 'VOICE|path_manual', 'VOICE|back_to_choice', 'VOICE|back_to_settings']
    assert [call.args[1] for call in client.send_message.call_args_list] == ['/settings', '/cancel']
    assert artifacts['portfolio-guru-settings-transcript.json'][-1].received == 'Cancelled cleanup'
    assert not replies


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', ['APPROVE|draft', 'ACTION|setup', 'ACTION|delete', 'REMIND|toggle',
                                     'SETLEVEL|HIGHER', 'PATHWAY_SETTINGS|training', 'SET_CURRICULUM|2021',
                                     'VOICE|path_kaizen', 'VOICE|remove'])
async def test_wider_navigation_refuses_value_changes_even_with_safe_label(wider_journey_harness, payload):
    journeys, client, replies, clicks, artifacts, message = wider_journey_harness
    with pytest.raises(AssertionError, match='Protected or unreviewed'):
        await journeys._wider_click(client, [], message('Settings', ('Back', payload)), payload)
    assert not clicks


def test_wider_synthetic_media_are_deterministic_and_pdf_text_is_readable(tmp_path):
    from PIL import Image
    from tests import test_e2e as journeys
    photo = journeys._synthetic_photo(tmp_path / 'note.jpg')
    first = photo.read_bytes()
    assert journeys._synthetic_photo(photo).read_bytes() == first
    with Image.open(photo) as image:
        assert image.format == 'JPEG' and image.size == (1400, 1000)
    pdf = journeys._synthetic_pdf(tmp_path / 'note.pdf')
    first = pdf.read_bytes()
    assert journeys._synthetic_pdf(pdf).read_bytes() == first
    # The application has an optional pypdf text-extraction path. Do not add a
    # parser dependency just for this local fixture check.
    reader = pytest.importorskip('pypdf', reason='Optional PDF text parser unavailable').PdfReader(pdf)
    assert len(reader.pages) == 1
    assert ' '.join(reader.pages[0].extract_text().split()) == journeys.SYNTHETIC_CASE


def test_wider_voice_skips_clearly_without_local_tools(monkeypatch, tmp_path):
    from tests import test_e2e as journeys
    monkeypatch.setattr(journeys.shutil, 'which', lambda name: None)
    with pytest.raises(pytest.skip.Exception, match='local macOS say and ffmpeg'):
        journeys._synthetic_voice(tmp_path / 'note.ogg')


def test_wider_local_voice_is_deterministic_ogg_opus(tmp_path):
    from tests import test_e2e as journeys
    voice = journeys._synthetic_voice(tmp_path / 'note.ogg')
    first = voice.read_bytes()
    assert first.startswith(b'OggS') and b'OpusHead' in first
    assert journeys._synthetic_voice(voice).read_bytes() == first


def _set_base_live_env(monkeypatch):
    monkeypatch.setenv("TELETHON_SESSION", "session")
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")


def test_live_env_requires_explicit_approval(monkeypatch):
    _set_base_live_env(monkeypatch)

    assert harness.has_telethon_env() is False
    with pytest.raises(RuntimeError, match="explicitly approves"):
        harness.assert_live_telegram_guardrails()


def test_live_env_allows_default_portfolio_bot_after_approval(monkeypatch):
    _set_base_live_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_LIVE_APPROVED", harness.LIVE_APPROVAL_VALUE)

    assert harness.has_telethon_env() is True
    harness.assert_live_telegram_guardrails()


def test_live_env_blocks_non_allowlisted_bot(monkeypatch):
    _set_base_live_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_LIVE_APPROVED", harness.LIVE_APPROVAL_VALUE)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "unrelated_bot")

    assert harness.has_telethon_env() is False
    with pytest.raises(RuntimeError, match="not allowlisted"):
        harness.assert_live_telegram_guardrails()


def test_live_env_accepts_explicit_allowlisted_bot(monkeypatch):
    _set_base_live_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_LIVE_APPROVED", harness.LIVE_APPROVAL_VALUE)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "@portfolio_guru_staging_bot")
    monkeypatch.setenv("TELEGRAM_LIVE_ALLOWED_BOTS", "portfolio_guru_bot,portfolio_guru_staging_bot")

    assert harness.has_telethon_env() is True
    harness.assert_live_telegram_guardrails()


def test_guardrails_refuse_runtime_target_mismatch(monkeypatch):
    _set_base_live_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_LIVE_APPROVED", harness.LIVE_APPROVAL_VALUE)

    with pytest.raises(RuntimeError, match="Refusing to send"):
        harness.assert_live_telegram_guardrails("@different_bot")


# --- the approved live target must survive the QA script's own dotenv load ---
#
# scripts/telegram_bot_qa.sh reads backend/.env after it starts. Before this
# guard, a TELEGRAM_BOT_USERNAME in that file silently replaced the target the
# release approval named, so an approved live proof could have messaged a
# different bot. These run the real script; each one exits at the guard, before
# any pytest step and long before anything live.


def _fake_backend(tmp_path, env_lines):
    backend = tmp_path / "backend"
    (backend / "venv" / "bin").mkdir(parents=True)
    # Give the script a real interpreter for its dotenv reader without letting it
    # find this repo's backend.
    (backend / "venv" / "bin" / "python3").symlink_to(sys.executable)
    (backend / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    return backend



def _run_bot_qa(tmp_path, env_lines, **env):
    _fake_backend(tmp_path, env_lines)
    return subprocess.run(
        ["bash", str(BOT_QA)],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
            "PORTFOLIO_GURU_APP_DIR": str(tmp_path),
            "TELEGRAM_BOT_QA_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
            # This empty fake backend tests shell guards, not pytest plugins.
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            **env,
        },
    )


def test_dotenv_cannot_redirect_an_approved_live_target(tmp_path):
    result = _run_bot_qa(
        tmp_path,
        ["TELEGRAM_BOT_USERNAME=attacker_bot"],
        RELEASE_LIVE_TARGET="portfolio_guru_bot",
        TELEGRAM_BOT_USERNAME="portfolio_guru_bot",
    )

    assert result.returncode == TARGET_REFUSED_EXIT
    assert "changed the live Telegram target" in result.stderr
    assert "@attacker_bot" in result.stderr
    assert "Nothing was sent" in result.stderr
    assert "Running" not in result.stdout, "it must refuse before running any step"


def test_dotenv_cannot_narrow_the_allowlist_out_from_under_an_approved_target(tmp_path):
    result = _run_bot_qa(
        tmp_path,
        ["TELEGRAM_BOT_USERNAME=portfolio_guru_bot", "TELEGRAM_LIVE_ALLOWED_BOTS=some_other_bot"],
        RELEASE_LIVE_TARGET="portfolio_guru_bot",
        TELEGRAM_BOT_USERNAME="portfolio_guru_bot",
    )

    assert result.returncode == TARGET_REFUSED_EXIT
    assert "not on the allowlist" in result.stderr
    assert "Nothing was sent" in result.stderr
    assert "Running" not in result.stdout


def test_matching_dotenv_target_and_allowlist_are_accepted(tmp_path):
    result = _run_bot_qa(
        tmp_path,
        [
            "TELEGRAM_BOT_USERNAME=@portfolio_guru_staging_bot",
            "TELEGRAM_LIVE_ALLOWED_BOTS=portfolio_guru_bot, portfolio_guru_staging_bot",
        ],
        RELEASE_LIVE_TARGET="portfolio_guru_staging_bot",
        TELEGRAM_BOT_USERNAME="portfolio_guru_staging_bot",
    )

    assert result.returncode != TARGET_REFUSED_EXIT
    assert "Running collect-live-tests" in result.stdout


def test_the_guard_is_scoped_to_release_proofs(tmp_path):
    """Without an approved target there is nothing to enforce, and the script's
    own direct-call guard is unchanged."""
    result = _run_bot_qa(tmp_path, ["TELEGRAM_BOT_USERNAME=some_local_bot"])

    assert result.returncode != TARGET_REFUSED_EXIT
    assert "Running collect-live-tests" in result.stdout


@pytest.mark.parametrize("key,value", [
    ("APPROVED_LIVE_TARGET", "attacker_bot"),
    ("APPROVED_LIVE_ALLOWLIST", "attacker_bot"),
    ("FOCUSED_RELEASE", "0"), ("PY", "/bin/false"),
    ("DEFAULT_LIVE_ALLOWLIST", "attacker_bot"),
    ("BASH_ENV", "/tmp/not-a-real-file"),
])
def test_dotenv_cannot_replace_captured_approval_or_execution_controls(tmp_path, key, value):
    result = _run_bot_qa(
        tmp_path,
        [f"{key}={value}", "TELEGRAM_BOT_USERNAME=attacker_bot", "TELEGRAM_LIVE_ALLOWED_BOTS=attacker_bot"],
        RELEASE_LIVE_TARGET="portfolio_guru_bot",
        RELEASE_LIVE_ALLOWLIST="portfolio_guru_bot",
        TELEGRAM_BOT_USERNAME="portfolio_guru_bot",
    )
    assert result.returncode == TARGET_REFUSED_EXIT
    assert "Nothing was sent" in result.stderr
    assert "Running" not in result.stdout


class _FakeButton:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text, buttons=(), *, message_id=1, out=False):
        self.id = message_id
        self.raw_text = text
        self.out = out
        self.buttons = [[_FakeButton(label) for label in row] for row in buttons]
        self.reply_markup = bool(buttons)


class _FakeClient:
    def __init__(self, history_batches):
        self.history_batches = list(history_batches)

    async def get_messages(self, chat_id, limit=5):
        if len(self.history_batches) > 1:
            return self.history_batches.pop(0)
        return self.history_batches[0]


class _FlakyThenWorkingClient:
    """Raises a transient network error once, then serves the given message."""

    def __init__(self, error, message):
        self._error = error
        self._raised = False
        self._message = message

    async def get_messages(self, chat_id, limit=5):
        if not self._raised:
            self._raised = True
            raise self._error
        return [self._message]


class _AlwaysRaisingClient:
    def __init__(self, error):
        self._error = error

    async def get_messages(self, chat_id, limit=5):
        raise self._error


def test_matches_expectation_requires_expected_text_and_button():
    step = harness.TelegramStep(
        name="case",
        message="case",
        expect_text_any=("CBD", "Case-Based"),
        expect_button_any=("Use best fit",),
    )
    message = _FakeMessage("This looks suitable for CBD", (("Use best fit", "See all forms"),))

    assert harness._matches_expectation(message, step) is True


def test_chase_live_expectation_matches_real_handler_response():
    code = '''import asyncio, json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import bot
async def main():
    reply_text = AsyncMock()
    await bot.unknown_command(SimpleNamespace(message=SimpleNamespace(reply_text=reply_text)), SimpleNamespace())
    reply_text.assert_awaited_once()
    print(json.dumps(reply_text.await_args_list[0].args[0]))
asyncio.run(main())'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=REPO_ROOT / "backend",
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHON_DOTENV_DISABLED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "DATABASE_URL": "sqlite://",
            "TELEGRAM_BOT_TOKEN": "0:FAKE",
            "GOOGLE_API_KEY": "fake",
            "FERNET_SECRET_KEY": "5Wv33F9sq99WGD2lEzwwd3J_JH5p6vxKdDiAwCWqoYQ=",
        },
    )
    actual_text = json.loads(result.stdout.splitlines()[-1])
    step = harness.TelegramStep(
        name="command:chase",
        message="/chase",
        expect_text_any=command_expectation("chase"),
    )
    assert harness._matches_expectation(_FakeMessage(actual_text), step) is True


def test_matches_expectation_blocks_forbidden_text_and_buttons():
    step = harness.TelegramStep(
        name="case",
        message="case",
        forbid_text_any=("traceback",),
        forbid_button_any=("danger",),
    )

    assert harness._matches_expectation(_FakeMessage("traceback shown", (("Use best fit",),)), step) is False
    assert harness._matches_expectation(_FakeMessage("Looks fine", (("Danger action",),)), step) is False


def test_find_button_selects_expected_inline_button():
    message = _FakeMessage("Choose", (("Use best fit",), ("See all forms",)))

    button = harness._find_button(message.buttons, ("all forms",))

    assert button is not None
    assert button.text == "See all forms"


@pytest.mark.asyncio
async def test_wait_for_matching_message_observes_edited_recent_message():
    stale = _FakeMessage("Old recommendation", (("Use best fit",),), message_id=10)
    edited = _FakeMessage("Forms that fit your case", (("See all forms",),), message_id=11)
    client = _FakeClient([
        [stale],
        [edited],
    ])

    match = await harness.wait_for_matching_message(
        client,
        "portfolio_guru_bot",
        timeout_seconds=2,
        expect_text_any=("Forms that fit",),
        expect_button_any=("See all forms",),
        min_id=11,
    )

    assert match is edited


@pytest.mark.asyncio
async def test_wait_for_matching_message_ignores_stale_pre_click_match():
    stale = _FakeMessage("Draft preview", (("Save as draft",),), message_id=20)
    fresh = _FakeMessage("Kaizen draft saved", (("File another case",),), message_id=21)
    client = _FakeClient([[stale, fresh]])

    match = await harness.wait_for_matching_message(
        client,
        "portfolio_guru_bot",
        timeout_seconds=2,
        expect_text_any=("draft",),
        expect_button_any=("File another",),
        min_id=21,
    )

    assert match is fresh


# --- fingerprint-based change detection (id + text + buttons), not id ordering alone ---
#
# Portfolio Guru's bot often edits a message in place after a button click
# rather than sending a new one, so the reply keeps the same id. Ordering on
# id alone cannot tell that edited-in-place reply apart from the identical
# pre-click message still being returned by a poll before the edit lands.


def test_message_fingerprint_changes_when_text_or_buttons_change():
    base = _FakeMessage("Ready to save your CBD draft", (("Save to Kaizen", "Cancel"),), message_id=30)
    edited_text = _FakeMessage("Ready to save your amended draft", (("Save to Kaizen", "Cancel"),), message_id=30)
    edited_buttons = _FakeMessage("Ready to save your CBD draft", (("Save to Kaizen",),), message_id=30)
    identical = _FakeMessage("Ready to save your CBD draft", (("Save to Kaizen", "Cancel"),), message_id=30)

    assert harness.message_fingerprint(base) == harness.message_fingerprint(identical)
    assert harness.message_fingerprint(base) != harness.message_fingerprint(edited_text)
    assert harness.message_fingerprint(base) != harness.message_fingerprint(edited_buttons)


@pytest.mark.asyncio
async def test_wait_for_matching_message_accepts_edited_same_id_reply():
    """A same-id in-place edit must be accepted once its fingerprint diverges
    from the known pre-click state, even though id ordering alone (min_id)
    would never distinguish it from the stale copy."""
    stale = _FakeMessage("Choose a form for this case", (("CBD", "Forms"),), message_id=40)
    edited = _FakeMessage("Draft ready — CBD", (("Save to Kaizen", "Cancel"),), message_id=40)
    client = _FakeClient([[stale], [edited]])

    match = await harness.wait_for_matching_message(
        client,
        "portfolio_guru_bot",
        timeout_seconds=2,
        expect_text_any=("Draft ready",),
        expect_button_any=("Save to Kaizen",),
        reject_fingerprint=harness.message_fingerprint(stale),
    )

    assert match is edited


@pytest.mark.asyncio
async def test_wait_for_matching_message_rejects_stale_pre_click_fingerprint_without_min_id():
    """Fingerprint rejection must work even with no min_id at all — proving the
    guard is not merely message-id ordering in disguise. The pre-click message
    already satisfies the text/button expectations (it is the same screen
    reappearing on a poll), so only the fingerprint match can catch it."""
    preclick = _FakeMessage("Ready to save your CBD draft", (("Save to Kaizen", "Cancel"),), message_id=50)
    client = _FakeClient([[preclick]])

    with pytest.raises(TimeoutError):
        await harness.wait_for_matching_message(
            client,
            "portfolio_guru_bot",
            timeout_seconds=1,
            expect_text_any=("Ready to save",),
            expect_button_any=("Save to Kaizen",),
            reject_fingerprint=harness.message_fingerprint(preclick),
        )


# --- parsing the rendered SLO->KC hierarchy (bot.py's `_format_curriculum_hierarchy`) ---
#
# The draft preview renders a parent "• *SLOn — label*" line followed by one or
# more child "  ↳ KCm: summary" lines. A regex like r"SLO\w*\s*KC\d+" cannot
# match this — the SLO and KC live on separate lines. `parse_visible_kc_selections`
# reconstructs the (SLO number, KC number) pairs a doctor actually sees.

_REALISTIC_HIERARCHY_TEXT = (
    "📚 *Curriculum:*\n"
    "• *SLO3 — Resuscitation & stabilisation*\n"
    "  ↳ KC2: recognising and escalating a deteriorating patient\n"
    "• *SLO8 — Lead the ED shift*\n"
    "  ↳ KC1: delegating tasks to the team\n"
    "• *SLO7 — Complex & challenging situations*\n"
    "  ↳ KC1: communicating uncertainty to patients and family\n"
)


def test_parse_visible_kc_selections_reads_hierarchy_pairs_in_order():
    pairs = harness.parse_visible_kc_selections(_REALISTIC_HIERARCHY_TEXT)

    assert pairs == [(3, 2), (8, 1), (7, 1)]


def test_parse_visible_kc_selections_keeps_same_kc_number_distinct_across_slos():
    # SLO8 KC1 and SLO7 KC1 share a KC number but are different canonical pairs.
    pairs = harness.parse_visible_kc_selections(_REALISTIC_HIERARCHY_TEXT)

    assert len(set(pairs)) == 3
    assert (8, 1) in pairs and (7, 1) in pairs and (8, 1) != (7, 1)


def test_parse_visible_kc_selections_ignores_kc_mentions_outside_hierarchy_lines():
    text = (
        "Reflection: I want to develop KC3 further next time.\n"
        "• *SLO3 — Resuscitation & stabilisation*\n"
        "  ↳ KC2: recognising and escalating a deteriorating patient\n"
    )

    pairs = harness.parse_visible_kc_selections(text)

    assert pairs == [(3, 2)]


def test_parse_visible_kc_selections_detects_duplicate_child_lines():
    """A rendering bug that repeats the same child line under one parent must
    be visible as a duplicate pair, not silently deduplicated by the parser
    itself — callers decide whether duplicates are acceptable."""
    text = (
        "• *SLO3 — Resuscitation & stabilisation*\n"
        "  ↳ KC2: recognising and escalating a deteriorating patient\n"
        "  ↳ KC2: recognising and escalating a deteriorating patient\n"
    )

    pairs = harness.parse_visible_kc_selections(text)

    assert pairs == [(3, 2), (3, 2)]
    assert len(pairs) != len(set(pairs))


def test_parse_visible_kc_selections_drops_child_line_with_no_parent_yet():
    text = "  ↳ KC1: orphaned child line with no preceding SLO header\n"

    assert harness.parse_visible_kc_selections(text) == []


# --- transcript retention (reused by the focused live journey) ---


def test_write_transcript_artifact_records_sent_and_received_content(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_E2E_ARTIFACT_DIR", str(tmp_path))
    transcript = [
        harness.TelegramExchange(step="reset", action="send:/cancel", received="Cancelled.", buttons=[]),
        harness.TelegramExchange(
            step="case",
            action="send:File this as a CBD. Synthetic case only.",
            received="I'll use CBD for this entry.",
            buttons=["CBD", "Forms", "Cancel"],
        ),
        harness.TelegramExchange(
            step="case",
            action="click_button",
            received="Draft ready — CBD",
            buttons=["Save to Kaizen", "Cancel"],
            clicked_button="CBD",
        ),
        harness.TelegramExchange(
            step="cancel",
            action="click_button",
            received="Cancelled.",
            buttons=[],
            clicked_button="Cancel",
        ),
    ]

    harness.write_transcript_artifact(transcript)

    written = json.loads((tmp_path / "portfolio-guru-telegram-transcript.json").read_text())
    assert [entry["step"] for entry in written] == ["reset", "case", "case", "cancel"]
    assert written[1]["action"].startswith("send:")
    assert written[2]["clicked_button"] == "CBD"
    assert written[3]["clicked_button"] == "Cancel"
    harness.write_transcript_artifact(transcript[:1], filename="portfolio-guru-photo-transcript.json")
    assert len(json.loads((tmp_path / "portfolio-guru-photo-transcript.json").read_text())) == 1
    assert len(json.loads((tmp_path / "portfolio-guru-telegram-transcript.json").read_text())) == 4


def test_write_transcript_artifact_is_a_noop_without_artifact_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_E2E_ARTIFACT_DIR", raising=False)

    harness.write_transcript_artifact([harness.TelegramExchange(step="x", action="y", received="z")])

    assert list(tmp_path.iterdir()) == []


# --- classifying the post-click state: ready draft vs. bounded missing-essentials ---
#
# A live run on 2026-09-22 showed clicking the CBD form button can land on
# `_ask_for_missing_essentials`'s "Before I draft this, I still need: Level of
# Supervision." prompt (Cancel-only) instead of going straight to the ready
# draft. The journey has to recognise exactly these two bot.py-defined states
# and fail closed on anything else, rather than assuming Save is always next.


def test_classify_post_click_draft_state_recognises_ready_draft():
    message = _FakeMessage("Draft ready — CBD", (("Save to Kaizen", "Cancel"),))

    assert harness.classify_post_click_draft_state(message) == "ready"


def test_classify_post_click_draft_state_recognises_missing_essentials_prompt():
    message = _FakeMessage(
        "📋 Before I draft this, I still need: Level of Supervision.\n\n"
        "Send it as text, voice, photo, or a document and I'll add it to your case.",
        (("❌ Cancel",),),
    )

    assert harness.classify_post_click_draft_state(message) == "missing_essentials"


def test_classify_post_click_draft_state_rejects_missing_essentials_with_extra_buttons():
    message = _FakeMessage(
        "📋 Before I draft this, I still need: Level of Supervision.",
        (("❌ Cancel", "🔁 Retry"),),
    )

    with pytest.raises(AssertionError, match="Cancel only"):
        harness.classify_post_click_draft_state(message)


def test_classify_post_click_draft_state_rejects_unrecognised_state():
    """Neither the ready-draft marker nor the missing-essentials marker is
    present — e.g. a different bounded prompt (a missing-reflection gate) —
    so this must fail closed rather than being treated as either state."""
    message = _FakeMessage("I still need your reflection before this is ready.", (("❌ Cancel",),))

    with pytest.raises(AssertionError, match="unexpected post-click state"):
        harness.classify_post_click_draft_state(message)


def test_classify_post_click_draft_state_rejects_contradictory_message():
    """A message that somehow carries both markers must not be silently
    treated as ready — that would risk asserting Save/Cancel-only and KC
    counts against a draft that was never actually completed."""
    message = _FakeMessage(
        "📋 Before I draft this, I still need: Level of Supervision.",
        (("Save to Kaizen", "Cancel"),),
    )

    with pytest.raises(AssertionError, match="unexpected post-click state"):
        harness.classify_post_click_draft_state(message)


# --- wait_for_matching_message must not swallow every exception ---
#
# The polling loop used to wrap the whole body (get_messages call and match
# evaluation) in a bare `except Exception: pass`, so a real bug or a
# non-transient Telegram error looked exactly like an ordinary timeout. Only
# a narrow set of clearly transient network errors from `get_messages` itself
# should be retried; everything else must propagate.


@pytest.mark.asyncio
async def test_wait_for_matching_message_retries_after_transient_connection_error():
    message = _FakeMessage("Draft ready", (("Save to Kaizen",),), message_id=60)
    client = _FlakyThenWorkingClient(ConnectionError("temporary network hiccup"), message)

    match = await harness.wait_for_matching_message(
        client,
        "portfolio_guru_bot",
        timeout_seconds=2,
        expect_text_any=("Draft ready",),
        expect_button_any=("Save to Kaizen",),
    )

    assert match is message


@pytest.mark.asyncio
async def test_wait_for_matching_message_reraises_non_transient_errors():
    client = _AlwaysRaisingClient(RuntimeError("harness bug, not a network hiccup"))

    with pytest.raises(RuntimeError, match="harness bug"):
        await harness.wait_for_matching_message(
            client,
            "portfolio_guru_bot",
            timeout_seconds=1,
            expect_text_any=("anything",),
        )


def test_draft_first_gap_preview_is_a_bounded_live_state():
    message = _FakeMessage(
        "Here is your Case-Based Discussion draft:\nCase narrative.\nStill needed: Level of Supervision. Reply with it.",
        (("Save draft to Kaizen", "Cancel"),),
    )
    assert harness.classify_post_click_draft_state(message) == "draft_with_gaps"


@pytest.mark.parametrize("text,buttons", [
    ("Case narrative without a gap list", (("Save draft to Kaizen", "Cancel"),)),
    ("Still needed: Level of Supervision.", (("Save draft to Kaizen", "Save to Kaizen", "Cancel"),)),
    ("Still needed: Level of Supervision.", (("Save draft to Kaizen", "Retry"),)),
])
def test_gap_preview_classifier_rejects_incomplete_or_conflicting_controls(text, buttons):
    with pytest.raises(AssertionError):
        harness.classify_post_click_draft_state(_FakeMessage(text, buttons))


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['text', 'photo', 'voice', 'document'])
@pytest.mark.parametrize('refreshed', ['unchanged', 'wrong-date', 'missing-controls', 'timeout'])
async def test_clinical_edit_must_land_in_refreshed_preview(wider_journey_harness, tmp_path, kind, refreshed):
    journeys, client, replies, clicks, artifacts, message = wider_journey_harness
    controls = (('Save to Kaizen', 'APPROVE|draft'), ('Cancel', 'CANCEL|draft'))
    bad = TimeoutError() if refreshed == 'timeout' else message(
        _ready_text('17 Mar 2026' if refreshed == 'unchanged' else '19 Mar 2026'),
        *(controls if refreshed != 'missing-controls' else (('Retry', 'ACTION|retry'),)))
    replies.extend([message('Case captured', ('Choose form', 'GATHER|done')),
                    message('CBD', ('CBD', 'FORM|CBD')), message(_ready_text(), *controls),
                    bad, message('Cancelled cleanup')])
    with pytest.raises((AssertionError, TimeoutError)):
        await journeys._media_ready_draft_to_cancel(client, tmp_path / 'media', kind)
    assert [call.args[1] for call in client.send_message.call_args_list].count('/cancel') == 1
    assert clicks == ['GATHER|done', 'FORM|CBD']
    assert artifacts[f'portfolio-guru-{kind}-transcript.json'][-1].received == 'Cancelled cleanup'


@pytest.mark.asyncio
@pytest.mark.parametrize('cleanup_failure', [None, 'send', 'wait'])
async def test_focused_journey_keeps_original_failure_and_cleanup_transcript(wider_journey_harness, cleanup_failure):
    journeys, client, replies, clicks, artifacts, message = wider_journey_harness
    replies.extend([message('Case captured', ('Choose form', 'GATHER|done')),
                    message('CBD', ('CBD', 'FORM|CBD')), message('Unexpected state', ('Retry', 'ACTION|retry'))])
    if cleanup_failure == 'send':
        async def send(target, text, **kwargs):
            if text == '/cancel':
                raise TimeoutError('cleanup send failed')
            from types import SimpleNamespace
            return SimpleNamespace(id=1)
        client.send_message.side_effect = send
    else:
        replies.append(TimeoutError('cleanup response timed out') if cleanup_failure == 'wait' else message('Cancelled'))
    with pytest.raises(AssertionError, match='unexpected post-click state') as failure:
        await journeys.test_e2e_text_ready_draft_to_cancel_journey(client)
    assert [call.args[1] for call in client.send_message.call_args_list].count('/cancel') == 1
    transcript = artifacts['portfolio-guru-text-transcript.json']
    if cleanup_failure:
        assert 'unconfirmed' in transcript[-1].received.lower()
        assert any('Cleanup unconfirmed' in note for note in failure.value.__notes__)
    else:
        assert transcript[-1].received == 'Cancelled'


@pytest.mark.asyncio
@pytest.mark.parametrize("gap", [False, True])
async def test_focused_journey_handles_stamped_review_controls(monkeypatch, gap):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from tests import test_e2e as journey

    reset = _FakeMessage("Cancelled", message_id=1)
    choice = _FakeMessage("Choose CBD", (("CBD",),), message_id=2)
    choice.buttons[0][0].data = b"FORM|CBD"
    choice.buttons[0][0].click = AsyncMock()
    text = "Here is your draft:\n• SLO3 — Resuscitation\n  ↳ KC3: assessment\n  ↳ KC5: leadership\n• SLO7 — Complex situations\n  ↳ KC1: communication"
    ready = _FakeMessage(text, (("Save to Kaizen", "Cancel"),), message_id=4)
    for button, data in zip(ready.buttons[0], (b"APPROVE|draft|abc123", b"CANCEL|draft|abc123")):
        button.data = data
        button.click = AsyncMock()
    incomplete = _FakeMessage(text + "\nStill needed: Level of Supervision. Reply with it.", (("Save draft to Kaizen", "Cancel"),), message_id=3)
    for button, data in zip(incomplete.buttons[0], (b"APPROVE|draft|def456", b"CANCEL|draft|def456")):
        button.data = data
        button.click = AsyncMock()
    cancelled = _FakeMessage("Cancelled", message_id=5)
    send = AsyncMock()

    @asynccontextmanager
    async def conversation(*args, **kwargs):
        yield SimpleNamespace(send_message=send, get_response=AsyncMock(return_value=reset))

    client = SimpleNamespace(conversation=conversation, send_message=AsyncMock(return_value=_FakeMessage("/cancel", message_id=6)))
    responses = [choice, incomplete, ready, cancelled] if gap else [choice, ready, cancelled]
    monkeypatch.setattr(journey, "wait_for_matching_message", AsyncMock(side_effect=responses))
    record = MagicMock()
    monkeypatch.setattr(journey, "write_transcript_artifact", record)

    await journey.test_e2e_cbd_ready_draft_to_cancel_journey(client)

    ready.buttons[0][0].click.assert_not_awaited()
    ready.buttons[0][1].click.assert_awaited_once()
    client.send_message.assert_not_awaited(), "successful button cancellation needs no extra reset"
    assert any("Level of supervision: indirect" in call.args[0] for call in send.await_args_list) == gap
    assert record.call_args.args[0][-1].step == "cancel"


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_failure", [None, "send", "wait"])
async def test_focused_journey_cancels_even_when_draft_validation_fails(monkeypatch, cleanup_failure):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from tests import test_e2e as journey

    reset = _FakeMessage("Cancelled", message_id=1)
    choice = _FakeMessage("Choose CBD", (("CBD",),), message_id=2)
    choice.buttons[0][0].data = b"FORM|CBD"
    choice.buttons[0][0].click = AsyncMock()
    bad = _FakeMessage("Unexpected state", (("Retry",),), message_id=3)
    cancelled = _FakeMessage("Cancelled", message_id=5)

    @asynccontextmanager
    async def conversation(*args, **kwargs):
        yield SimpleNamespace(send_message=AsyncMock(), get_response=AsyncMock(return_value=reset))

    client = SimpleNamespace(conversation=conversation, send_message=AsyncMock(return_value=_FakeMessage("/cancel", message_id=4)))
    if cleanup_failure == "send":
        client.send_message.side_effect = TimeoutError("cleanup send failed")
    cleanup_response = TimeoutError("cleanup response timed out") if cleanup_failure == "wait" else cancelled
    monkeypatch.setattr(journey, "wait_for_matching_message", AsyncMock(side_effect=[choice, bad, cleanup_response]))
    record = MagicMock()
    monkeypatch.setattr(journey, "write_transcript_artifact", record)

    with pytest.raises(AssertionError, match="unexpected post-click state") as failure:
        await journey.test_e2e_cbd_ready_draft_to_cancel_journey(client)

    client.send_message.assert_awaited_once_with(journey.BOT_USERNAME, "/cancel")
    assert record.call_args.args[0][-1].step == "cleanup"
    if cleanup_failure:
        assert "unconfirmed" in record.call_args.args[0][-1].received.lower()
        assert "TimeoutError" in record.call_args.args[0][-1].received
        assert any("cleanup" in note.lower() for note in failure.value.__notes__)
    else:
        assert "cancelled" in record.call_args.args[0][-1].received.lower()
