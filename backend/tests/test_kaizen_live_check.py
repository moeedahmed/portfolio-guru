"""Operator-only check: all effects mocked, no real credentials or network."""
import asyncio
import inspect
import json
from unittest.mock import AsyncMock

import pytest

import kaizen_live_check as check


@pytest.mark.parametrize("env", [
    {}, {"KAIZEN_LIVE_CHECK_APPROVED": "yes"},
    {"KAIZEN_LIVE_CHECK_APPROVED": "operator-own-account", "PG_ENV": "staging"},
    {"KAIZEN_LIVE_CHECK_APPROVED": "operator-own-account", "PG_ENV": "offline"},
    {"KAIZEN_LIVE_CHECK_APPROVED": "operator-own-account", "PG_KAIZEN_OFFLINE": "true"},
])
def test_guard_refuses_before_credentials_or_browser(monkeypatch, tmp_path, env):
    for key in ("KAIZEN_LIVE_CHECK_APPROVED", "PG_ENV", "PG_KAIZEN_OFFLINE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(check.GuardRefusal):
        asyncio.run(check.run_check(check.DEFAULT_FORMS))
    assert not list(tmp_path.iterdir())


def test_no_alternate_account_cli_and_extended_form_selection():
    assert "user_id" not in inspect.signature(check.run_check).parameters
    with pytest.raises(TypeError):
        check.run_check(user_id=123)
    with pytest.raises(SystemExit):
        check.parser().parse_args(["--user-id", "123"])
    assert check.parser().parse_args([]).forms == check.DEFAULT_FORMS
    assert check.parser().parse_args(["--forms", "LAT", "MGMT_ROTA"]).forms == ["LAT", "MGMT_ROTA"]
    assert check.parser().parse_args(["--all-mapped"]).all_mapped
    with pytest.raises(SystemExit):
        check.parser().parse_args(["--forms", "CBD", "--all-mapped"])
    import bot
    assert check.OPERATOR_USER_ID == bot.ADMIN_USER_ID


def test_builder_populates_every_mapped_field_and_alias():
    import kaizen_form_filer as filer
    from extractor import schema_form_type
    for form in check.mapped_forms():
        fields = check.synthetic_fields(form)
        mapping = {**filer.COMMON_HEADER_FIELD_MAP, **filer.FORM_FIELD_MAP[form]}
        assert all(fields[key] for key in mapping), form
        assert fields["event_description"] == check.MARKER
        assert fields["key_capabilities"] == ["SLO6 KC1"]
        specs = {s['key']: s for s in filer.FORM_SCHEMAS.get(schema_form_type(form), {}).get('fields', [])}
        for key, target in mapping.items():
            spec = specs.get(key, {})
            if 'date' in key or filer._field_dom_id(target) in {'startDate', 'endDate'}:
                assert __import__('re').fullmatch(r'\d{1,2}/\d{1,2}/\d{4}', fields[key]), (form, key)
            elif spec.get('options') and key != 'stage_of_training':
                values = fields[key] if isinstance(fields[key], list) else [fields[key]]
                assert all(value in spec['options'] for value in values), (form, key)
    assert check.synthetic_fields("DOPS_2021")["procedure_name"] == check.synthetic_fields("DOPS_2021")["procedural_skill"]


def test_form_selection_is_live_catalogue_intersection():
    import bot
    import kaizen_form_filer as filer
    visible = {f for groups in (bot.TRAINING_LEVEL_FORMS, bot.FORM_CATEGORIES)
               for forms in groups.values() for f in forms}
    visible.update(bot._filter_forms_by_curriculum(sorted(visible), '2021'))
    assert set(check.mapped_forms()) == visible.intersection(filer.FORM_FIELD_MAP)
    assert len(check.mapped_forms()) == 71
    assert not {'FILE_UPLOAD', 'ASAT', 'ESLE_REFLECTION'} & set(check.mapped_forms())


@pytest.mark.parametrize('forms', [(), ('FILE_UPLOAD',), ('ASAT',), ('ESLE_REFLECTION',), ('CBD', 'unknown')])
def test_non_catalogue_forms_refused_before_credential_retrieval(monkeypatch, forms):
    import credentials
    monkeypatch.setenv('KAIZEN_LIVE_CHECK_APPROVED', 'operator-own-account')
    monkeypatch.delenv('PG_ENV', raising=False)
    monkeypatch.delenv('PG_KAIZEN_OFFLINE', raising=False)
    monkeypatch.setattr(credentials, 'get_credentials', lambda *_: pytest.fail('Retrieved credentials'))
    with pytest.raises(check.GuardRefusal, match='catalogue-visible'):
        asyncio.run(check.run_check(forms))


def test_cli_parser_does_not_import_credentials_or_bot():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c',
        "import sys; import kaizen_live_check as c; c.parser().parse_args(['--all-mapped']); "
        "assert 'credentials' not in sys.modules; assert 'bot' not in sys.modules"],
        cwd=__import__('pathlib').Path(check.__file__).parent, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('state, expected, classification', [
    ({'value': ['AAA', 'ELS']}, ['ELS', 'AAA'], 'landed'),
    ({'value': ['AAA']}, ['AAA', 'ELS'], 'mismatch'),
    ({'value': []}, ['AAA'], 'empty'),
    ({'missing': True}, ['AAA'], 'empty'),
])
def test_multiselect_readback_compares_saved_options(state, expected, classification):
    assert check.classify(state, expected)[0] == classification


