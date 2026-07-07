from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import types
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
ENSURE_CHROME = BACKEND_DIR / "ensure_chrome.sh"
AGENT_HELPERS = BACKEND_DIR / "engine/providers/kaizen/domain_skill/agent_helpers.py"


def _fake_chrome(tmp_path: Path) -> Path:
    chrome = tmp_path / "chrome"
    chrome.write_text("#!/bin/sh\nexit 0\n")
    chrome.chmod(chrome.stat().st_mode | stat.S_IXUSR)
    return chrome


def _run_ensure_chrome(tmp_path: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    run_env = os.environ.copy()
    run_env.update(
        {
            "KAIZEN_CDP_URL": "http://127.0.0.1:1",
            "KAIZEN_CHROME_APP": str(_fake_chrome(tmp_path)),
            "KAIZEN_CHROME_PROFILE": str(tmp_path / "profile"),
        }
    )
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", str(ENSURE_CHROME), "--dry-run", *args],
        env=run_env,
        text=True,
        capture_output=True,
        check=True,
    )


def test_ensure_chrome_defaults_to_headless(tmp_path: Path):
    result = _run_ensure_chrome(tmp_path)

    assert "--headless=new" in result.stdout
    assert "--remote-debugging-port=1" in result.stdout


def test_ensure_chrome_visible_flag_suppresses_headless(tmp_path: Path):
    result = _run_ensure_chrome(tmp_path, "--visible")

    assert "--headless=new" not in result.stdout
    assert "--remote-debugging-port=1" in result.stdout


def test_ensure_chrome_headless_flag_overrides_visible_env(tmp_path: Path):
    result = _run_ensure_chrome(tmp_path, "--headless", env={"KAIZEN_CHROME_VISIBLE": "1"})

    assert "--headless=new" in result.stdout


def _load_agent_helpers(monkeypatch):
    helpers = types.ModuleType("browser_harness.helpers")

    def placeholder(*_args, **_kwargs):
        raise AssertionError("unexpected browser_harness helper call")

    for name in (
        "capture_screenshot",
        "cdp",
        "click_at_xy",
        "fill_input",
        "goto_url",
        "http_get",
        "js",
        "list_tabs",
        "new_tab",
        "page_info",
        "scroll",
        "switch_tab",
        "type_text",
        "wait",
        "wait_for_element",
        "wait_for_load",
        "wait_for_network_idle",
    ):
        setattr(helpers, name, placeholder)

    package = types.ModuleType("browser_harness")
    monkeypatch.setitem(sys.modules, "browser_harness", package)
    monkeypatch.setitem(sys.modules, "browser_harness.helpers", helpers)

    spec = importlib.util.spec_from_file_location("kaizen_agent_helpers_under_test", AGENT_HELPERS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kaizen_init_reuses_existing_kaizen_tab(monkeypatch):
    module = _load_agent_helpers(monkeypatch)
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        module,
        "list_tabs",
        lambda: [
            {"targetId": "blank", "url": "about:blank"},
            {"targetId": "kaizen", "url": "https://kaizenep.com/dashboard"},
        ],
    )
    monkeypatch.setattr(module, "new_tab", lambda url: calls.append(("new_tab", url)))
    monkeypatch.setattr(module, "switch_tab", lambda target_id: calls.append(("switch_tab", target_id)))

    assert module.kaizen_init() == "kaizen"
    assert ("new_tab", "about:blank") not in calls
    assert ("switch_tab", "kaizen") in calls


def test_kaizen_init_opens_kaizen_not_blank_when_no_tab_exists(monkeypatch):
    module = _load_agent_helpers(monkeypatch)
    calls: list[tuple[str, str]] = []
    tab_lists = iter(
        [
            [],
            [{"targetId": "new", "url": "https://kaizenep.com"}],
        ]
    )

    monkeypatch.setattr(module, "list_tabs", lambda: next(tab_lists))
    monkeypatch.setattr(module, "new_tab", lambda url: calls.append(("new_tab", url)))
    monkeypatch.setattr(module, "switch_tab", lambda target_id: calls.append(("switch_tab", target_id)))

    assert module.kaizen_init() == "new"
    assert ("new_tab", "https://kaizenep.com") in calls
    assert ("new_tab", "about:blank") not in calls


def test_kaizen_init_does_not_reuse_unrelated_profile_tabs(monkeypatch):
    module = _load_agent_helpers(monkeypatch)
    calls: list[tuple[str, str]] = []
    tab_lists = iter(
        [
            [{"targetId": "other", "url": "https://example.com"}],
            [{"targetId": "new", "url": "https://kaizenep.com"}],
        ]
    )

    monkeypatch.setattr(module, "list_tabs", lambda: next(tab_lists))
    monkeypatch.setattr(module, "new_tab", lambda url: calls.append(("new_tab", url)))
    monkeypatch.setattr(module, "switch_tab", lambda target_id: calls.append(("switch_tab", target_id)))

    assert module.kaizen_init() == "new"
    assert ("new_tab", "https://kaizenep.com") in calls
    assert ("switch_tab", "other") not in calls


def test_kaizen_close_extra_tabs_closes_blank_and_extra_kaizen(monkeypatch):
    module = _load_agent_helpers(monkeypatch)
    closed: list[str] = []

    module._DEFAULT_TAB = "keep"
    monkeypatch.setattr(
        module,
        "list_tabs",
        lambda: [
            {"targetId": "keep", "url": "https://kaizenep.com/dashboard"},
            {"targetId": "blank", "url": "about:blank"},
            {"targetId": "newtab", "url": "chrome://new-tab-page/"},
            {"targetId": "extra", "url": "https://kaizenep.com/events/list/All"},
            {"targetId": "other", "url": "https://example.com"},
        ],
    )
    monkeypatch.setattr(module, "cdp", lambda _method, targetId: closed.append(targetId))
    monkeypatch.setattr(module, "switch_tab", lambda _target_id: None)

    module.kaizen_close_extra_tabs()

    assert closed == ["blank", "newtab", "extra"]
