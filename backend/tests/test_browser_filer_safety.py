"""Safety guards on the off-by-default browser-use fallback.

Mirrors Anthropic's "Run the toolset safely" checklist: the agent reaches only
the platform's own hosts, cannot run page JavaScript or touch local files, and
gets a fresh browser holding one doctor's own session, never the shared CDP
Chrome. No browser is launched and no model is called here.
"""

import sys
import types
from pathlib import Path

import pytest

import browser_filer


KAIZEN_HOSTS = browser_filer.allowed_hosts("kaizen", "https://eportfolio.rcem.ac.uk")


@pytest.mark.parametrize("url", [
    "https://eportfolio.rcem.ac.uk",
    "https://kaizenep.com/events/new-section/abc",
    "https://auth.kaizenep.com/login",
])
def test_kaizen_hosts_allowed(url):
    assert browser_filer.url_is_allowed(url, KAIZEN_HOSTS)


@pytest.mark.parametrize("url", [
    "https://evilkaizen.com",           # the old "*kaizen*" glob let this through
    "https://kaizenep.com.evil.example",
    "https://rcem.evil.example",
    "https://notkaizenep.com",
    "http://kaizenep.com",              # https only
    "javascript:alert(1)",
    "file:///etc/passwd",
    "data:text/html,hi",
    "https://127.0.0.1:18800/json",
    "https://evil.example\\@kaizenep.com",
])
def test_other_hosts_and_schemes_refused(url):
    assert not browser_filer.url_is_allowed(url, KAIZEN_HOSTS)


def test_unknown_platform_gets_only_its_own_https_host(monkeypatch):
    monkeypatch.delenv("PG_BROWSER_USE_EXTRA_HOSTS", raising=False)
    assert browser_filer.allowed_hosts("horus", "https://horus.nhs.uk") == ["horus.nhs.uk"]
    assert browser_filer.allowed_hosts("horus", "http://horus.nhs.uk") == []


def test_extra_hosts_must_be_plain_names(monkeypatch):
    monkeypatch.setenv("PG_BROWSER_USE_EXTRA_HOSTS", "cdn.kaizenep.net, *, *evil*, bad host")
    hosts = browser_filer.allowed_hosts("kaizen", "https://eportfolio.rcem.ac.uk")
    assert "cdn.kaizenep.net" in hosts
    assert "*" not in hosts and "*evil*" not in hosts and "bad host" not in hosts


def test_host_resolver_rule_blocks_everything_else():
    rule = browser_filer.host_resolver_rule(KAIZEN_HOSTS)
    assert rule.startswith("MAP * ~NOTFOUND")
    for host in KAIZEN_HOSTS:
        assert f"EXCLUDE {host}" in rule


class _Fakes:
    def __init__(self):
        self.profile_kwargs = None
        self.agent_kwargs = None
        self.killed = False
        self.state_seen = None
        self.telemetry = None


def _install_fake_browser_use(monkeypatch, fakes):
    class BrowserProfile:
        def __init__(self, **kwargs):
            fakes.profile_kwargs = kwargs
            fakes.state_seen = Path(kwargs["storage_state"]).read_text()

    class BrowserSession:
        def __init__(self, browser_profile):
            self.browser_profile = browser_profile

        async def kill(self):
            fakes.killed = True

    class Tools:
        def __init__(self, exclude_actions=None):
            self.exclude_actions = exclude_actions

    class Agent:
        def __init__(self, **kwargs):
            import os
            fakes.agent_kwargs = kwargs
            fakes.telemetry = (
                os.environ.get("ANONYMIZED_TELEMETRY"),
                os.environ.get("BROWSER_USE_CLOUD_SYNC"),
            )

        async def run(self):
            return "Draft saved"

    root = types.ModuleType("browser_use")
    root.Agent = Agent
    root.Tools = Tools
    browser = types.ModuleType("browser_use.browser")
    browser.BrowserProfile = BrowserProfile
    browser.BrowserSession = BrowserSession
    monkeypatch.setitem(sys.modules, "browser_use", root)
    monkeypatch.setitem(sys.modules, "browser_use.browser", browser)
    monkeypatch.setattr(browser_filer, "require_online", lambda: None)
    for name in browser_filer._TELEMETRY_OFF:
        monkeypatch.setenv(name, "true")
    monkeypatch.setattr(browser_filer, "_create_llm", lambda model: object())


