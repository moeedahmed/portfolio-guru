"""Staging boundaries: owner only, no real Kaizen, no live launcher effects."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot
import filer_router
import kaizen_form_filer


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", [1, None])
async def test_allowlist_silently_stops_other_users(monkeypatch, user_id):
    from telegram.ext import ApplicationHandlerStop
    monkeypatch.setenv("PG_ALLOWED_USER_IDS", "6912896590")
    update = SimpleNamespace(effective_user=SimpleNamespace(id=user_id) if user_id else None)
    with pytest.raises(ApplicationHandlerStop):
        await bot._allowed_user_gate(update, None)


@pytest.mark.asyncio
async def test_allowlist_passes_owner_and_unset_preserves_live(monkeypatch):
    monkeypatch.setenv("PG_ALLOWED_USER_IDS", "6912896590")
    await bot._allowed_user_gate(SimpleNamespace(effective_user=SimpleNamespace(id=6912896590)), None)
    monkeypatch.delenv("PG_ALLOWED_USER_IDS")
    await bot._allowed_user_gate(SimpleNamespace(effective_user=SimpleNamespace(id=1)), None)


@pytest.mark.asyncio
async def test_offline_filing_completes_without_real_filer(monkeypatch):
    monkeypatch.setenv("PG_KAIZEN_OFFLINE", "1")
    real = AsyncMock(side_effect=AssertionError("real filer reached"))
    monkeypatch.setattr(filer_router, "_route_filing_unbounded", real)
    fields = {"case_title": "Synthetic case", "clinical_details": "Test only"}
    result = await filer_router.route_filing("kaizen", "CBD", fields, {})
    assert result["status"] == "success"
    assert result["offline"] is True
    assert result["fields"] == fields
    assert set(result["filled"]) == set(fields)
    assert not result.get("draft_url")
    real.assert_not_called()


@pytest.mark.asyncio
async def test_offline_connect_and_scan_never_open_browser(monkeypatch):
    import kaizen_sync
    monkeypatch.setenv("PG_KAIZEN_OFFLINE", "1")
    real = AsyncMock(side_effect=AssertionError("real browser reached"))
    monkeypatch.setattr(kaizen_form_filer, "_connect_cdp", real)
    monkeypatch.setattr(kaizen_sync, "_open_kaizen_session_page", real)
    assert await bot._test_kaizen_login("test@example.invalid", "fake") is True
    result = await kaizen_sync.sync_kaizen_portfolio_index_for_user(6912896590)
    assert result.offline is True
    assert result.rows_seen == 0
    real.assert_not_called()


@pytest.mark.asyncio
async def test_direct_browser_entry_refuses_offline(monkeypatch):
    monkeypatch.setenv("PG_KAIZEN_OFFLINE", "1")
    with pytest.raises(RuntimeError, match="offline"):
        await kaizen_form_filer._connect_cdp()


@pytest.mark.asyncio
async def test_registered_first_gate_drops_callbacks_and_messages(monkeypatch):
    from datetime import datetime, timezone
    from telegram import Update, Message, Chat, User, CallbackQuery
    from telegram.ext import TypeHandler
    monkeypatch.setenv('PG_ALLOWED_USER_IDS', '6912896590')
    app = bot.build_application()
    assert min(app.handlers) == -1000
    app._initialized = True
    reached = []
    async def sentinel(update, context):
        reached.append(update.update_id)
    # A later group lets the real pipeline test the stop behaviour.
    app.add_handler(TypeHandler(Update, sentinel), group=-999)
    user = User(1, 'Other', False)
    message = Message(1, datetime.now(timezone.utc), Chat(1, 'private'), from_user=user, text='private test content')
    await app.process_update(Update(1, message=message))
    await app.process_update(Update(2, callback_query=CallbackQuery('q', user, 'ci', message=message, data='ACTION|file')))
    assert reached == []


@pytest.mark.parametrize('raw', ['', ' ', '6912896590,bad', '0'])
def test_allowlist_invalid_configuration_fails_closed(monkeypatch, raw):
    monkeypatch.setenv('PG_ALLOWED_USER_IDS', raw)
    with pytest.raises(ValueError):
        bot._allowed_user_ids()


def test_staging_alert_has_fixed_test_prefix(monkeypatch):
    import ops_alert
    monkeypatch.setenv('PG_ENV', 'staging')
    assert ops_alert.render_alert('handler_error').startswith('[TEST BOT] ')
    assert ops_alert.render_alert('unknown') is None
    monkeypatch.delenv('PG_ENV')
    assert ops_alert.render_alert('handler_error') == ops_alert.ALERT_TEMPLATES['handler_error']


@pytest.fixture
def staging_launcher(tmp_path):
    import json
    import os
    import shutil
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    backend = tmp_path / 'backend'
    backend.mkdir()
    for filename in ('run_local.sh', 'staging_env.sh'):
        shutil.copy(root / 'backend' / filename, backend / filename)
    home = tmp_path / 'home'
    (home / '.openclaw').mkdir(parents=True)
    (home / '.openclaw/.bws-token').write_text('dummy')
    bins = tmp_path / 'bin'
    bins.mkdir()
    (bins / 'python3').symlink_to(sys.executable)
    bws = bins / 'bws'
    bws.write_text('#!' + sys.executable + '\n' + '''import json,os,sys
with open(os.environ['BWS_LOG'], 'a') as log: log.write(' '.join(sys.argv[1:]) + '\\n')
if sys.argv[2] == 'list':
    print(json.dumps([{'key': 'GCP_PROJECT_ID', 'value': 'synthetic-project'}]))
else:
    sid = sys.argv[3]
    if sid == '28ed5b26-6c5c-4848-b5a5-b45801555aee':
        print(json.dumps({'id': os.environ.get('TOKEN_REPLY_ID', sid), 'key': 'TELEGRAM_BOT_TOKEN_PORTFOLIO_TEST', 'value': 'fake-test-token'}))
    else: print(json.dumps({'value': 'fake'}))
''')
    bws.chmod(0o755)
    venv = backend / 'venv/bin'
    venv.mkdir(parents=True)
    python = venv / 'python3'
    python.write_text('#!/bin/bash\n/usr/bin/env > "$LAUNCH_ENV_LOG"\nprintf "%s\\n" "$*" >> "$CHILD_LOG"\n')
    python.chmod(0o755)
    env = {
        'PATH': str(bins) + ':/usr/bin:/bin', 'HOME': str(home), 'PG_ENV': 'staging',
        'PORTFOLIO_GURU_BOT_LOCK': str(tmp_path / 'staging-lock'),
        'PORTFOLIO_GURU_RUNTIME_IDENTITY': str(tmp_path / 'runtime.json'),
        'BWS_LOG': str(tmp_path / 'bws.log'), 'LAUNCH_ENV_LOG': str(tmp_path / 'env.log'),
        'CHILD_LOG': str(tmp_path / 'children.log'),
    }
    return backend, env, home


def _launch(staging_launcher, extra=None):
    import subprocess
    backend, env, home = staging_launcher
    return subprocess.run(['bash', str(backend / 'run_local.sh')], env={**env, **(extra or {})}, capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize('extra,match', [
    ({'TOKEN_REPLY_ID': 'af553b7d-5c05-418a-b80e-b405015708ed'}, 'token secret identity'),
    ({'PG_ALLOWED_USER_IDS': ''}, 'PG_ALLOWED_USER_IDS'),
    ({'PG_ALLOWED_USER_IDS': 'bad'}, 'PG_ALLOWED_USER_IDS'),
])
def test_staging_launcher_refuses_unsafe_settings(staging_launcher, extra, match):
    result = _launch(staging_launcher, extra)
    assert result.returncode != 0
    assert match in result.stdout + result.stderr
    from pathlib import Path
    assert not Path(staging_launcher[1]['CHILD_LOG']).exists()


def test_staging_launcher_refuses_live_data_and_symlink(staging_launcher):
    backend, env, home = staging_launcher
    live = home / '.openclaw/data/portfolio-guru'
    live.mkdir(parents=True)
    alias = backend.parent / 'alias'
    alias.symlink_to(live, target_is_directory=True)
    for target in (live, alias):
        result = _launch(staging_launcher, {'PORTFOLIO_GURU_DATA_DIR': str(target)})
        assert result.returncode != 0
        assert 'PORTFOLIO_GURU_DATA_DIR' in result.stderr


def test_staging_launcher_exports_only_isolated_settings(staging_launcher):
    from pathlib import Path
    backend, env, home = staging_launcher
    forbidden = {'SUPABASE_URL': 'fake', 'SUPABASE_SERVICE_ROLE_KEY': 'fake', 'STRIPE_SECRET_KEY': 'fake',
                 'PG_HEARTBEAT_URL': 'fake', 'PORTFOLIO_INBOUND_SECRET': 'fake', 'PORTFOLIO_OUTBOUND_SECRET': 'fake',
                 'OPENCLAW_GATEWAY_TOKEN': 'fake', 'DATABASE_URL': 'fake', 'USAGE_DB_PATH': 'fake',
                 'PORTFOLIO_GURU_SESSION_CACHE_DIR': str(home / 'live-cache')}
    result = _launch(staging_launcher, {**forbidden, 'PG_ENABLE_PROACTIVE': '1', 'PG_PAYMENTS_ENABLED': '1'})
    assert result.returncode == 0, result.stdout + result.stderr
    launched = dict(line.split('=', 1) for line in Path(env['LAUNCH_ENV_LOG']).read_text().splitlines() if '=' in line)
    assert not (set(forbidden) & set(launched))
    assert launched['PG_KAIZEN_OFFLINE'] == '1'
    assert launched['KAIZEN_USE_CDP'] == '0'
    assert launched['PG_PAYMENTS_ENABLED'] == '0'
    assert launched['PG_ALLOWED_USER_IDS'] == '6912896590'
    assert launched['PG_ENABLE_SIGNOFF_CHASE'] == ''
    assert launched['PG_ENABLE_PROACTIVE'] == ''
    assert launched['PG_ENABLE_PASSWORDLESS_CONNECT'] == ''
    assert launched['PYTHON_DOTENV_DISABLED'] == '1'
    assert launched['PORTFOLIO_GURU_DATA_DIR'] == str(home / '.openclaw/data/portfolio-guru-staging')
    assert Path(env['CHILD_LOG']).read_text().strip() == 'bot.py'
    requests = Path(env['BWS_LOG']).read_text()
    assert 'af553b7d-5c05-418a-b80e-b405015708ed' not in requests
    assert '4450d6ac-f7a2-4802-a27a-b428006488c9' not in requests
    assert 'secret list' in requests  # shared Vertex settings only
    assert 'TEST BOT: offline Kaizen copy' in result.stdout


@pytest.mark.parametrize('control', ['--target', 'TELEGRAM_BOT_USERNAME', 'RELEASE_LIVE_TARGET', 'TELEGRAM_LIVE_ALLOWED_BOTS'])
def test_stage_smoke_refuses_live_bot_before_any_child(tmp_path, control):
    import os
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    args = ['bash', str(root / 'scripts/stage.sh'), 'smoke', '--sha', 'a' * 40]
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path)}
    if control == '--target': args.extend([control, 'portfolio_guru_bot'])
    else: env[control] = 'portfolio_guru_bot'
    result = subprocess.run(args, env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 21
    assert 'refuses' in result.stderr or 'singleton' in result.stderr
    assert not list(tmp_path.rglob('*.json'))


def test_central_network_guard_blocks_http_and_browser_process_before_effect(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    code = '''import os,socket,subprocess
import kaizen_offline
kaizen_offline.install_network_guard()
for host in ('kaizenep.com', 'auth.kaizenep.com', 'eportfolio.rcem.ac.uk', 'connect.emgurus.com'):
    try: socket.getaddrinfo(host, 443)
    except RuntimeError as exc: assert 'offline Kaizen' in str(exc)
    else: raise AssertionError('real DNS path admitted')
try: subprocess.Popen(['/synthetic/playwright/driver/node'])
except RuntimeError as exc: assert 'offline Kaizen' in str(exc)
else: raise AssertionError('browser driver admitted')
print('guard passed before network or subprocess')
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path,
        env={'PATH': os.environ['PATH'], 'PG_KAIZEN_OFFLINE': '1', 'PYTHONPATH': str(backend)},
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'guard passed' in result.stdout


@pytest.mark.asyncio
async def test_direct_filers_and_login_refuse_offline_even_with_fake_browser(monkeypatch):
    import browser_filer
    import kaizen_offline
    monkeypatch.setenv('PG_KAIZEN_OFFLINE', '1')
    with pytest.raises(RuntimeError, match='offline Kaizen'):
        await kaizen_form_filer._login(None, 'dummy', 'dummy')
    with pytest.raises(RuntimeError, match='offline Kaizen'):
        await kaizen_form_filer.file_to_kaizen('CBD', {}, 'dummy', 'dummy')
    with pytest.raises(RuntimeError, match='offline Kaizen'):
        await browser_filer.file_with_browser_use('https://kaizenep.com', 'CBD', {}, {})
    with pytest.raises(RuntimeError, match='drafts only'):
        await filer_router.route_filing('kaizen', 'CBD', {}, {}, submit=True)
    assert all(button.url is None for row in bot._build_post_filing_keyboard('CBD', 'success').inline_keyboard for button in row)


@pytest.mark.asyncio
async def test_offline_doctor_save_flow_completes_and_labels_test_copy(monkeypatch):
    from tests.bot_simulator import BotSimulator
    from models import FormDraft
    sim = BotSimulator(user_id=6912896590)
    context = sim._make_context()
    update = sim._make_callback_update('APPROVE|draft')
    bot._store_draft(context, FormDraft(form_type='CBD', fields={'case_title': 'Synthetic test'}))
    context.user_data['case_text'] = 'Synthetic test only'
    monkeypatch.setenv('PG_KAIZEN_OFFLINE', '1')
    monkeypatch.setattr(bot, 'get_credentials', lambda _uid: ('dummy', 'dummy'))
    monkeypatch.setattr(bot, 'get_curriculum', lambda _uid: '2025')
    monkeypatch.setattr(bot, 'get_training_level', lambda _uid: 'ST5')
    monkeypatch.setattr(bot, 'get_case_history', AsyncMock(return_value=[]))
    monkeypatch.setattr(bot, 'record_case_filed', AsyncMock())
    monkeypatch.setattr(bot, 'check_can_file', AsyncMock(return_value=(True, 0, None, 'pro_plus')))
    real = AsyncMock(side_effect=AssertionError('real Kaizen filing reached'))
    monkeypatch.setattr(filer_router, '_route_filing_unbounded', real)
    result = await bot.handle_approval_approve(update, context)
    visible = '\n'.join(text for _, text, _ in sim.messages_sent if isinstance(text, str))
    assert 'offline Kaizen copy, nothing saved to Kaizen' in visible
    assert context.user_data['last_filing_status'] == 'success'
    real.assert_not_called()


@pytest.mark.parametrize('staging', [False, True])
def test_startup_schedules_no_unsolicited_jobs_in_staging(monkeypatch, staging):
    import builtins
    import io
    import portalocker
    import requests
    import ops_alert
    import stripe_handler
    from unittest.mock import MagicMock
    if staging: monkeypatch.setenv('PG_ENV', 'staging')
    else: monkeypatch.delenv('PG_ENV', raising=False)
    monkeypatch.delenv('PG_KAIZEN_OFFLINE', raising=False)
    monkeypatch.setenv('PG_ENABLE_PROACTIVE', '')
    monkeypatch.setenv('PG_ENABLE_SIGNOFF_CHASE', '')
    file = io.StringIO()
    real_open = builtins.open
    def test_open(path, *args, **kwargs):
        return file if str(path).endswith('portfolio-guru-bot.lock') else real_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', test_open)
    monkeypatch.setattr(portalocker, 'lock', lambda *args: None)
    monkeypatch.setattr(requests, 'post', MagicMock())
    monkeypatch.setattr(bot, 'init', lambda: None)
    monkeypatch.setattr(bot, 'init_profile_db', lambda: None)
    monkeypatch.setattr(bot, 'write_runtime_identity', lambda _: {'commit': 'a'*40, 'branch': 'test', 'pid': 1})
    monkeypatch.setattr(ops_alert, 'HEARTBEAT_URL', '')
    monkeypatch.setattr(stripe_handler, 'stripe_mode', lambda: 'unknown')
    monkeypatch.setattr(stripe_handler, 'log_stripe_mode', lambda: None)
    app = MagicMock()
    monkeypatch.setattr(bot, 'build_application', lambda: app)
    bot.main()
    if staging:
        app.job_queue.run_daily.assert_not_called()
        assert all(call.kwargs.get('name') == 'retention' for call in app.job_queue.run_repeating.call_args_list)
    else:
        assert app.job_queue.run_daily.call_args.kwargs['name'] == 'weekly_push'
        assert any(call.args[0].__name__ == '_supervisor_tick' for call in app.job_queue.run_repeating.call_args_list)
    app.run_polling.assert_called_once_with(drop_pending_updates=False)
    file.close()


@pytest.mark.asyncio
async def test_allowlist_drops_owner_messages_in_groups(monkeypatch):
    from telegram.ext import ApplicationHandlerStop
    monkeypatch.setenv("PG_ALLOWED_USER_IDS", "6912896590")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=6912896590),
        effective_chat=SimpleNamespace(id=-1001234567890, type="supergroup"),
    )
    with pytest.raises(ApplicationHandlerStop):
        await bot._allowed_user_gate(update, None)
    private = SimpleNamespace(
        effective_user=SimpleNamespace(id=6912896590),
        effective_chat=SimpleNamespace(id=6912896590, type="private"),
    )
    await bot._allowed_user_gate(private, None)


def test_staging_lock_cannot_point_at_live_data(staging_launcher):
    backend, env, home = staging_launcher
    live = home / '.openclaw/data/portfolio-guru'
    live.mkdir(parents=True)
    result = _launch(staging_launcher, {'PORTFOLIO_GURU_BOT_LOCK': str(live)})
    assert result.returncode != 0
    assert 'PORTFOLIO_GURU_BOT_LOCK' in result.stdout + result.stderr
    assert live.is_dir()
