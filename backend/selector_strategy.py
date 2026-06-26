"""Selector planning helpers for deterministic portfolio form mappings.

The existing Kaizen maps use DOM ids because those are the only selectors that
were verified for the first deterministic filer. New mappings should keep that
fallback, but prefer user-facing locators when the page exposes them.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional


SEMANTIC_STRATEGY_ORDER = (
    "label",
    "role",
    "text",
    "placeholder",
    "name",
    "id",
    "data",
    "css",
    "xpath",
)

_RANK = {strategy: idx for idx, strategy in enumerate(SEMANTIC_STRATEGY_ORDER)}


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _css_attr(selector: str, attr: str) -> Optional[str]:
    match = re.search(rf"\[{re.escape(attr)}=(['\"])(.*?)\1\]", selector)
    return match.group(2) if match else None


def infer_selector_strategy(selector: str, metadata: Optional[Mapping[str, Any]] = None) -> str:
    """Classify a selector into the strategy buckets used for ranking."""
    selector = _clean(selector)
    lower = selector.lower()
    metadata = metadata or {}

    explicit = metadata.get("strategy") or metadata.get("kind")
    if explicit in _RANK:
        return str(explicit)

    if lower.startswith("xpath=") or lower.startswith("//") or lower.startswith("(//"):
        return "xpath"
    if "get_by_label" in lower or lower.startswith("label="):
        return "label"
    if "get_by_role" in lower or lower.startswith("role="):
        return "role"
    if "get_by_text" in lower or lower.startswith("text=") or ":has-text(" in lower:
        return "text"
    if "get_by_placeholder" in lower or "placeholder=" in lower:
        return "placeholder"
    if _css_attr(selector, "name") or lower.startswith("name="):
        return "name"
    if _css_attr(selector, "id") or lower.startswith("#") or lower.startswith("id="):
        return "id"
    if re.search(r"\[data-[\w-]+=", lower):
        return "data"
    return "css"


def selector_rank(selector: str, metadata: Optional[Mapping[str, Any]] = None) -> int:
    """Lower is better. Semantic, user-facing selectors beat CSS/XPath."""
    return _RANK[infer_selector_strategy(selector, metadata)]


def selector_candidate(
    value: str,
    *,
    strategy: Optional[str] = None,
    kind: str = "playwright",
    expected_unique: bool = True,
    intent: str = "",
    source: str = "",
) -> Dict[str, Any]:
    """Create a serialisable selector candidate with verification metadata."""
    value = _clean(value)
    inferred = strategy or infer_selector_strategy(value)
    return {
        "strategy": inferred,
        "kind": kind,
        "value": value,
        "expected_unique": expected_unique,
        "intent": _clean(intent),
        "source": _clean(source),
    }


def rank_selector_candidates(candidates: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Return candidates ordered from best semantic selector to fallback."""
    normalised = []
    seen = set()
    for raw in candidates:
        value = _clean(raw.get("value") or raw.get("selector"))
        if not value or value in seen:
            continue
        seen.add(value)
        normalised.append(
            selector_candidate(
                value,
                strategy=raw.get("strategy"),
                kind=str(raw.get("kind") or "playwright"),
                expected_unique=bool(raw.get("expected_unique", True)),
                intent=str(raw.get("intent") or ""),
                source=str(raw.get("source") or ""),
            )
        )
    return sorted(normalised, key=lambda item: (_RANK[item["strategy"]], item["value"]))


def build_selector_plan(
    *,
    field_key: str,
    label: str = "",
    selectors: Iterable[str] = (),
    dom_id: str = "",
    name: str = "",
    placeholder: str = "",
    data_attributes: Optional[Mapping[str, str]] = None,
    expected_unique: bool = True,
    source: str = "",
) -> Dict[str, Any]:
    """Build a semantic-first selector plan for a discovered form field.

    The first candidate is the preferred deterministic locator. ID/XPath-style
    entries remain in the candidate list so older DOM-id filling and later
    verification still have a stable fallback.
    """
    intent = _clean(label) or _clean(field_key)
    candidates: List[Dict[str, Any]] = []

    if label:
        candidates.append(selector_candidate(label, strategy="label", kind="label", expected_unique=expected_unique, intent=intent, source=source))
    if placeholder:
        candidates.append(selector_candidate(placeholder, strategy="placeholder", kind="placeholder", expected_unique=expected_unique, intent=intent, source=source))
    if name:
        candidates.append(selector_candidate(f'[name="{name}"]', strategy="name", kind="css", expected_unique=expected_unique, intent=intent, source=source))
    for attr, attr_value in (data_attributes or {}).items():
        if attr_value:
            candidates.append(selector_candidate(f'[{attr}="{attr_value}"]', strategy="data", kind="css", expected_unique=expected_unique, intent=intent, source=source))
    if dom_id:
        candidates.append(selector_candidate(f'[id="{dom_id}"]', strategy="id", kind="css", expected_unique=expected_unique, intent=intent, source=source))
    for selector in selectors:
        candidates.append(selector_candidate(selector, expected_unique=expected_unique, intent=intent, source=source))

    ranked = rank_selector_candidates(candidates)
    fallback = next((item for item in ranked if item["strategy"] in {"id", "xpath", "css"}), None)
    repair_hint = {
        "on_drift": "Prefer repairing or adding label/role/placeholder/name/data candidates before changing DOM id or XPath fallbacks.",
        "snapshot": "Capture page URL, field label, candidate counts, screenshot, and HTML around the field before updating the map.",
        "verify": "A repaired candidate should match exactly one control and preserve the existing DOM id fallback when available.",
    }
    return {
        "field_key": _clean(field_key),
        "intent": intent,
        "expected_unique": expected_unique,
        "preferred": ranked[0] if ranked else None,
        "fallback": fallback,
        "candidates": ranked,
        "repair_hint": repair_hint,
    }


def preferred_selector_value(plan_or_selector: Any) -> str:
    """Return the best selector value while accepting legacy string maps."""
    if isinstance(plan_or_selector, str):
        return plan_or_selector
    if isinstance(plan_or_selector, Mapping):
        preferred = plan_or_selector.get("preferred")
        if isinstance(preferred, Mapping) and preferred.get("value"):
            return str(preferred["value"])
        candidates = plan_or_selector.get("candidates")
        if isinstance(candidates, list) and candidates:
            value = candidates[0].get("value")
            if value:
                return str(value)
    return ""


def fallback_dom_id(plan_or_selector: Any) -> str:
    """Extract a DOM id from a legacy value or a selector plan fallback."""
    if isinstance(plan_or_selector, str):
        return plan_or_selector
    if not isinstance(plan_or_selector, Mapping):
        return ""
    for item in plan_or_selector.get("candidates", []):
        if not isinstance(item, Mapping):
            continue
        value = str(item.get("value") or "")
        attr_id = _css_attr(value, "id")
        if attr_id:
            return attr_id
        if item.get("strategy") == "id" and value.startswith("#"):
            return value[1:]
    return ""


__all__ = [
    "SEMANTIC_STRATEGY_ORDER",
    "build_selector_plan",
    "fallback_dom_id",
    "infer_selector_strategy",
    "preferred_selector_value",
    "rank_selector_candidates",
    "selector_candidate",
    "selector_rank",
]
