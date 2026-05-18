import os


def test_gemini_fallback_models_default_order(monkeypatch):
    monkeypatch.delenv("GEMINI_FAST_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_STABLE_MODEL", raising=False)

    from model_config import gemini_fallback_models

    assert gemini_fallback_models() == ["gemini-3-flash-preview", "gemini-2.5-flash"]


def test_gemini_fallback_models_honours_env(monkeypatch):
    monkeypatch.setenv("GEMINI_FAST_MODEL", "gemini-custom-fast")
    monkeypatch.setenv("GEMINI_STABLE_MODEL", "gemini-custom-stable")

    from model_config import gemini_fallback_models

    assert gemini_fallback_models() == ["gemini-custom-fast", "gemini-custom-stable"]


def test_extractor_defaults_to_deepseek(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_GURU_EXTRACTOR_PROVIDER", raising=False)

    from extractor import _select_providers

    providers = _select_providers()
    assert [p["name"] for p in providers] == ["deepseek-v4"]


def test_extractor_gemini_pro_override_reads_model_at_runtime(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_GURU_EXTRACTOR_PROVIDER", "gemini-pro")
    monkeypatch.setenv("GEMINI_PREMIUM_MODEL", "gemini-runtime-pro")

    from extractor import _select_providers

    provider = _select_providers()[0]
    assert provider["name"] == "gemini-pro"
    assert provider["model"]() == "gemini-runtime-pro"
