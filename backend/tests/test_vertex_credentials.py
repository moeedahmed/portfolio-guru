"""Vertex AI credential lifecycle: BWS -> process memory only.

Replaces the earlier persistent-file contract (validated, operator-supplied
service-account key at GCP_VERTEX_CREDENTIALS_PATH). Covers:

  - the earlier temp-file / persistent-file lifecycle is gone (static checks
    on run_local.sh)
  - backend/vertex_credentials.py: token-file read, fetch, validation,
    caching, single-flight, drift rejection, identity pinning, and
    exception-boundary redaction — all against a fully mocked `bws`
    subprocess and SDK, never the real CLI or network
  - backend/gemini_client.py: make_client() passes an explicit credential
    object through to genai.Client() in Vertex mode

No real secret, token, or network access anywhere in this file — only
synthetic fixtures, a mocked token file, and monkeypatched subprocess/SDK
calls.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
RUN_LOCAL_SH = BACKEND_DIR / "run_local.sh"

sys.path.insert(0, str(BACKEND_DIR))

import vertex_credentials  # noqa: E402

VALID_SA_JSON = json.dumps(
    {
        "type": "service_account",
        "project_id": "portfolio-guru-eu",
        "private_key": "-----BEGIN PRIVATE KEY-----\nfakekeydata\n-----END PRIVATE KEY-----\n",
        "client_email": "svc@portfolio-guru-eu.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
)
SECRET_ID = "11111111-2222-3333-4444-555555555555"
EXPECTED_CLIENT_EMAIL = "svc@portfolio-guru-eu.iam.gserviceaccount.com"
FAKE_TOKEN = "fake-token-not-real"


def _envelope(secret_id=SECRET_ID, key="GCP_VERTEX_SA_JSON", value=VALID_SA_JSON):
    return json.dumps({"id": secret_id, "key": key, "value": value}).encode()


class _FakeCompleted:
    def __init__(self, stdout=b"", returncode=0):
        self.stdout = stdout
        self.stderr = b""
        self.returncode = returncode


@pytest.fixture()
def token_file(tmp_path):
    path = tmp_path / ".bws-token"
    path.write_text(FAKE_TOKEN)
    os.chmod(path, 0o600)
    return path


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, token_file):
    vertex_credentials._reset_cache_for_tests()
    monkeypatch.delenv("BWS_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("PG_VERTEX_SA_SECRET_ID", SECRET_ID)
    monkeypatch.setenv("PG_VERTEX_SA_CLIENT_EMAIL", EXPECTED_CLIENT_EMAIL)
    monkeypatch.setenv("PG_VERTEX_BWS_TOKEN_PATH", str(token_file))
    monkeypatch.setattr(vertex_credentials, "_bws_binary", lambda: sys.executable)
    yield
    vertex_credentials._reset_cache_for_tests()


def _patch_run(monkeypatch, result=None, side_effect=None):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        if side_effect is not None:
            raise side_effect
        return result

    monkeypatch.setattr(vertex_credentials.subprocess, "run", fake_run)
    return calls


def _patch_sdk(monkeypatch, fail=False):
    # Patches the already-imported real module's class attribute in place,
    # rather than swapping sys.modules — Python caches "from X import Y"
    # lookups on the parent module object, so a sys.modules-only swap is
    # silently ignored once google.oauth2.service_account has been imported
    # anywhere else in the test session.
    from google.oauth2 import service_account

    captured = {}

    def fake_from_info(info, scopes=None):
        if fail:
            raise ValueError("boom")
        captured["info"] = info
        captured["scopes"] = scopes
        return object()

    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", staticmethod(fake_from_info))
    return captured


# --- Configuration / fail-closed preconditions -----------------------------


class TestPreconditions:
    def test_missing_secret_id_fails_closed(self, monkeypatch):
        monkeypatch.delenv("PG_VERTEX_SA_SECRET_ID", raising=False)
        with pytest.raises(vertex_credentials.VertexCredentialError, match="PG_VERTEX_SA_SECRET_ID"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_non_uuid_secret_id_fails_closed(self, monkeypatch):
        monkeypatch.setenv("PG_VERTEX_SA_SECRET_ID", "not-a-uuid")
        with pytest.raises(vertex_credentials.VertexCredentialError):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_missing_expected_client_email_fails_closed(self, monkeypatch):
        monkeypatch.delenv("PG_VERTEX_SA_CLIENT_EMAIL", raising=False)
        with pytest.raises(vertex_credentials.VertexCredentialError, match="PG_VERTEX_SA_CLIENT_EMAIL"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_invalid_expected_client_email_fails_closed(self, monkeypatch):
        monkeypatch.setenv("PG_VERTEX_SA_CLIENT_EMAIL", "not-an-email")
        with pytest.raises(vertex_credentials.VertexCredentialError, match="PG_VERTEX_SA_CLIENT_EMAIL"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_missing_project_fails_closed(self):
        with pytest.raises(vertex_credentials.VertexCredentialError):
            vertex_credentials.get_credentials("")

    def test_missing_token_path_fails_closed(self, monkeypatch):
        monkeypatch.delenv("PG_VERTEX_BWS_TOKEN_PATH", raising=False)
        with pytest.raises(vertex_credentials.VertexCredentialError, match="PG_VERTEX_BWS_TOKEN_PATH"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_relative_token_path_fails_closed(self, monkeypatch):
        monkeypatch.setenv("PG_VERTEX_BWS_TOKEN_PATH", "relative/token")
        with pytest.raises(vertex_credentials.VertexCredentialError, match="PG_VERTEX_BWS_TOKEN_PATH"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_missing_token_file_fails_closed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PG_VERTEX_BWS_TOKEN_PATH", str(tmp_path / "does-not-exist"))
        with pytest.raises(vertex_credentials.VertexCredentialError, match="not accessible"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_symlink_token_path_fails_closed(self, monkeypatch, tmp_path, token_file):
        link = tmp_path / "link-token"
        link.symlink_to(token_file)
        monkeypatch.setenv("PG_VERTEX_BWS_TOKEN_PATH", str(link))
        with pytest.raises(vertex_credentials.VertexCredentialError, match="symlink"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_group_readable_token_path_fails_closed(self, monkeypatch, token_file):
        os.chmod(token_file, 0o640)
        monkeypatch.setenv("PG_VERTEX_BWS_TOKEN_PATH", str(token_file))
        with pytest.raises(vertex_credentials.VertexCredentialError, match="permissions"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_empty_token_file_fails_closed(self, monkeypatch, tmp_path):
        empty = tmp_path / "empty-token"
        empty.write_text("")
        os.chmod(empty, 0o600)
        monkeypatch.setenv("PG_VERTEX_BWS_TOKEN_PATH", str(empty))
        with pytest.raises(vertex_credentials.VertexCredentialError, match="empty"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_oversized_token_file_fails_closed(self, monkeypatch, tmp_path):
        huge = tmp_path / "huge-token"
        huge.write_text("x" * (vertex_credentials._MAX_TOKEN_BYTES + 1))
        os.chmod(huge, 0o600)
        monkeypatch.setenv("PG_VERTEX_BWS_TOKEN_PATH", str(huge))
        with pytest.raises(vertex_credentials.VertexCredentialError, match="oversized"):
            vertex_credentials.get_credentials("portfolio-guru-eu")


# --- Successful fetch --------------------------------------------------


class TestSuccessfulFetch:
    def test_success_builds_credentials(self, monkeypatch):
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope()))
        captured = _patch_sdk(monkeypatch)
        creds = vertex_credentials.get_credentials("portfolio-guru-eu")
        assert creds is not None
        assert captured["info"]["project_id"] == "portfolio-guru-eu"
        assert list(captured["scopes"]) == ["https://www.googleapis.com/auth/cloud-platform"]

    def test_no_credential_values_in_argv(self, monkeypatch):
        calls = _patch_run(monkeypatch, result=_FakeCompleted(_envelope()))
        _patch_sdk(monkeypatch)
        vertex_credentials.get_credentials("portfolio-guru-eu")
        cmd = calls[0]["cmd"]
        joined = " ".join(cmd)
        assert "fakekeydata" not in joined
        assert FAKE_TOKEN not in joined
        assert cmd == [sys.executable, "secret", "get", SECRET_ID, "--output", "json"]

    def test_child_env_is_minimal(self, monkeypatch):
        calls = _patch_run(monkeypatch, result=_FakeCompleted(_envelope()))
        _patch_sdk(monkeypatch)
        vertex_credentials.get_credentials("portfolio-guru-eu")
        env = calls[0]["kwargs"]["env"]
        assert set(env.keys()) == {"BWS_ACCESS_TOKEN", "HOME", "PATH"}
        assert env["BWS_ACCESS_TOKEN"] == FAKE_TOKEN
        assert env["PATH"] == vertex_credentials._CHILD_PATH

    def test_token_never_enters_process_environment(self, monkeypatch):
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope()))
        _patch_sdk(monkeypatch)
        vertex_credentials.get_credentials("portfolio-guru-eu")
        assert "BWS_ACCESS_TOKEN" not in os.environ

    def test_shell_is_never_used(self, monkeypatch):
        calls = _patch_run(monkeypatch, result=_FakeCompleted(_envelope()))
        _patch_sdk(monkeypatch)
        vertex_credentials.get_credentials("portfolio-guru-eu")
        assert calls[0]["kwargs"]["shell"] is False

    def test_second_call_same_project_uses_cache_no_refetch(self, monkeypatch):
        calls = _patch_run(monkeypatch, result=_FakeCompleted(_envelope()))
        _patch_sdk(monkeypatch)
        first = vertex_credentials.get_credentials("portfolio-guru-eu")
        second = vertex_credentials.get_credentials("portfolio-guru-eu")
        assert first is second
        assert len(calls) == 1


# --- Fail-closed validation of the BWS response -----------------------


class TestResponseValidation:
    def test_nonzero_exit_fails_closed(self, monkeypatch):
        _patch_run(monkeypatch, result=_FakeCompleted(b"", returncode=1))
        with pytest.raises(vertex_credentials.VertexCredentialError):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_timeout_fails_closed(self, monkeypatch):
        _patch_run(monkeypatch, side_effect=subprocess.TimeoutExpired(cmd="bws", timeout=15))
        with pytest.raises(vertex_credentials.VertexCredentialError, match="timed out"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_malformed_json_fails_closed(self, monkeypatch):
        _patch_run(monkeypatch, result=_FakeCompleted(b"{not json"))
        with pytest.raises(vertex_credentials.VertexCredentialError):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_oversized_output_fails_closed(self, monkeypatch):
        huge = json.dumps({"id": SECRET_ID, "key": "GCP_VERTEX_SA_JSON", "value": "x" * 100_000}).encode()
        _patch_run(monkeypatch, result=_FakeCompleted(huge))
        with pytest.raises(vertex_credentials.VertexCredentialError, match="oversized"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_secret_id_mismatch_fails_closed(self, monkeypatch):
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope(secret_id="99999999-0000-0000-0000-000000000000")))
        with pytest.raises(vertex_credentials.VertexCredentialError, match="mismatched"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_wrong_key_name_fails_closed(self, monkeypatch):
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope(key="SOME_OTHER_SECRET")))
        with pytest.raises(vertex_credentials.VertexCredentialError, match="key"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_project_mismatch_fails_closed(self, monkeypatch):
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope()))
        _patch_sdk(monkeypatch)
        with pytest.raises(vertex_credentials.VertexCredentialError, match="project"):
            vertex_credentials.get_credentials("some-other-project")

    def test_client_email_mismatch_fails_closed(self, monkeypatch):
        monkeypatch.setenv("PG_VERTEX_SA_CLIENT_EMAIL", "someone-else@portfolio-guru-eu.iam.gserviceaccount.com")
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope()))
        _patch_sdk(monkeypatch)
        with pytest.raises(vertex_credentials.VertexCredentialError, match="client_email"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_missing_required_field_fails_closed(self, monkeypatch):
        data = json.loads(VALID_SA_JSON)
        del data["private_key"]
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope(value=json.dumps(data))))
        with pytest.raises(vertex_credentials.VertexCredentialError):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_wrong_type_field_fails_closed(self, monkeypatch):
        data = json.loads(VALID_SA_JSON)
        data["type"] = "user_account"
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope(value=json.dumps(data))))
        with pytest.raises(vertex_credentials.VertexCredentialError):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_unsafe_token_uri_fails_closed(self, monkeypatch):
        data = json.loads(VALID_SA_JSON)
        data["token_uri"] = "https://attacker.example/token"
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope(value=json.dumps(data))))
        with pytest.raises(vertex_credentials.VertexCredentialError, match="token_uri"):
            vertex_credentials.get_credentials("portfolio-guru-eu")

    def test_sdk_construction_failure_fails_closed(self, monkeypatch):
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope()))
        _patch_sdk(monkeypatch, fail=True)
        with pytest.raises(vertex_credentials.VertexCredentialError):
            vertex_credentials.get_credentials("portfolio-guru-eu")


# --- Redaction and exception-boundary safety ----------------------------


class TestRedaction:
    def test_no_leakage_in_exception_messages(self, monkeypatch):
        data = json.loads(VALID_SA_JSON)
        del data["private_key"]
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope(value=json.dumps(data))))
        with pytest.raises(vertex_credentials.VertexCredentialError) as exc_info:
            vertex_credentials.get_credentials("portfolio-guru-eu")
        message = str(exc_info.value)
        assert "fakekeydata" not in message
        assert FAKE_TOKEN not in message
        assert "svc@portfolio-guru-eu" not in message

    def test_raised_exception_has_no_context_or_cause(self, monkeypatch):
        data = json.loads(VALID_SA_JSON)
        del data["private_key"]
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope(value=json.dumps(data))))
        with pytest.raises(vertex_credentials.VertexCredentialError) as exc_info:
            vertex_credentials.get_credentials("portfolio-guru-eu")
        exc = exc_info.value
        assert exc.__context__ is None
        assert exc.__cause__ is None

    def test_raised_exception_traceback_holds_no_secret_locals(self, monkeypatch):
        # Deliberately not modifying VALID_SA_JSON here: the fetch/parse
        # steps succeed and the fake SDK call fails last, after every
        # secret-bearing local (raw envelope, SA JSON string, parsed dict)
        # has already been built somewhere in the call chain.
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope()))
        _patch_sdk(monkeypatch, fail=True)
        with pytest.raises(vertex_credentials.VertexCredentialError) as exc_info:
            vertex_credentials.get_credentials("portfolio-guru-eu")

        tb = exc_info.value.__traceback__
        # client_email is intentionally NOT a secret marker: PG_VERTEX_SA_
        # CLIENT_EMAIL is a nonsecret identity pin the module is designed to
        # hold and compare against, unlike the private key/BWS token.
        secret_markers = ("fakekeydata", FAKE_TOKEN)
        module_frames = 0
        while tb is not None:
            frame = tb.tb_frame
            # Only frames inside vertex_credentials.py matter here — a
            # caller/test frame's own unrelated locals are not the concern.
            if frame.f_code.co_filename == vertex_credentials.__file__:
                module_frames += 1
                for value in frame.f_locals.values():
                    text = repr(value)
                    for marker in secret_markers:
                        assert marker not in text
            tb = tb.tb_next
        # The traceback that reaches the caller is only the fresh raise
        # inside get_credentials — it must not include frames from
        # _fetch_and_build/_fetch_secret_envelope/_parse_and_validate_sa_info/
        # _build_sdk_credentials, which is where the secret-bearing locals
        # live.
        assert module_frames == 1


# --- Concurrency: single-flight -----------------------------------------


class TestConcurrency:
    def test_concurrent_calls_trigger_one_fetch(self, monkeypatch):
        import time

        def slow_run(cmd, **kwargs):
            time.sleep(0.05)
            return _FakeCompleted(_envelope())

        monkeypatch.setattr(vertex_credentials.subprocess, "run", slow_run)
        _patch_sdk(monkeypatch)

        results = []
        errors = []

        def worker():
            try:
                results.append(vertex_credentials.get_credentials("portfolio-guru-eu"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 8
        assert len(set(id(r) for r in results)) == 1


# --- Drift rejection -----------------------------------------------------


class TestDriftRejection:
    def test_second_call_different_project_rejected(self, monkeypatch):
        _patch_run(monkeypatch, result=_FakeCompleted(_envelope()))
        _patch_sdk(monkeypatch)
        vertex_credentials.get_credentials("portfolio-guru-eu")
        with pytest.raises(vertex_credentials.VertexCredentialError, match="does not match"):
            vertex_credentials.get_credentials("a-different-project")


# --- Previous persistent-file lifecycle contract must be gone -------------


def test_run_local_no_longer_materialises_sa_json():
    text = RUN_LOCAL_SH.read_text()
    assert "GCP_VERTEX_SA_JSON" not in text
    assert "mktemp" not in text
    assert "GCP_VERTEX_CREDENTIALS_PATH" not in text
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in text
    assert "vertex_credentials_gate" not in text


def test_run_local_routes_project_location_model_flag_and_secret_id():
    text = RUN_LOCAL_SH.read_text()
    for token in (
        "GCP_PROJECT_ID",
        "GCP_VERTEX_LOCATION",
        "GEMINI_VERTEX_MODEL",
        "PG_USE_VERTEX",
        "PG_VERTEX_SA_SECRET_ID",
        "PG_VERTEX_SA_CLIENT_EMAIL",
        "PG_VERTEX_BWS_TOKEN_PATH",
    ):
        assert token in text


def test_run_local_does_not_export_bws_access_token():
    text = RUN_LOCAL_SH.read_text()
    assert "export BWS_ACCESS_TOKEN" not in text


def test_run_local_fails_closed_without_project_secret_id_email_or_token_path():
    text = RUN_LOCAL_SH.read_text()
    assert "PG_USE_VERTEX is enabled but GCP_PROJECT_ID is not set" in text
    assert "PG_VERTEX_SA_SECRET_ID is not set" in text
    assert "PG_VERTEX_SA_CLIENT_EMAIL is not set" in text
    assert "PG_VERTEX_BWS_TOKEN_PATH is not set" in text


def test_run_local_runs_credential_preflight_before_webhook_and_bot():
    text = RUN_LOCAL_SH.read_text()
    preflight_pos = text.index("vertex_preflight")
    webhook_pos = text.index("uvicorn webhook_server:app")
    bot_pos = text.index("exec $PYTHON bot.py")
    assert preflight_pos < webhook_pos < bot_pos


def test_run_local_syntax_ok():
    result = subprocess.run(["bash", "-n", str(RUN_LOCAL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_gate_script_removed():
    assert not (BACKEND_DIR / "vertex_credentials_gate.sh").exists()
