"""Flat-file health profile store.

Stores per-user Portfolio Health pathway preferences outside the credentials
database so the health engine can remain deterministic and simple.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from health_models import HealthProfile


def _store_path() -> Path:
    return Path(
        os.environ.get(
            "PORTFOLIO_GURU_HEALTH_PROFILE_PATH",
            os.path.expanduser("~/.openclaw/data/portfolio-guru/health_profiles.json"),
        )
    )


def _load_all() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _dump_profile(profile: HealthProfile) -> dict[str, Any]:
    if hasattr(profile, "model_dump"):
        return profile.model_dump(mode="json")
    return json.loads(profile.json())


def get_health_profile(user_id: int) -> HealthProfile | None:
    """Return the stored health profile for a user, or None when unset."""
    data = _load_all().get(str(user_id))
    if not data:
        return None
    try:
        return HealthProfile(**data)
    except Exception:
        return None


def save_health_profile(profile: HealthProfile) -> None:
    """Persist a user's health profile."""
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    profiles = _load_all()
    profile.updated_at = datetime.now(UTC)
    profiles[str(profile.user_id)] = _dump_profile(profile)

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(profiles, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)
