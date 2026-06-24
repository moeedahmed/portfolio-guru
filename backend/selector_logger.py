"""
Selector logger — captures DOM selectors from browser-use sessions.
Foundation for the learning loop: browser-use → log selectors → auto-generate Playwright mappings.

Usage:
    logger = SelectorLogger("kaizen", "CBD")
    logger.log_step(step_num, action_type, selector, label, value, success)
    logger.save()  # writes to ~/.openclaw/data/portfolio-guru/selector-logs/

    # Later: analyse patterns
    history = get_selector_history("kaizen", "CBD")
    candidate = analyse_selectors("kaizen", "CBD")
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from selector_strategy import build_selector_plan, rank_selector_candidates

logger = logging.getLogger(__name__)

SELECTOR_LOG_DIR = Path.home() / ".openclaw/data/portfolio-guru/selector-logs"


class SelectorLogger:
    """Captures DOM selectors during a browser-use filing session."""

    def __init__(self, platform: str, form_type: str):
        self.platform = platform
        self.form_type = form_type
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.steps: List[Dict[str, Any]] = []

    def log_step(
        self,
        step_num: int,
        action_type: str,  # "click", "type", "select", "check", "navigate"
        selector: str = "",
        label: str = "",
        value: str = "",
        success: bool = True,
        raw_action: str = "",
        selector_meta: Optional[Dict[str, Any]] = None,
    ):
        """Log a single browser-use step."""
        self.steps.append({
            "step": step_num,
            "action": action_type,
            "selector": selector,
            "selector_meta": selector_meta or {},
            "label": label,
            "value": value,
            "success": success,
            "raw": raw_action,
            "timestamp": datetime.now().isoformat(),
        })

    def save(self) -> str:
        """Save the log to disk. Returns path."""
        log_dir = SELECTOR_LOG_DIR / self.platform / self.form_type
        log_dir.mkdir(parents=True, exist_ok=True)

        log_path = log_dir / f"{self.session_id}.json"
        data = {
            "platform": self.platform,
            "form_type": self.form_type,
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
            "steps": self.steps,
            "total_steps": len(self.steps),
            "success_count": sum(1 for s in self.steps if s["success"]),
        }

        with open(log_path, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Selector log saved: {log_path}")
        return str(log_path)


def get_selector_history(platform: str, form_type: str) -> List[Dict]:
    """Get all logged selector sessions for a platform+form combination."""
    log_dir = SELECTOR_LOG_DIR / platform / form_type
    if not log_dir.exists():
        return []

    sessions = []
    for f in sorted(log_dir.glob("*.json"), reverse=True):
        try:
            with open(f) as fh:
                sessions.append(json.load(fh))
        except Exception:
            continue

    return sessions


def analyse_selector_plans(platform: str, form_type: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """
    If enough consistent selector data exists (3+ successful filings),
    return candidate deterministic selector plans.

    Returns: {field_label: selector_plan} or None if not enough data.
    """
    history = get_selector_history(platform, form_type)
    successful = [h for h in history if h.get("success_count", 0) > 0]

    if len(successful) < 3:
        return None

    # Count selector frequency per label. Candidate choice is semantic-first:
    # label/role/text/placeholder/name/id/data selectors beat CSS/XPath even
    # when an XPath happened to be logged more often during exploration.
    label_selectors: Dict[str, Dict[str, int]] = {}
    label_metadata: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for session in successful:
        for step in session.get("steps", []):
            if not step.get("success") or not step.get("selector") or not step.get("label"):
                continue
            label = step["label"]
            sel = step["selector"]
            if label not in label_selectors:
                label_selectors[label] = {}
                label_metadata[label] = {}
            label_selectors[label][sel] = label_selectors[label].get(sel, 0) + 1
            label_metadata[label][sel] = step.get("selector_meta") or {}

    # Keep selectors seen in 2+ sessions, then rank by strategy. The full plan
    # retains fallback candidates for verification and later manual repair.
    candidate: Dict[str, Dict[str, Any]] = {}
    for label, selectors in label_selectors.items():
        stable = [
            {
                "value": selector,
                "strategy": label_metadata[label].get(selector, {}).get("strategy"),
                "kind": label_metadata[label].get(selector, {}).get("kind") or "playwright",
                "expected_unique": label_metadata[label].get(selector, {}).get("expected_unique", True),
                "intent": label,
                "source": f"selector-log:{selectors[selector]}",
            }
            for selector in selectors
            if selectors[selector] >= 2
        ]
        if stable:
            ranked = rank_selector_candidates(stable)
            candidate[label] = build_selector_plan(
                field_key=label,
                label="",
                selectors=[item["value"] for item in ranked],
                expected_unique=True,
                source=f"selector-log:{platform}/{form_type}",
            )

    if len(candidate) < 3:
        return None  # Not enough consistent fields

    logger.info(
        f"Candidate mapping for {platform}/{form_type}: "
        f"{len(candidate)} fields from {len(successful)} sessions"
    )
    return candidate


def analyse_selectors(platform: str, form_type: str) -> Optional[Dict[str, str]]:
    """
    Backwards-compatible selector summary.

    Returns the preferred selector value from the semantic-first plan for each
    label. Use analyse_selector_plans when fallback/verification metadata is
    needed.
    """
    plans = analyse_selector_plans(platform, form_type)
    if plans is None:
        return None
    return {
        label: plan["preferred"]["value"]
        for label, plan in plans.items()
        if plan.get("preferred")
    }
