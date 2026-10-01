"""Explicit review transitions and drift reconciliation; local JSON storage."""
import json
import re
from dataclasses import replace
from pathlib import Path

from .models import Field, FormMap, Option


def _update(form, key, change):
    if not any(f.key == key for f in form.fields):
        raise KeyError(key)
    return replace(form, fields=tuple(change(f) if f.key == key else f for f in form.fields))


def suggest(form, key, concept):
    if not isinstance(concept, str) or not concept.strip():
        raise ValueError("A nonempty canonical concept is required")
    def change(f):
        if f.state == "verified" or f.drift == "missing":
            raise ValueError("Cannot change a verified or missing mapping")
        return replace(f, concept=concept, state="candidate")
    return _update(form, key, change)


def verify(form, key):
    """Caller explicitly confirms the field's current meaning and structure."""
    def change(f):
        if f.state == "unmapped" or f.drift == "missing":
            raise ValueError("Only present candidate fields can be verified")
        return replace(f, state="verified", drift="")
    return _update(form, key, change)


def reread(old, fresh):
    if (old.platform, old.form_id) != (fresh.platform, fresh.form_id):
        raise ValueError("Cannot reconcile different forms")
    previous = {f.key: f for f in old.fields}
    fields = []
    for field in fresh.fields:
        before = previous.pop(field.key, None)
        if before is None:
            fields.append(field)
        elif before.structure() == field.structure() and before.drift != "missing":
            fields.append(before)
        else:
            fields.append(replace(field, state="candidate" if before.state == "verified" else before.state,
                                  concept=before.concept, drift="changed"))
    fields.extend(replace(f, state="candidate" if f.concept else "unmapped", drift="missing") for f in previous.values())
    return replace(fresh, fields=tuple(fields))


def save_map(form, root="form_maps"):
    for part in (form.platform, form.form_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", part):
            raise ValueError("Platform and form identifiers must be safe path components")
    root = Path(root).resolve()
    path = root / form.platform / f"{form.form_id}.json"
    if not path.resolve().is_relative_to(root):
        raise ValueError("Map path escapes storage root")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(form.to_json(), encoding="utf-8")
    return path


def load_map(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    source_hash = data.pop("source_hash")
    data["fields"] = tuple(Field(**{**f, "options": tuple(Option(**o) for o in f["options"]),
                                    "restrictions": tuple(f["restrictions"])}) for f in data["fields"])
    form = FormMap(**data)
    if source_hash != form.source_hash:
        raise ValueError("Stored source structure hash does not match")
    return form
