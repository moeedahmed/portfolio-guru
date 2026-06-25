"""Shared Gemini client factory — developer API today, Vertex AI (EU) on a flag.

This is the single switch for the "UK/EU-hosted only" data-routing decision.

- Default (flag off): the existing developer-API client (`GOOGLE_API_KEY`).
  No behaviour change.
- `PG_USE_VERTEX=1` + `GCP_PROJECT_ID` set: a Vertex-mode client pinned to an EU
  region, so ALL clinical extraction (text, voice, vision, documents) is
  processed in the EU under Google Cloud's Data Processing Addendum, with no
  other code change. Auth is Application Default Credentials — `run_local.sh`
  materialises `GCP_VERTEX_SA_JSON` to a temp file and sets
  `GOOGLE_APPLICATION_CREDENTIALS` at boot.

The same `client.models.generate_content(...)` call works for both backends, so
callers only swap their client construction for `make_client()`.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def use_vertex() -> bool:
    """True when EU/Vertex routing is enabled AND a project is configured.

    Requires a project id so a stray flag without credentials can never silently
    break extraction — it falls back to the developer API instead.
    """
    return (
        os.environ.get("PG_USE_VERTEX", "").strip().lower() in _TRUTHY
        and bool(os.environ.get("GCP_PROJECT_ID"))
    )


def vertex_location() -> str:
    return os.environ.get("GCP_VERTEX_LOCATION", "europe-west2")


def vertex_model(default: str = "gemini-2.5-flash") -> str:
    """Model id to use in Vertex mode.

    Overridable via GEMINI_VERTEX_MODEL. Defaults to a model broadly available
    on Vertex in EU regions; preview developer-API model names are not assumed
    to exist on Vertex — confirm availability per region before changing.
    """
    return os.environ.get("GEMINI_VERTEX_MODEL", default)


def make_client():
    """Construct a google-genai client honouring the EU-routing flag."""
    from google import genai

    if use_vertex():
        project = os.environ.get("GCP_PROJECT_ID")
        location = vertex_location()
        logger.info("Gemini client: Vertex AI (EU) project=%s location=%s", project, location)
        return genai.Client(vertexai=True, project=project, location=location)
    return genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
