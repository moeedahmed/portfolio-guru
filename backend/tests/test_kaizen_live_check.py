"""Operator-only check: all effects mocked, no real credentials or network."""
import asyncio
import inspect
import json
from unittest.mock import AsyncMock, MagicMock

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
        assert all(fields.get(key) or check.not_applicable_reason(form, key, fields) for key in mapping), form
        assert fields["event_description"] == check.MARKER
        assert fields["key_capabilities"] == ["SLO6 KC1"]
        specs = {s['key']: s for s in filer.FORM_SCHEMAS.get(schema_form_type(form), {}).get('fields', [])}
        for key, target in mapping.items():
            spec = specs.get(key, {})
            if 'date' in key or filer._field_dom_id(target) in {'startDate', 'endDate'}:
                assert __import__('re').fullmatch(r'\d{1,2}/\d{1,2}/\d{4}', fields[key]), (form, key)
            elif spec.get('options') and key != 'stage_of_training':
                if key not in fields:
                    continue
                values = fields[key] if isinstance(fields[key], list) else [fields[key]]
                assert all(value in spec['options'] for value in values), (form, key)
    assert check.synthetic_fields("DOPS_2021")["procedure_name"] == check.synthetic_fields("DOPS_2021")["procedural_skill"]


def test_ultrasound_age_payload_matches_independent_scraped_number_control():
    from pathlib import Path
    import kaizen_form_filer as filer
    scrape = json.loads(Path(filer.__file__).with_name("unified_dom_map.json").read_text())
    age_id = filer.FORM_FIELD_MAP["US_CASE"]["patient_age"]
    assert scrape["US_CASE"]["fields"][age_id]["type"] == "number"
    assert check.synthetic_fields("US_CASE")["patient_age"] == "3"


@pytest.mark.parametrize("form", ["PROC_LOG", "PROC_LOG_2021"])
def test_procedural_check_requests_one_stage_and_only_triggered_other_detail(form):
    fields = check.synthetic_fields(form)
    assert fields["stage_of_training"] == "Higher"
    assert fields["higher_procedural_skill"] == "Paediatric sedation"
    for key in ("intermediate_procedural_skill", "accs_procedural_skill",
                "higher_procedural_skill_other", "procedure_other"):
        assert key not in fields
        assert check.not_applicable_reason(form, key, fields)
    # A real request for Other must still be read back, including both aliases.
    fields["higher_procedural_skill"] = "Other"
    for key in ("higher_procedural_skill_other", "procedure_other"):
        assert check.not_applicable_reason(form, key, fields) is None
    assert check.not_applicable_reason(form, "higher_procedural_skill", fields) is None
    assert check.not_applicable_reason("US_CASE", "patient_age", fields) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_other", [False, True])
async def test_procedural_readback_exempts_only_unrequested_scenario_fields(monkeypatch, requested_other):
    import kaizen_form_filer as filer
    fields = check.synthetic_fields("PROC_LOG")
    if requested_other:
        fields.update(higher_procedural_skill="Other", higher_procedural_skill_other="Synthetic detail",
                      procedure_other="Synthetic detail")
    page = AsyncMock()
    page.url = "https://kaizenep.com/events/fillin/synthetic-document"
    # Every control is missing: exemptions must never hide the tested Higher
    # skill, or an explicitly requested Other detail.
    page.evaluate = AsyncMock(return_value={"missing": True})
    browser = AsyncMock()
    browser.new_context.return_value.new_page.return_value = page
    pw = AsyncMock()
    pw.chromium.launch.return_value = browser
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=pw)
    manager.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(filer, "async_playwright", lambda: manager)
    monkeypatch.setattr(filer, "load_session_state", lambda uid, username: {"cookies": []})
    rows = {r["field"]: r for r in await check.read_back("PROC_LOG", fields, page.url, "fake-login")}
    assert rows["higher_procedural_skill"]["classification"] == "empty"
    assert rows["accs_procedural_skill"]["classification"] == "not-applicable"
    assert rows["intermediate_procedural_skill"]["classification"] == "not-applicable"
    for key in ("higher_procedural_skill_other", "procedure_other"):
        assert rows[key]["classification"] == ("empty" if requested_other else "not-applicable")
    browser.close.assert_awaited_once()


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


