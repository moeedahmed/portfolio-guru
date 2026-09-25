"""Eager Vertex credential preflight: fail closed before any traffic.

Run once, in its own short-lived process, by run_local.sh — after the venv
is selected and before the webhook server or bot start:

    "$PYTHON" -m vertex_preflight

It fetches the Vertex service-account credential (vertex_credentials.py)
and performs one refresh against the real Google token endpoint (no model
call, no public send), so a broken credential aborts the launcher instead
of surfacing after health checks pass and Telegram polling/webhook traffic
has already started. This preflight's credential object does not cross the
process boundary: the bot and webhook processes each call
vertex_credentials.get_credentials() again on first use, in their own
process. Exits non-zero on any failure. No-op (exit 0) when PG_USE_VERTEX
is not enabled — this module never falls back to Application Default
Credentials.
"""
from __future__ import annotations

import os
import sys

_TRUTHY = {"1", "true", "yes", "on"}


def _use_vertex() -> bool:
    return os.environ.get("PG_USE_VERTEX", "").strip().lower() in _TRUTHY


def main() -> int:
    if not _use_vertex():
        return 0

    project = os.environ.get("GCP_PROJECT_ID", "")
    if not project:
        print(
            "Vertex preflight: PG_USE_VERTEX is enabled but GCP_PROJECT_ID is not set",
            file=sys.stderr,
        )
        return 1

    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        print(
            "Vertex preflight: GOOGLE_APPLICATION_CREDENTIALS is set; Vertex mode uses an "
            "explicit credential only and refuses ambient Application Default Credentials",
            file=sys.stderr,
        )
        return 1

    import vertex_credentials

    try:
        credentials = vertex_credentials.get_credentials(project)
    except vertex_credentials.VertexCredentialError as exc:
        print(f"Vertex preflight: credential fetch failed: {exc}", file=sys.stderr)
        return 1

    from google.auth.transport.requests import Request

    try:
        credentials.refresh(Request())
    except Exception:
        print(
            "Vertex preflight: credential refresh against the Google token endpoint failed",
            file=sys.stderr,
        )
        return 1

    print(f"Vertex preflight: credential fetched and refreshed for project={project}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
