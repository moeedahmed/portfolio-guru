"""Shared Gemini client factory — developer API today, Vertex AI (EU) on a flag.

This is the single switch for the "UK/EU-hosted only" data-routing decision.

- Default (flag off): the existing developer-API client (`GOOGLE_API_KEY`).
  No behaviour change.
- `PG_USE_VERTEX=1` + `GCP_PROJECT_ID` set: a Vertex-mode client pinned to an EU
  region, so ALL clinical extraction (text, voice, vision, documents) is
  processed in the EU under Google Cloud's Data Processing Addendum, with no
  other code change. Auth is an explicit service-account credential fetched
  from BWS into process memory by `vertex_credentials.get_credentials()` —
  never written to disk, never placed in the process environment — and passed
  straight to `google.genai.Client(credentials=...)`. There is deliberately no
  Application Default Credentials fallback: if the in-memory fetch fails,
  `make_client()` raises rather than silently routing clinical data off-region
  or to an ambient identity.
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


def vertex_model(default: str = "gemini-3.5-flash") -> str:
    """Model id to use in Vertex mode.

    Overridable via GEMINI_VERTEX_MODEL (empty value falls back to the default).
    gemini-3.5-flash and gemini-2.5-flash are both verified available on Vertex
    in europe-west2; confirm any other model per region before switching.
    """
    return os.environ.get("GEMINI_VERTEX_MODEL") or default


def make_client():
    """Construct a google-genai client honouring the EU-routing flag.

    In Vertex mode this fetches the service-account credential from BWS into
    process memory (see vertex_credentials.py) and passes it explicitly —
    there is no ADC fallback, so a fetch failure raises rather than routing
    clinical data off-region.
    """
    from google import genai

    if use_vertex():
        from vertex_credentials import get_credentials

        project = os.environ.get("GCP_PROJECT_ID")
        location = vertex_location()
        credentials = get_credentials(project)
        logger.info("Gemini client: Vertex AI (EU) project=%s location=%s", project, location)
        return genai.Client(vertexai=True, project=project, location=location, credentials=credentials)
    return genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
