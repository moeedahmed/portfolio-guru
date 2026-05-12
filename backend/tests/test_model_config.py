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


def test_extractor_provider_reads_fast_model_at_runtime(monkeypatch):
    monkeypatch.setenv("GEMINI_FAST_MODEL", "gemini-runtime-fast")

    from extractor import PROVIDERS

    gemini_provider = next(p for p in PROVIDERS if p["type"] == "gemini")
    assert gemini_provider["model"]() == "gemini-runtime-fast"