def test_curriculum_skip_rows_name_the_field_without_copying_provider_text():
    rows = check.skipped_fields("LAT", ["key_capabilities (1 not ticked)",
                                       "kc:SLO6 KC1", "untrusted secret text"])
    assert rows[0] == {"field": "key_capabilities", "classification": "not-mapped",
                       "reason": "key_capabilities_not_ticked"}
    assert rows[1] == {"field": "kc:SLO6 KC1", "classification": "empty",
                       "reason": "key_capability_not_persisted"}
    assert "untrusted" not in json.dumps(rows)


def test_schema_only_skip_names_and_kc_descriptions_are_safely_classified():
    rows = check.skipped_fields("TEACH", ["key_capabilities (not ticked)",
        "higher_procedural_skill", "kc:SLO6 KC1: private-provider-text", "key_capabilities (private-provider-text)"])
    assert [r["field"] for r in rows[:3]] == ["key_capabilities", "higher_procedural_skill", "kc:SLO6 KC1"]
    assert rows[1]["classification"] == "not-mapped"
    assert rows[-1]["field"] == "unclassified_filer_skip"
    assert "private-provider-text" not in json.dumps(rows)


def test_named_kc_skip_merges_with_readback_row_and_prevents_clean_pass(monkeypatch, tmp_path):
    import credentials
    import kaizen_form_filer as filer
    monkeypatch.setenv("KAIZEN_LIVE_CHECK_APPROVED", "operator-own-account")
    monkeypatch.delenv("PG_ENV", raising=False)
    monkeypatch.delenv("PG_KAIZEN_OFFLINE", raising=False)
    monkeypatch.setattr(credentials, "get_credentials", lambda uid: ("fake-login", "fake-password"))
    monkeypatch.setattr(filer, "file_to_kaizen", AsyncMock(return_value={
        "status": "partial", "saved_url": "https://kaizenep.com/events/fillin/synthetic-draft",
        "skipped": ["key_capabilities (1 not ticked)", "kc:SLO6 KC1"]}))
    monkeypatch.setattr(check, "read_back", AsyncMock(return_value=[
        {"field": "kc:SLO6 KC1", "classification": "empty", "reason": "value_not_persisted"}]))
    report, code = asyncio.run(check.run_check(("LAT",)))
    assert code == check.PARTIAL
    rows = report["forms"][0]["fields"]
    assert [r["field"] for r in rows] == ["kc:SLO6 KC1", "key_capabilities"]
    assert rows[0]["filer_skip_reason"] == "key_capability_not_persisted"
    check.write_report(report, tmp_path)
    assert "unclassified_filer_skip" not in (tmp_path / "summary.md").read_text()


@pytest.mark.parametrize("url", ["http://kaizenep.com/events/fillin/a", "https://evil.invalid/events/fillin/a", "https://name:password@kaizenep.com/events/fillin/a", "https://kaizenep.com/activities"])
def test_readback_refuses_untrusted_draft_url(url):
    with pytest.raises(check.GuardRefusal):
        check.draft_url(url)


