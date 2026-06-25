"""EU-routing flag: gemini_client.make_client + extractor provider gating.

Guards the "UK/EU-hosted only" switch. With the flag off (default) nothing
changes; with it on AND a project configured, clients go to Vertex (EU) and the
extractor stops using DeepSeek (China).
"""
import google.genai as genai

import gemini_client


def _clear(monkeypatch):
    for k in ("PG_USE_VERTEX", "GCP_PROJECT_ID", "GCP_VERTEX_LOCATION", "GEMINI_VERTEX_MODEL"):
        monkeypatch.delenv(k, raising=False)


def test_use_vertex_off_by_default(monkeypatch):
    _clear(monkeypatch)
    assert gemini_client.use_vertex() is False


def test_use_vertex_requires_project_even_with_flag(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("PG_USE_VERTEX", "1")  # flag on but no project -> still off (safe)
    assert gemini_client.use_vertex() is False


def test_use_vertex_on_with_flag_and_project(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("PG_USE_VERTEX", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "emgurus-portfolio")
    assert gemini_client.use_vertex() is True


def test_make_client_developer_mode(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GOOGLE_API_KEY", "dev-key")
    captured = {}
    monkeypatch.setattr(genai, "Client", lambda **kw: captured.update(kw) or "client")
    gemini_client.make_client()
    assert captured == {"api_key": "dev-key"}


def test_make_client_vertex_mode(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("PG_USE_VERTEX", "true")
    monkeypatch.setenv("GCP_PROJECT_ID", "emgurus-portfolio")
    monkeypatch.setenv("GCP_VERTEX_LOCATION", "europe-west2")
    captured = {}
    monkeypatch.setattr(genai, "Client", lambda **kw: captured.update(kw) or "client")
    gemini_client.make_client()
    assert captured == {"vertexai": True, "project": "emgurus-portfolio", "location": "europe-west2"}


def test_vertex_location_default_and_override(monkeypatch):
    _clear(monkeypatch)
    assert gemini_client.vertex_location() == "europe-west2"
    monkeypatch.setenv("GCP_VERTEX_LOCATION", "europe-west1")
    assert gemini_client.vertex_location() == "europe-west1"


def test_extractor_drops_deepseek_in_vertex_mode(monkeypatch):
    _clear(monkeypatch)
    import extractor
    # Flag off: full provider list (DeepSeek first).
    providers_off = extractor._select_providers()
    assert any(p["type"] != "gemini" for p in providers_off)

    monkeypatch.setenv("PG_USE_VERTEX", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "emgurus-portfolio")
    providers_on = extractor._select_providers()
    assert providers_on and all(p["type"] == "gemini" for p in providers_on)
