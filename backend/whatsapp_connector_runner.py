"""Runnable shell for the Portfolio Guru direct WhatsApp linked-device connector.

This is the repo-owned *transport half* of the direct linked-device path. It
carries **no** Portfolio Guru product logic: it only turns raw linked-device
message envelopes into the channel-neutral inbound contract via
:mod:`whatsapp_linked_device` and relays the neutral payload to the repo-owned
``POST /api/portfolio/inbound`` bridge. Extraction, form recommendation,
drafting, Kaizen access, and every product decision stay behind that bridge.

Two modes:

* **dry-run** (default) — read one or more recorded raw envelopes and print the
  channel-neutral routing verdict for each (scope / disposition / media kinds).
  It contacts **nothing**: no WhatsApp, no Portfolio Guru bridge, no secret, no
  device link. Its output deliberately excludes clinical text and captions, so a
  recorded batch can be replayed and logged without spilling patient content.

* **relay** — read raw linked-device events (NDJSON or a JSON array) and forward
  the neutral payload for each *handled* (DIRECT, non-empty) turn to the inbound
  bridge, authenticated with the shared gateway secret. GROUP and empty turns are
  refused locally as a gateway responsibility and never forwarded, so private
  content in a shared thread never leaves the connector. Relay is gated on the
  read-only readiness guard returning ``launch-ready`` and refuses by default.

The QR / device-link step is **not** performed here. Emitting the WhatsApp QR and
maintaining the linked-device session is the job of a thin Baileys /
WhatsApp-Web sidecar that streams raw message events (one JSON object per line)
into this runner's ``relay`` mode. That live sidecar is the documented next
dependency step (see ``docs/hermes/WHATSAPP_ROLLOUT_PLAN.md``); this runner is
the offline-testable seam it feeds. Nothing here links a device, authenticates a
session, reads a secret from disk, or starts a persistent live connector.

Configuration is by environment variable *name* only — values are never
hardcoded and never logged:

* ``PORTFOLIO_INBOUND_URL``    — full URL of the ``POST /api/portfolio/inbound`` bridge.
* ``PORTFOLIO_INBOUND_SECRET`` — shared gateway secret sent as ``X-Gateway-Secret``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import whatsapp_linked_device as wld
from channel_contract import InboundDisposition

# A poster forwards one already-normalised neutral payload to the inbound bridge.
# It is injected so the relay core stays free of any network dependency and is
# fully unit-testable offline; the live implementation is make_bridge_poster.
Poster = Callable[[Mapping[str, Any]], None]

_INBOUND_URL_ENV = "PORTFOLIO_INBOUND_URL"
_INBOUND_SECRET_ENV = "PORTFOLIO_INBOUND_SECRET"


@dataclass(frozen=True)
class RelayStats:
    """Counts for one relay pass — routing metadata only, never content.

    ``forwarded`` is the number of DIRECT non-empty turns posted to the bridge;
    ``refused_group``, ``refused_empty`` and ``refused_invalid`` are turns dropped
    locally without any forward. ``refused_invalid`` counts internal/non-user
    frames (no routable ``remoteJid``) that a live Baileys session can stream. The
    sum equals ``total``.
    """

    total: int = 0
    forwarded: int = 0
    refused_group: int = 0
    refused_empty: int = 0
    refused_invalid: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "forwarded": self.forwarded,
            "refused_group": self.refused_group,
            "refused_empty": self.refused_empty,
            "refused_invalid": self.refused_invalid,
        }


def _iter_events(source_text: str) -> list[Mapping[str, Any]]:
    """Parse recorded events from a JSON array, a single JSON object, or NDJSON.

    A raw linked-device batch may be recorded as a JSON array of envelopes, a
    single envelope object, or newline-delimited JSON (one envelope per line, the
    shape the live sidecar streams). Blank lines are ignored. Nothing here
    inspects content — it only structurally decodes the transport frames.
    """
    stripped = source_text.strip()
    if not stripped:
        return []
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        events: list[Mapping[str, Any]] = []
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
        return events
    if isinstance(decoded, list):
        return list(decoded)
    return [decoded]


def run_dry_run(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Offline routing preview for a batch — never contacts a service.

    Delegates each envelope to :func:`whatsapp_linked_device.dry_run`, which
    returns routing metadata only (channel, scope, disposition, media kinds) and
    deliberately excludes the clinical text and captions.
    """
    return [wld.dry_run(raw) for raw in events]


