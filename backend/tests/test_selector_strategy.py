import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from selector_strategy import (
    build_selector_plan,
    fallback_dom_id,
    infer_selector_strategy,
    rank_selector_candidates,
)


def test_selector_ranking_prefers_semantic_before_xpath():
    candidates = rank_selector_candidates(
        [
            {"value": "//textarea[3]", "source": "browser-use"},
            {"value": '[id="60772a97-92eb-4dbe-a813-6a5293be82f9"]', "source": "dom"},
            {"value": "Clinical reasoning", "strategy": "label", "kind": "label"},
            {"value": '[name="clinicalReasoning"]', "source": "dom"},
        ]
    )

    assert [candidate["strategy"] for candidate in candidates] == [
        "label",
        "name",
        "id",
        "xpath",
    ]
    assert candidates[-1]["value"] == "//textarea[3]"


def test_selector_plan_keeps_id_fallback_and_intent_metadata():
    plan = build_selector_plan(
        field_key="reflection",
        label="Reflection",
        selectors=["//textarea[4]"],
        dom_id="610b5c60-99ac-4902-9407-22974d6a5799",
        expected_unique=True,
        source="unit-test",
    )

    assert plan["preferred"]["strategy"] == "label"
    assert plan["intent"] == "Reflection"
    assert plan["expected_unique"] is True
    assert fallback_dom_id(plan) == "610b5c60-99ac-4902-9407-22974d6a5799"
    assert any(candidate["strategy"] == "xpath" for candidate in plan["candidates"])
    assert "label/role/placeholder/name/data" in plan["repair_hint"]["on_drift"]
    assert "screenshot" in plan["repair_hint"]["snapshot"].lower()


def test_infer_selector_strategy_for_common_playwright_and_css_forms():
    assert infer_selector_strategy("page.get_by_label('Feedback')") == "label"
    assert infer_selector_strategy("page.get_by_role('textbox', name='Feedback')") == "role"
    assert infer_selector_strategy("page.get_by_text('Save as draft')") == "text"
    assert infer_selector_strategy('[placeholder="Add reflection"]') == "placeholder"
    assert infer_selector_strategy('[name="reflection"]') == "name"
    assert infer_selector_strategy('[data-testid="reflection"]') == "data"
    assert infer_selector_strategy("//textarea[4]") == "xpath"


def test_selector_log_analysis_prefers_semantic_selector_but_keeps_fallback(monkeypatch, tmp_path):
    import selector_logger

    monkeypatch.setattr(selector_logger, "SELECTOR_LOG_DIR", tmp_path)
    log_dir = tmp_path / "kaizen" / "CBD"
    log_dir.mkdir(parents=True)

    labels = ["Clinical reasoning", "Reflection", "Date of event"]
    for idx in range(3):
        steps = []
        for label in labels:
            steps.append(
                {
                    "step": len(steps) + 1,
                    "action": "type",
                    "selector": f"//textarea[@data-label='{label}']",
                    "label": label,
                    "success": True,
                    "selector_meta": {"strategy": "xpath"},
                }
            )
            if idx < 2:
                steps.append(
                    {
                        "step": len(steps) + 1,
                        "action": "type",
                        "selector": f"page.get_by_label('{label}')",
                        "label": label,
                        "success": True,
                        "selector_meta": {"strategy": "label", "kind": "label"},
                    }
                )
        (log_dir / f"session-{idx}.json").write_text(
            json.dumps({"success_count": len(steps), "steps": steps})
        )

    selectors = selector_logger.analyse_selectors("kaizen", "CBD")
    plans = selector_logger.analyse_selector_plans("kaizen", "CBD")

    assert selectors["Reflection"] == "page.get_by_label('Reflection')"
    assert plans["Reflection"]["preferred"]["strategy"] == "label"
    assert any(
        candidate["strategy"] == "xpath"
        for candidate in plans["Reflection"]["candidates"]
    )


@pytest.mark.asyncio
async def test_kaizen_filer_executes_logged_playwright_semantic_candidates():
    from kaizen_form_filer import _first_field_locator

    page = MagicMock()
    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.first = "label-first"
    page.get_by_label.return_value = label_locator

    plan = {
        "candidates": [
            {
                "strategy": "label",
                "kind": "label",
                "value": "page.get_by_label('Reflection')",
                "expected_unique": True,
            },
            {
                "strategy": "xpath",
                "kind": "playwright",
                "value": "//textarea[4]",
                "expected_unique": True,
            },
        ]
    }

    locator = await _first_field_locator(page, plan, field_key="reflection")

    assert locator == "label-first"
    page.get_by_label.assert_called_once_with("Reflection")
    page.locator.assert_not_called()
