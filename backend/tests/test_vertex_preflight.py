"""Eager Vertex credential preflight (backend/vertex_preflight.py).

Offline only: vertex_credentials.get_credentials and the SDK refresh call
are always monkeypatched. No real bws/BWS/network/runtime launch here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import vertex_credentials  # noqa: E402
import vertex_preflight  # noqa: E402


class _FakeCredentials:
    def __init__(self, fail_refresh=False):
        self.fail_refresh = fail_refresh
        self.refreshed = False

    def refresh(self, request):
        if self.fail_refresh:
            raise RuntimeError("refresh boom")
        self.refreshed = True


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PG_USE_VERTEX", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    yield


def test_noop_when_vertex_disabled(monkeypatch):
    assert vertex_preflight.main() == 0


def test_fails_when_project_missing(monkeypatch, capsys):
    monkeypatch.setenv("PG_USE_VERTEX", "1")
    assert vertex_preflight.main() == 1
    assert "GCP_PROJECT_ID" in capsys.readouterr().err


def test_fails_when_project_missing_before_fetching_credentials(monkeypatch):
    monkeypatch.setenv("PG_USE_VERTEX", "1")

    def boom(project):
        raise AssertionError("must not fetch a credential before the project check")

    monkeypatch.setattr(vertex_credentials, "get_credentials", boom)
    assert vertex_preflight.main() == 1


def test_rejects_ambient_adc(monkeypatch, capsys):
    monkeypatch.setenv("PG_USE_VERTEX", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "portfolio-guru-eu")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/whatever.json")
    assert vertex_preflight.main() == 1
    assert "Application Default Credentials" in capsys.readouterr().err


def test_fetch_failure_fails_closed(monkeypatch, capsys):
    monkeypatch.setenv("PG_USE_VERTEX", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "portfolio-guru-eu")

    def boom(project):
        raise vertex_credentials.VertexCredentialError("fetch failed")

    monkeypatch.setattr(vertex_credentials, "get_credentials", boom)
    assert vertex_preflight.main() == 1
    assert "fetch failed" in capsys.readouterr().err


def test_refresh_failure_fails_closed(monkeypatch, capsys):
    monkeypatch.setenv("PG_USE_VERTEX", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "portfolio-guru-eu")
    fake = _FakeCredentials(fail_refresh=True)
    monkeypatch.setattr(vertex_credentials, "get_credentials", lambda project: fake)
    assert vertex_preflight.main() == 1
    assert "refresh" in capsys.readouterr().err
    assert fake.refreshed is False


def test_success_fetches_and_refreshes_once(monkeypatch):
    monkeypatch.setenv("PG_USE_VERTEX", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "portfolio-guru-eu")
    fake = _FakeCredentials()
    seen_projects = []

    def fake_get_credentials(project):
        seen_projects.append(project)
        return fake

    monkeypatch.setattr(vertex_credentials, "get_credentials", fake_get_credentials)
    assert vertex_preflight.main() == 0
    assert seen_projects == ["portfolio-guru-eu"]
    assert fake.refreshed is True