@pytest.mark.parametrize("field_classification, filer_status, code", [
    ("landed", "success", 0), ("empty", "success", check.PARTIAL),
    ("not-applicable", "success", 0),
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
        assert all(fields.get(key) or check.not_applicable_reason(form, key, fields)
                   for key in filer.FORM_FIELD_MAP[form])
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


@pytest.fixture
def inspect_browser(monkeypatch):
    import kaizen_form_filer as filer
    page = AsyncMock()
    page.url = "https://kaizenep.com/events/fillin/synthetic-document"
    browser = AsyncMock()
    context = browser.new_context.return_value
    context.new_page.return_value = page
    pw = AsyncMock()
    pw.chromium.launch.return_value = browser
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=pw)
    manager.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(filer, "async_playwright", lambda: manager)
    session = MagicMock(return_value={"cookies": []})
    monkeypatch.setattr(filer, "load_session_state", session)
    for name in ("file_to_kaizen", "_login", "_save_form", "save_session_state"):
        monkeypatch.setattr(filer, name, AsyncMock(side_effect=AssertionError("Must never mutate")))
    return page, browser, context, session


@pytest.mark.asyncio
async def test_inspect_only_reads_and_blocks_writes(inspect_browser):
    page, browser, context, session = inspect_browser
    snapshot = {"trees": [{"label": "Higher curriculum", "source": "angular-model",
        "nodes": [{"label": "Higher SLO6", "selected": False},
                  {"label": "SLO6 KC1", "selected": False}]}],
        "higher_procedural_skill": {"options": [{"label": "Paediatric sedation"}],
                                    "selected_label": "Other"}}
    page.evaluate.return_value = snapshot
    result = await check.inspect_draft("PROC_LOG", page.url, "fake-login")
    assert result["observed"] == snapshot
    expected = result["expected"]
    assert expected["key_capabilities"] == ["SLO6 KC1"]
    assert expected["stage"] == "Higher"
    assert expected["higher_procedural_skill"]["payload"] == "Paediatric sedation"
    assert expected["higher_procedural_skill"]["dom_id"] == "8def931e-3a00-43ac-8529-44cdaf34be2d"
    session.assert_called_once_with(check.OPERATOR_USER_ID, "fake-login")
    for name in ("fill", "click", "check", "select_option", "press", "locator"):
        assert not getattr(page, name).called, name
    assert page.evaluate.call_args.args[0] == check.INSPECT_DRAFT_JS
    assert not __import__('re').search(r"\.(?:click|dispatchEvent|setAttribute)\s*\(|\.checked\s*=|\.value\s*=", check.INSPECT_DRAFT_JS)
    guard = context.route.call_args.args[1]
    for method, url, allowed in [
        ("GET", page.url, True), ("POST", "https://kaizenep.com/elastic/events", True),
        ("POST", "https://kaizenep.com/events/save", False),
        ("POST", "https://kaizenep.com/events/changes/delete", False),
        ("DELETE", page.url, False), ("GET", "https://evil.invalid/token", False),
    ]:
        route = AsyncMock()
        route.request.method, route.request.url = method, url
        await guard(route)
        assert route.fallback.called == allowed
        assert route.abort.called != allowed
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://evil.invalid/events/fillin/a",
    "https://kaizenep.com/events/new-section/a", "https://kaizenep.com/events/fillin/a?next=secret"])
async def test_inspect_refuses_url_before_session_or_browser(inspect_browser, url):
    _, browser, _, session = inspect_browser
    with pytest.raises(check.GuardRefusal):
        await check.inspect_draft("LAT", url, "fake-login")
    session.assert_not_called()
    browser.new_context.assert_not_called()


@pytest.mark.asyncio
async def test_inspect_refuses_redirect_and_missing_session(inspect_browser):
    page, _, _, session = inspect_browser
    session.return_value = None
    with pytest.raises(check.GuardRefusal, match="session"):
        await check.inspect_draft("LAT", page.url, "fake-login")
    session.return_value = {"cookies": []}
    url = page.url
    page.url = "https://kaizenep.com/events/fillin/other-document"
    with pytest.raises(check.GuardRefusal, match="redirected"):
        await check.inspect_draft("LAT", url, "fake-login")
    page.evaluate.assert_not_awaited()


def test_inspect_report_selection_and_private_failure(monkeypatch, tmp_path, capsys):
    import credentials
    monkeypatch.setenv("KAIZEN_LIVE_CHECK_APPROVED", "operator-own-account")
    monkeypatch.delenv("PG_ENV", raising=False)
    monkeypatch.delenv("PG_KAIZEN_OFFLINE", raising=False)
    credential_reader = MagicMock(return_value=("private-login", "private-password"))
    monkeypatch.setattr(credentials, "get_credentials", credential_reader)
    source = tmp_path / "results.json"
    source.write_text(json.dumps({"forms": [
        {"form": f, "draft_url": f"https://kaizenep.com/events/fillin/synthetic-{f.replace('_', '-')}"}
        for f in ("LAT", "PROC_LOG")]}))
    async def inspect_one(form, url, username):
        assert username == "private-login"
        print("private-password")
        raise RuntimeError("private-password")
    monkeypatch.setattr(check, "inspect_draft", AsyncMock(side_effect=inspect_one))
    report, code = asyncio.run(check.run_inspect(source, ["PROC_LOG"]))
    assert code == check.FAILED
    assert [r["form"] for r in report["forms"]] == ["PROC_LOG"]
    credential_reader.assert_called_once_with(check.OPERATOR_USER_ID)
    check.write_inspect_report(report, tmp_path)
    assert source.exists()
    assert (tmp_path / "inspect.json").exists() and (tmp_path / "inspect.md").exists()
    assert "private-" not in capsys.readouterr().out + json.dumps(report) + (tmp_path / "inspect.md").read_text()
    source.write_text(json.dumps({"forms": [{"form": "LAT", "draft_url": "https://evil.invalid/events/fillin/a"}]}))
    credential_reader.reset_mock()
    with pytest.raises(check.GuardRefusal):
        asyncio.run(check.run_inspect(source))
    credential_reader.assert_not_called()


