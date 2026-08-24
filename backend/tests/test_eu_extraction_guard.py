"""Clinical text must never leave the UK/EEA because a flag went missing.

Before this guard, `PG_USE_VERTEX` being unset silently re-enabled the DeepSeek
provider — a Chinese endpoint with no UK adequacy decision and no DPA. The guard
now fails closed: an outage is recoverable, an Art. 9 transfer is not.
"""
from __future__ import annotations

import pytest

import extractor


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("PG_ALLOW_NON_EU_EXTRACTION", raising=False)


def _force_vertex(monkeypatch, enabled: bool):
    import gemini_client

    monkeypatch.setattr(gemini_client, "use_vertex", lambda: enabled)


def test_vertex_mode_selects_only_eu_providers(monkeypatch):
    _force_vertex(monkeypatch, True)

    providers = extractor._select_providers()

    assert providers, "vertex mode must still return a usable provider"
    assert all(p["type"] == "gemini" for p in providers)
    assert not any("deepseek" in p["name"] for p in providers)


def test_without_vertex_the_guard_refuses_rather_than_falling_back(monkeypatch):
    _force_vertex(monkeypatch, False)

    with pytest.raises(RuntimeError) as excinfo:
        extractor._select_providers()

    message = str(excinfo.value)
    assert "PG_USE_VERTEX" in message, "the error must say how to fix it"
    assert "deepseek" in message.lower(), "the error must name the offending provider"


def test_explicit_opt_out_is_honoured_for_local_development(monkeypatch):
    _force_vertex(monkeypatch, False)
    monkeypatch.setenv("PG_ALLOW_NON_EU_EXTRACTION", "1")

    assert extractor._select_providers() == extractor.PROVIDERS


def test_production_startup_does_not_carry_a_deepseek_key():
    """run_local.sh must not export DEEPSEEK_API_KEY — the guard is the second
    line of defence, not the first."""
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "run_local.sh").read_text()
    exports = [
        line.strip()
        for line in script.splitlines()
        if line.strip().startswith("export DEEPSEEK_API_KEY")
        or line.strip().startswith("DEEPSEEK_API_KEY=")
    ]
    assert exports == [], f"run_local.sh still loads a DeepSeek key: {exports}"
