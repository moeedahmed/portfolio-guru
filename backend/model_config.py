"""Central model configuration for Portfolio Guru.

Defaults keep the bot on a free-friendly Flash setup while allowing runtime
overrides from environment variables.
"""
from __future__ import annotations

import os


DEFAULT_GEMINI_FAST_MODEL = "gemini-3-flash-preview"
DEFAULT_GEMINI_STABLE_MODEL = "gemini-2.5-flash"
DEFAULT_GEMINI_PREMIUM_MODEL = "gemini-3.1-pro-preview"
DEFAULT_OPENAI_FALLBACK_MODEL = "gpt-4o-mini"
DEFAULT_BROWSER_FALLBACK_MODEL = "gpt-4o"


def gemini_fast_model() -> str:
    return os.environ.get("GEMINI_FAST_MODEL", DEFAULT_GEMINI_FAST_MODEL)


def gemini_stable_model() -> str:
    return os.environ.get("GEMINI_STABLE_MODEL", DEFAULT_GEMINI_STABLE_MODEL)


def gemini_premium_model() -> str:
    return os.environ.get("GEMINI_PREMIUM_MODEL", DEFAULT_GEMINI_PREMIUM_MODEL)


def gemini_fallback_models(include_premium: bool = False) -> list[str]:
    models = [gemini_fast_model()]
    if include_premium and os.environ.get("GOOGLE_API_KEY_PREMIUM"):
        models.append(gemini_premium_model())
    models.append(gemini_stable_model())
    return list(dict.fromkeys(m for m in models if m))


def openai_fallback_model() -> str:
    return os.environ.get("OPENAI_FALLBACK_MODEL", DEFAULT_OPENAI_FALLBACK_MODEL)


def browser_fallback_model() -> str:
    return os.environ.get("BROWSER_FALLBACK_MODEL", DEFAULT_BROWSER_FALLBACK_MODEL)