def test_inspect_cli_is_distinct_from_save_mode():
    args = check.parser().parse_args(["--inspect-drafts", "results.json", "--forms", "LAT", "PROC_LOG"])
    assert args.inspect_drafts == "results.json" and args.forms == ["LAT", "PROC_LOG"]
    assert check.parser().parse_args(["--inspect-drafts", "results.json"]).forms is None
    with pytest.raises(SystemExit):
        check.parser().parse_args(["--inspect-drafts", "results.json", "--all-mapped"])


@pytest.mark.parametrize("model_available", [True, False])
def test_inspect_snapshot_js_preserves_values_and_reads_collapsed_nodes(model_available):
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable for offline JavaScript snapshot execution")
    # Minimal frozen DOM/model: unexpected writes throw, all clicks throw.
    fixture = r"""
'use strict';
const element = (properties, attributes = {}) => Object.freeze({
    id: attributes.id || '', labels: [], parentElement: null,
    getAttribute: key => attributes[key] ?? null,
    hasAttribute: key => key in attributes,
    closest: () => null, querySelector: () => null, querySelectorAll: () => [],
    click: () => {throw new Error('Must not click');}, ...properties
});
const box = element({checked: true}, {id: 'kc-checkbox', 'data-node-id': 'kc-selected'});
const name = element({textContent: 'SLO6 KC2'});
let actualRow;
const actualBox = Object.freeze({...box, closest: () => actualRow});
const actualName = Object.freeze({...name, closest: () => actualRow});
actualRow = element({querySelectorAll: selector => selector.includes('checkbox') ? [actualBox] : [actualName]}, {id: 'kc-row'});
const tree = element({querySelectorAll: () => [actualRow]}, {id: 'tree-higher', 'aria-label': 'Higher curriculum'});
const otherTree = element({}, {id: 'tree-accs', 'aria-label': 'ACCS curriculum'});
const option1 = element({textContent: 'Paediatric sedation', value: 'sedation-id', selected: false});
const option2 = element({textContent: 'Other', value: 'other-id', selected: true});
const skill = element({tagName: 'SELECT', options: [option1, option2], selectedOptions: [option2]}, {id: 'skill'});
const nodes = Object.freeze([Object.freeze({name: 'Higher SLO6:', _id: 'slo', categories: Object.freeze([
    Object.freeze({name: 'SLO6 Key Capability: first offered', _id: 'kc-unchecked'}),
    Object.freeze({name: 'SLO6 KC2', _id: 'kc-selected'})])})]);
global.document = Object.freeze({querySelectorAll: selector => selector === 'label[for]' ? [] : [tree, otherTree], getElementById: id => id === 'skill' ? skill : null});
global.window = Object.freeze({angular: MODEL ? Object.freeze({element: t => ({isolateScope: () => ({nodes, selected: Object.freeze(t === tree ? ['kc-selected'] : [])})})}) : null});
const before = JSON.stringify([nodes, skill.options, actualBox.checked]);
const result = SNAPSHOT('skill');
if (before !== JSON.stringify([nodes, skill.options, actualBox.checked])) throw new Error('Changed form data');
console.log(JSON.stringify(result));
"""
    fixture = fixture.replace('MODEL', json.dumps(model_available)).replace('SNAPSHOT', '(' + check.INSPECT_DRAFT_JS + ')')
    result = subprocess.run([node, '-e', fixture], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert len(snapshot['trees']) == 2
    tree = snapshot['trees'][0]
    assert tree['label'] == 'Higher curriculum'
    assert tree['dom_nodes'][0]['checked'] is True
    assert tree['dom_nodes'][0]['control_attributes']['data-node-id'] == 'kc-selected'
    if model_available:
        assert tree['source'] == 'angular-model'
        assert tree['nodes'][1]['label'] == 'SLO6 Key Capability: first offered'
        assert tree['nodes'][1]['selected'] is False
        assert tree['nodes'][2]['selected'] is True
        assert tree['nodes'][2]['parent_labels'] == ['Higher SLO6:']
        assert snapshot['trees'][1]['nodes'][2]['selected'] is False
    else:
        assert tree['source'] == 'dom-only'
        assert 'collapsed-children-may-be-absent' in tree['coverage']
    skill = snapshot['higher_procedural_skill']
    assert [o['label'] for o in skill['options']] == ['Paediatric sedation', 'Other']
    assert skill['selected_label'] == 'Other'


@pytest.mark.asyncio
@pytest.mark.parametrize("model_available", [True, False])
async def test_inspect_run_and_reports_show_expected_beside_selected(monkeypatch, inspect_browser, tmp_path, model_available):
    import credentials
    page, _, _, _ = inspect_browser
    monkeypatch.setenv("KAIZEN_LIVE_CHECK_APPROVED", "operator-own-account")
    monkeypatch.delenv("PG_ENV", raising=False)
    monkeypatch.delenv("PG_KAIZEN_OFFLINE", raising=False)
    monkeypatch.setattr(credentials, "get_credentials", lambda uid: ("fake-login", "fake-password") if uid == check.OPERATOR_USER_ID else pytest.fail("Wrong user"))
    source = tmp_path / 'results.json'
    original = json.dumps({'forms': [{'form': 'PROC_LOG', 'draft_url': page.url}]})
    source.write_text(original)
    page.evaluate.return_value = {
        'trees': [{'label': 'Higher curriculum', 'attributes': {'id': 'tree'},
            'source': 'angular-model' if model_available else 'dom-only',
            'coverage': 'available-model' if model_available else 'current-dom-only',
            'nodes': [{'label': 'SLO6 KC1', 'model_id': 'kc-1', 'selected': False}],
            'dom_nodes': []}],
        'higher_procedural_skill': {'dom_id': 'skill', 'missing': False,
            'options': [{'label': 'Paediatric sedation'}, {'label': 'Other'}], 'selected_label': 'Other'}}
    report, code = await check.run_inspect(source)
    assert code == (0 if model_available else check.PARTIAL)
    assert report['forms'][0]['status'] == ('inspected' if model_available else 'partial')
    check.write_inspect_report(report, tmp_path)
    assert source.read_text() == original
    text = (tmp_path / 'inspect.md').read_text()
    for expected in ('SLO6 KC1', 'Higher', 'Paediatric sedation', 'Other', 'kc-1', 'selected'):
        assert expected in text
    assert json.loads((tmp_path / 'inspect.json').read_text()) == report
    assert 'fake-login' not in text and 'fake-password' not in text
    page.fill.assert_not_called()
    page.click.assert_not_called()


def test_inspect_entry_refuses_without_foreground_approval(monkeypatch, tmp_path):
    monkeypatch.delenv('KAIZEN_LIVE_CHECK_APPROVED', raising=False)
    with pytest.raises(check.GuardRefusal, match='approval'):
        asyncio.run(check.run_inspect(tmp_path / 'nonexistent.json'))


def test_inspect_wrapper_routes_only_to_inspection(monkeypatch, tmp_path):
    import importlib.util
    import sys
    import dotenv
    from pathlib import Path
    path = Path(check.__file__).parents[1] / 'scripts' / 'kaizen_live_check.py'
    spec = importlib.util.spec_from_file_location('operator_inspection_wrapper_test', path)
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    monkeypatch.setenv('KAIZEN_LIVE_CHECK_APPROVED', 'operator-own-account')
    monkeypatch.delenv('PG_ENV', raising=False)
    monkeypatch.delenv('PG_KAIZEN_OFFLINE', raising=False)
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    monkeypatch.setattr(dotenv, 'load_dotenv', lambda *_: None)
    source = tmp_path / 'results.json'
    monkeypatch.setattr(sys, 'argv', ['check', '--inspect-drafts', str(source), '--forms', 'LAT'])
    reader = AsyncMock(return_value=({'status': 'inspected', 'forms': []}, 0))
    monkeypatch.setattr(wrapper, 'run_inspect', reader)
    monkeypatch.setattr(wrapper, 'run_check', AsyncMock(side_effect=AssertionError('Must never save')))
    assert wrapper.main() == 0
    reader.assert_awaited_once_with(source, ['LAT'])
    assert (tmp_path / 'inspect.json').exists() and (tmp_path / 'inspect.md').exists()


@pytest.mark.asyncio
async def test_inspect_never_reads_catalogue_from_unconfirmed_synthetic_form(inspect_browser):
    page, _, _, _ = inspect_browser
    async def wait(script, **kwargs):
        if 'args.marker' in script:
            raise TimeoutError('Description lacks synthetic marker')
    page.wait_for_function.side_effect = wait
    with pytest.raises(check.GuardRefusal, match='synthetic PG CHECK'):
        await check.inspect_draft('LAT', page.url, 'fake-login')
    page.evaluate.assert_not_awaited()
    page.fill.assert_not_called()
    page.click.assert_not_called()