def test_failure_report_cannot_echo_credentials_or_provider_error(monkeypatch, tmp_path, capsys):
    import credentials
    import kaizen_form_filer as filer
    monkeypatch.setenv("KAIZEN_LIVE_CHECK_APPROVED", "operator-own-account")
    monkeypatch.delenv("PG_ENV", raising=False)
    monkeypatch.delenv("PG_KAIZEN_OFFLINE", raising=False)
    seen = []
    def own_credentials(user_id):
        seen.append(user_id)
        return "private-login", "private-password"
    async def broken(*args, **kwargs):
        print("private-login private-password")
        assert kwargs["submit"] is False
        assert kwargs["telegram_user_id"] == check.OPERATOR_USER_ID
        assert filer.KAIZEN_USE_CDP is False
        assert __import__("os").environ["DEBUG"] == ""
        assert __import__("os").environ["PWDEBUG"] == "0"
        raise RuntimeError("private-login private-password")
    monkeypatch.setattr(credentials, "get_credentials", own_credentials)
    monkeypatch.setattr(filer, "file_to_kaizen", broken)
    original = filer._SESSION_DIR
    report, code = asyncio.run(check.run_check(("CBD",)))
    assert code == check.FAILED
    assert seen == [check.OPERATOR_USER_ID]
    assert filer._SESSION_DIR == original
    check.write_report(report, tmp_path)
    output = capsys.readouterr()
    for text in (output.out, output.err, json.dumps(report), *(p.read_text() for p in tmp_path.iterdir())):
        assert "private-login" not in text and "private-password" not in text


def test_unmapped_skip_keeps_safe_skip_reason():
    rows = check.skipped_fields("LAT", ["clinical_setting", "stage_of_training", "untrusted secret text"])
    assert rows[0]["classification"] == "not-mapped"
    assert rows[0]["reason"] == "safe_skip:lat_clinical_setting_no_dom_field"
    assert "untrusted" not in json.dumps(rows)


@pytest.mark.parametrize("url", ["http://kaizenep.com/events/fillin/a", "https://evil.invalid/events/fillin/a", "https://name:password@kaizenep.com/events/fillin/a", "https://kaizenep.com/activities"])
def test_readback_refuses_untrusted_draft_url(url):
    with pytest.raises(check.GuardRefusal):
        check.draft_url(url)


@pytest.mark.parametrize("field_classification, filer_status, code", [
    ("landed", "success", 0), ("empty", "success", check.PARTIAL),
    ("mismatch", "success", check.PARTIAL), ("landed", "partial", check.PARTIAL),
])
def test_readback_drives_exit_and_keeps_account_isolation(monkeypatch, field_classification, filer_status, code):
    import credentials
    import kaizen_form_filer as filer
    import filing_result_logger
    monkeypatch.setenv("KAIZEN_LIVE_CHECK_APPROVED", "operator-own-account")
    monkeypatch.delenv("PG_ENV", raising=False)
    monkeypatch.delenv("PG_KAIZEN_OFFLINE", raising=False)
    monkeypatch.setattr(credentials, "get_credentials", lambda uid: ("private-login", "private-password") if uid == check.OPERATOR_USER_ID else pytest.fail("Wrong account"))
    original_cache, original_log = filer._SESSION_DIR, filing_result_logger.log_filing_result
    url = "https://kaizenep.com/events/fillin/synthetic-document-1?autosave=1"
    async def file_draft(form, fields, username, password, **kwargs):
        assert filer.KAIZEN_USE_CDP is False
        assert filer._SESSION_DIR != original_cache
        assert kwargs == {"curriculum_links": ["SLO6"], "submit": False, "telegram_user_id": check.OPERATOR_USER_ID}
        # Arbitrary provider details, including passwords, are never retained.
        return {"saved_url": url, "status": filer_status, "error": password, "filing_qa": {"field_states": {"password": password}}, "skipped": []}
    monkeypatch.setattr(filer, "file_to_kaizen", file_draft)
    reader = AsyncMock(return_value=[{"field": "reflection", "classification": field_classification, "reason": "fixture"}])
    monkeypatch.setattr(check, "read_back", reader)
    report, actual_code = asyncio.run(check.run_check(("CBD",)))
    assert actual_code == code
    assert "private" not in json.dumps(report)
    assert reader.call_args.args[2] == url
    assert filer._SESSION_DIR == original_cache
    assert filing_result_logger.log_filing_result is original_log