def relay_events(events: Iterable[Mapping[str, Any]], poster: Poster) -> RelayStats:
    """Normalise each raw event and forward only the handled turns via ``poster``.

    Routing is delegated wholly to the neutral contract:
    :func:`whatsapp_linked_device.normalize_and_route` decides the disposition and
    :func:`whatsapp_linked_device.to_inbound_payload` builds the exact bridge
    body. DIRECT non-empty turns are forwarded; GROUP, empty and internal/non-user
    frames (no routable ``remoteJid``) are refused locally and never posted, so
    private content in a shared thread never leaves the connector and a Baileys
    protocol frame can never crash the relay. No product logic runs here.
    """
    total = forwarded = refused_group = refused_empty = refused_invalid = 0
    for raw in events:
        total += 1
        decision = wld.normalize_and_route(raw).decision
        if decision.disposition is InboundDisposition.HANDLE:
            poster(wld.to_inbound_payload(raw))
            forwarded += 1
        elif decision.disposition is InboundDisposition.REFUSE_GROUP:
            refused_group += 1
        elif decision.disposition is InboundDisposition.REFUSE_INVALID:
            refused_invalid += 1
        else:
            refused_empty += 1
    return RelayStats(
        total=total,
        forwarded=forwarded,
        refused_group=refused_group,
        refused_empty=refused_empty,
        refused_invalid=refused_invalid,
    )


def make_bridge_poster(url: str, secret: str, *, timeout: float = 10.0) -> Poster:
    """Build the live poster that authenticates to the inbound bridge.

    The shared gateway secret is sent as the ``X-Gateway-Secret`` header — the
    same private contract the bridge enforces in :mod:`webhook_server`. httpx is
    imported lazily so dry-run never needs it.
    """

    def _post(payload: Mapping[str, Any]) -> None:
        import httpx

        resp = httpx.post(
            url,
            json=dict(payload),
            headers={"X-Gateway-Secret": secret},
            timeout=timeout,
        )
        resp.raise_for_status()

    return _post


def _readiness_status(
    repo_root: Path | None = None, env: Mapping[str, str] | None = None
) -> str:
    """Return the read-only rollout readiness status (``blocked`` by default).

    Loads the repo-owned ``scripts/pg_whatsapp_readiness.py`` guard and evaluates
    it. The guard reads only repo files and non-secret identifiers; it never
    reads BWS, credential material, or runtime state. Relay refuses unless this
    returns ``launch-ready``.
    """
    import importlib.util

    root = repo_root or Path(__file__).resolve().parents[1]
    script = root / "scripts" / "pg_whatsapp_readiness.py"
    spec = importlib.util.spec_from_file_location("pg_whatsapp_readiness", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load readiness guard at {script}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec: the guard's frozen dataclasses resolve annotations
    # via sys.modules[cls.__module__] at class-definition time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    result = module.evaluate(root, env=dict(env) if env is not None else None)
    return str(result["status"])


def _load_source_text(payload: str | None) -> str:
    if payload:
        return Path(payload).read_text(encoding="utf-8")
    return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    """CLI entry: dry-run a recorded batch, or relay events to the inbound bridge.

    Dry-run is always safe and offline. Relay is gated: it refuses unless the
    read-only readiness guard returns ``launch-ready`` and both
    ``PORTFOLIO_INBOUND_URL`` and ``PORTFOLIO_INBOUND_SECRET`` are set. It never
    links a device or emits a QR — the live sidecar streams events into it.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Runnable shell for the Portfolio Guru direct WhatsApp linked-device "
            "connector. Normalises raw linked-device events via the repo-owned "
            "neutral contract and (in relay mode) forwards handled turns to the "
            "POST /api/portfolio/inbound bridge. Contains no product logic and, "
            "in dry-run mode, contacts no live service."
        )
    )
    parser.add_argument(
        "--relay",
        action="store_true",
        help=(
            "Forward handled turns to the inbound bridge. Requires the readiness "
            "guard to return launch-ready and the inbound env vars to be set. "
            "Without this flag the runner performs an offline dry-run only."
        ),
    )
    parser.add_argument(
        "--payload",
        type=str,
        default=None,
        help=(
            "Path to recorded events (JSON array, single JSON object, or NDJSON). "
            "Defaults to stdin, which is how the live sidecar streams events."
        ),
    )
    args = parser.parse_args(argv)

    if not args.relay:
        events = _iter_events(_load_source_text(args.payload))
        print(json.dumps(run_dry_run(events), indent=2, sort_keys=True))
        return 0

    status = _readiness_status()
    if status != "launch-ready":
        print(
            json.dumps(
                {
                    "error": "readiness-blocked",
                    "status": status,
                    "detail": (
                        "relay refused: scripts/pg_whatsapp_readiness.py is not "
                        "launch-ready"
                    ),
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 3

    url = os.environ.get(_INBOUND_URL_ENV, "").strip()
    secret = os.environ.get(_INBOUND_SECRET_ENV, "").strip()
    if not url or not secret:
        print(
            json.dumps(
                {
                    "error": "inbound-not-configured",
                    "detail": (
                        f"relay requires {_INBOUND_URL_ENV} and "
                        f"{_INBOUND_SECRET_ENV} to be set"
                    ),
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 4

    events = _iter_events(_load_source_text(args.payload))
    stats = relay_events(events, make_bridge_poster(url, secret))
    print(json.dumps(stats.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
