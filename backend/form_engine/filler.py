"""Deterministic, non-generative draft plans and a pure local mock executor."""
import re
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .models import Action, FillPlan, FillStep, Skipped
from .reader import parse_form

ACTIONS = {"text": Action.FILL, "textarea": Action.FILL, "date": Action.FILL,
           "select": Action.SELECT, "radio": Action.CHOOSE, "checkbox": Action.CHECK, "file": Action.SET_FILE}


def selector_for(field, fields):
    for by in ("id", "name", "label"):
        value = field.selectors.get(by)
        if value and sum(f.selectors.get(by) == value for f in fields if f.drift != "missing") == 1:
            return {"by": by, "value": value}
    return None


def valid_value(field, value):
    if field.kind == "checkbox":
        return type(value) is bool
    if not isinstance(value, str):
        return False
    if field.kind in {"select", "radio"}:
        matches = [o for o in field.options if o.value == value]
        return len(matches) == 1 and not matches[0].disabled
    if field.kind == "date" and value:
        try:
            return date.fromisoformat(value).isoformat() == value
        except ValueError:
            return False
    return True


def blocked(field):
    return bool(field.restrictions) or bool(re.search(
        r"\b(submit|send|approve|sign|signature)\b", " ".join((field.key, field.label)).lower()))


def make_plan(form, values, *, allow_candidates=False):
    if not isinstance(values, dict) or any(not isinstance(k, str) for k in values):
        raise ValueError("Canonical values must be a dictionary with string keys")
    steps, skipped = [], []
    meanings = Counter(f.concept for f in form.fields if f.concept and f.drift != "missing")
    for f in form.fields:
        selector = selector_for(f, form.fields)
        reason = None
        if f.drift:
            reason = "form_drift_" + f.drift
        elif f.state == "unmapped":
            reason = "unmapped"
        elif f.state == "candidate" and not allow_candidates:
            reason = "candidate_not_allowed"
        elif f.concept not in values:
            reason = "missing_value"
        elif meanings[f.concept] > 1:
            reason = "ambiguous_concept"
        elif blocked(f):
            reason = "protected_or_unsupported_control"
        elif selector is None:
            reason = "ambiguous_selector"
        elif not valid_value(f, values[f.concept]):
            reason = "invalid_value"
        if reason:
            skipped.append(Skipped(f.key, f.concept, reason))
        else:
            steps.append(FillStep(f.key, selector, ACTIONS[f.kind], values[f.concept]))
    known = {f.concept for f in form.fields if f.concept}
    skipped.extend(Skipped("", key, "unknown_value") for key in sorted(values.keys() - known))
    return FillPlan(form.platform, form.form_id, form.source_hash, tuple(steps), tuple(skipped))


def local_path(source):
    text = str(source)
    url = urlsplit(text)
    if text.startswith("//") or (url.scheme and (url.scheme != "file" or url.netloc not in {"", "localhost"})):
        raise ValueError("Only local paths and local file:// URLs are supported; no network")
    if url.query or url.fragment:
        raise ValueError("Queries/fragments are not local file paths")
    return Path(unquote(url.path) if url.scheme == "file" else text)


def execute(plan, source):
    """Apply to an inert DOM. Files are filename metadata only, never opened/uploaded."""
    html = local_path(source).read_text(encoding="utf-8")
    form, controls = parse_form(html, plan.platform, plan.form_id)
    if form.source_hash != plan.source_hash:
        raise ValueError("Form source structure has changed; regenerate the map and plan")
    fields = {f.key: f for f in form.fields}
    seen = set()
    for step in plan.steps:
        f = fields.get(step.field_key)
        if (f is None or f.key in seen or blocked(f) or step.selector != selector_for(f, form.fields)
                or step.action != ACTIONS[f.kind] or not valid_value(f, step.value)):
            raise ValueError("Invalid or unsafe draft step")
        seen.add(f.key)
        group = controls[f.key]
        if f.kind == "textarea":
            group[0].children = [step.value]
        elif f.kind in {"select", "radio"}:
            choices = [n for n in group[0].walk() if n.tag == "option"] if f.kind == "select" else group
            attr = "selected" if f.kind == "select" else "checked"
            for n, option in zip(choices, f.options):
                n.attrs.pop(attr, None)
                if option.value == step.value:
                    n.attrs[attr] = ""
        elif f.kind == "checkbox":
            group[0].attrs.pop("checked", None)
            if step.value:
                group[0].attrs["checked"] = ""
        else:
            group[0].attrs["value"] = step.value
    results = {}
    for f in form.fields:
        group = controls[f.key]
        if f.kind == "textarea":
            results[f.key] = "".join(str(c) for c in group[0].children)
        elif f.kind == "checkbox":
            results[f.key] = "checked" in group[0].attrs
        elif f.kind in {"select", "radio"}:
            choices = [n for n in group[0].walk() if n.tag == "option"] if f.kind == "select" else group
            attr = "selected" if f.kind == "select" else "checked"
            results[f.key] = next((o.value for n, o in zip(choices, f.options) if attr in n.attrs),
                                  f.options[0].value if f.kind == "select" and f.options else "")
        else:
            results[f.key] = group[0].attrs.get("value", "")
    return results
