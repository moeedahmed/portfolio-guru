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


def test_gemini_three_five_flash_model_default(monkeypatch):
    monkeypatch.delenv("GEMINI_3_5_FLASH_MODEL", raising=False)

    from model_config import gemini_three_five_flash_model

    assert gemini_three_five_flash_model() == "gemini-3.5-flash"


def test_gemini_three_five_flash_model_honours_env(monkeypatch):
    monkeypatch.setenv("GEMINI_3_5_FLASH_MODEL", "gemini-3-5-flash-runtime")

    from model_config import gemini_three_five_flash_model

    assert gemini_three_five_flash_model() == "gemini-3-5-flash-runtime"


def test_extractor_gemini_3_5_flash_override_reads_model_at_runtime(monkeypatch):
    # `gemini-3.5-flash` selects a Gemini Flash provider while leaving the
    # production default untouched, so the eval can compare models without
    # swapping the default extractor.
    monkeypatch.setenv("PORTFOLIO_GURU_EXTRACTOR_PROVIDER", "gemini-3.5-flash")
    monkeypatch.setenv("GEMINI_3_5_FLASH_MODEL", "gemini-3-5-flash-runtime")

    from extractor import _select_providers

    provider = _select_providers()[0]
    assert provider["name"] == "gemini-3-5-flash"
    assert provider["type"] == "gemini"
    assert provider["env_key"] == "GOOGLE_API_KEY"
    assert provider["model"]() == "gemini-3-5-flash-runtime"


def test_extractor_default_unaffected_by_3_5_flash_being_available(monkeypatch):
    # Adding the 3.5 Flash option must not change the default route.
    monkeypatch.delenv("PORTFOLIO_GURU_EXTRACTOR_PROVIDER", raising=False)
    monkeypatch.delenv("EXTRACTOR_PROVIDER", raising=False)

    from extractor import _select_providers

    providers = _select_providers()
    assert [p["name"] for p in providers] == ["deepseek-v4"]