@pytest.mark.asyncio
async def test_runs_in_fresh_browser_with_this_doctors_session(monkeypatch):
    fakes = _Fakes()
    _install_fake_browser_use(monkeypatch, fakes)
    loaded = {}

    def fake_load(user_id, username):
        loaded["args"] = (user_id, username)
        return {"cookies": [{"name": "s", "value": "doctor-a"}], "origins": []}

    import kaizen_form_filer
    monkeypatch.setattr(kaizen_form_filer, "load_session_state", fake_load)

    result = await browser_filer.file_with_browser_use(
        platform_url="https://eportfolio.rcem.ac.uk",
        form_name="New form",
        fields={"reflection": "synthetic"},
        credentials={"username": "doctor-a@example.invalid", "password": "unused"},
        form_url="https://kaizenep.com/events/new-section/abc",
        form_type="NEW_FORM",
        platform="kaizen",
        telegram_user_id=111,
    )

    assert result["status"] == "success"
    assert loaded["args"] == (111, "doctor-a@example.invalid")
    profile = fakes.profile_kwargs
    assert "cdp_url" not in profile                      # never the shared Chrome
    assert profile["allowed_domains"] == KAIZEN_HOSTS
    assert profile["block_ip_addresses"] is True
    assert profile["accept_downloads"] is False
    assert Path(profile["downloads_path"]).parent == Path(profile["user_data_dir"]).parent
    assert any(a.startswith("--host-resolver-rules=MAP * ~NOTFOUND") for a in profile["args"])
    assert "doctor-a" in fakes.state_seen
    assert fakes.agent_kwargs["available_file_paths"] == []
    assert fakes.telemetry == ("false", "false")
    assert set(fakes.agent_kwargs["tools"].exclude_actions) >= {"evaluate", "upload_file"}
    # The throwaway profile and decrypted session are gone afterwards.
    assert fakes.killed
    assert not Path(profile["user_data_dir"]).parent.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id, saved", [(None, {"cookies": []}), (111, None)])
async def test_refuses_without_this_doctors_saved_session(monkeypatch, user_id, saved):
    fakes = _Fakes()
    _install_fake_browser_use(monkeypatch, fakes)
    import kaizen_form_filer
    monkeypatch.setattr(kaizen_form_filer, "load_session_state", lambda *a: saved)

    result = await browser_filer.file_with_browser_use(
        platform_url="https://eportfolio.rcem.ac.uk",
        form_name="New form",
        fields={"reflection": "synthetic"},
        credentials={"username": "doctor-a@example.invalid", "password": "unused"},
        platform="kaizen",
        telegram_user_id=user_id,
    )

    assert result["status"] == "failed"
    assert fakes.profile_kwargs is None and fakes.agent_kwargs is None


@pytest.mark.asyncio
async def test_refuses_form_url_off_the_allowlist(monkeypatch):
    fakes = _Fakes()
    _install_fake_browser_use(monkeypatch, fakes)

    result = await browser_filer.file_with_browser_use(
        platform_url="https://eportfolio.rcem.ac.uk",
        form_name="New form",
        fields={"reflection": "synthetic"},
        credentials={},
        form_url="https://evilkaizen.com/form",
        platform="kaizen",
        telegram_user_id=111,
    )

    assert result["status"] == "failed"
    assert fakes.agent_kwargs is None


@pytest.mark.asyncio
async def test_router_passes_the_doctor_to_browser_use(monkeypatch):
    import filer_router

    seen = {}

    async def fake_file_with_browser_use(**kwargs):
        seen.update(kwargs)
        return {"status": "failed", "filled": [], "skipped": []}

    monkeypatch.setattr(browser_filer, "file_with_browser_use", fake_file_with_browser_use)
    await filer_router._route_browser_use(
        platform="kaizen",
        platform_url="https://eportfolio.rcem.ac.uk",
        form_url=None,
        form_name="New form",
        form_type="NEW_FORM",
        fields={},
        credentials={},
        curriculum_links=None,
        telegram_user_id=222,
    )
    assert seen["telegram_user_id"] == 222


def test_stale_work_dirs_are_swept(tmp_path, monkeypatch):
    import os
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    stale = tmp_path / "pg-browser-use-old"
    fresh = tmp_path / "pg-browser-use-new"
    other = tmp_path / "unrelated-old"
    for d in (stale, fresh, other):
        d.mkdir()
        (d / "state.json").write_text("{}")
    old = 1_000_000
    os.utime(stale, (old, old))
    os.utime(other, (old, old))

    browser_filer._sweep_stale_work_dirs()

    assert not stale.exists()
    assert fresh.exists() and other.exists()
