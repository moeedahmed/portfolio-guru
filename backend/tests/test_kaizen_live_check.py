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


def test_no_alternate_account_or_form_cli():
    assert "user_id" not in inspect.signature(check.run_check).parameters
    with pytest.raises(TypeError):
        check.run_check(user_id=123)
    with pytest.raises(SystemExit):
        check.parser().parse_args(["--user-id", "123"])
    with pytest.raises(SystemExit):
        check.parser().parse_args(["--forms", "LAT"])
    import bot
    assert check.OPERATOR_USER_ID == bot.ADMIN_USER_ID


def test_builder_populates_every_mapped_field_and_alias():
    import kaizen_form_filer as filer
    for form in check.DEFAULT_FORMS:
        fields = check.synthetic_fields(form)
        assert all(fields[key] for key in {**filer.COMMON_HEADER_FIELD_MAP, **filer.FORM_FIELD_MAP[form]})
        assert fields["event_description"] == check.MARKER
        assert fields["key_capabilities"] == ["SLO6 KC1"]
    assert check.synthetic_fields("DOPS_2021")["procedure_name"] == check.synthetic_fields("DOPS_2021")["procedural_skill"]


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