def test_missing_stored_connection_refuses(monkeypatch):
    import credentials
    monkeypatch.setenv("KAIZEN_LIVE_CHECK_APPROVED", "operator-own-account")
    monkeypatch.delenv("PG_ENV", raising=False)
    monkeypatch.delenv("PG_KAIZEN_OFFLINE", raising=False)
    monkeypatch.setattr(credentials, "get_credentials", lambda uid: None)
    with pytest.raises(check.GuardRefusal, match="no stored password connection"):
        asyncio.run(check.run_check())


@pytest.mark.parametrize('unavailable', [True, False])
def test_profile_unavailability_is_separate_from_filing_failure(monkeypatch, tmp_path, unavailable):
    import credentials
    import kaizen_form_filer as filer
    monkeypatch.setenv('KAIZEN_LIVE_CHECK_APPROVED', 'operator-own-account')
    monkeypatch.delenv('PG_ENV', raising=False)
    monkeypatch.delenv('PG_KAIZEN_OFFLINE', raising=False)
    monkeypatch.setattr(credentials, 'get_credentials', lambda uid: ('private-login', 'private-password'))
    error = (filer._form_navigation_unavailable_error('AUDIT', 'https://kaizenep.com/events/list')
             if unavailable else 'Private provider error: private-password')
    writer = AsyncMock(return_value={'status': 'failed', 'error': error})
    monkeypatch.setattr(filer, 'file_to_kaizen', writer)
    reader = AsyncMock()
    monkeypatch.setattr(check, 'read_back', reader)
    report, code = asyncio.run(check.run_check(('AUDIT',)))
    assert code == (check.PARTIAL if unavailable else check.FAILED)
    entry = report['forms'][0]
    assert entry['status'] == ('unavailable' if unavailable else 'failed')
    assert report['status'] == entry['status']
    assert entry['draft_url'] is None
    reader.assert_not_awaited()
    assert writer.call_args.kwargs['submit'] is False
    check.write_report(report, tmp_path)
    for path in tmp_path.iterdir():
        assert 'private-' not in path.read_text()
        assert '/events/list' not in path.read_text()


def test_every_visible_form_uses_operator_only_draft_payload(monkeypatch):
    import credentials
    import kaizen_form_filer as filer
    monkeypatch.setenv('KAIZEN_LIVE_CHECK_APPROVED', 'operator-own-account')
    monkeypatch.delenv('PG_ENV', raising=False)
    monkeypatch.delenv('PG_KAIZEN_OFFLINE', raising=False)
    requested = []
    def own(user_id):
        requested.append(user_id)
        return 'private-login', 'private-password'
    monkeypatch.setattr(credentials, 'get_credentials', own)
    calls = []
    async def save(form, fields, username, password, **kwargs):
        assert kwargs['telegram_user_id'] == check.OPERATOR_USER_ID
        assert kwargs['submit'] is False
        assert fields['event_description'] == check.MARKER
        assert all(fields[key] for key in filer.FORM_FIELD_MAP[form])
        calls.append(form)
        return {'status': 'success', 'saved_url': f"https://kaizenep.com/events/fillin/synthetic-{form.replace('_', '-')}", 'skipped': []}
    monkeypatch.setattr(filer, 'file_to_kaizen', save)
    monkeypatch.setattr(check, 'read_back', AsyncMock(return_value=[
        {'field': 'event_description', 'classification': 'landed', 'reason': 'exact_value_match'}]))
    forms = check.mapped_forms()
    report, code = asyncio.run(check.run_check(forms + forms))
    assert calls == list(forms)
    assert requested == [check.OPERATOR_USER_ID]
    assert code == 0
    assert all(entry['uploads']['classification'] == 'not-applicable' for entry in report['forms'])
    assert 'private-' not in json.dumps(report)


def test_readback_accepts_real_kaizen_new_section_draft_address():
    # Shape of the address real Kaizen returned after a DOPS draft save (8 Oct 2026).
    url = "https://kaizenep.com/events/new-section/27a300c6-245a-4fed-943e-fe2976686d0d?doc=ebda7b06-b7ac-4436-8219-e128d6596882"
    assert check.draft_url(url) == url
    with pytest.raises(check.GuardRefusal):
        check.draft_url("https://kaizenep.com/events/new-section/27a300c6-245a-4fed-943e-fe2976686d0d")
