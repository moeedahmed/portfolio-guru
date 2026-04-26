"""
Regression tests for curriculum KC tag filling via 'Add tags' modal retry.

Bug (2026-04-23): DOPS / Large joint aspiration — _fill_curriculum_links checked
for ANY [kz-tree] on the page. DOPS has a non-curriculum kz-tree (procedural skill
multi-select), so tree_ready returned True, the "Add tags" modal was never opened,
and TICK_KCS_JS found 0 matching KCs. Both scope-push and DOM-click fallback
failed silently because the curriculum tree wasn't rendered.

Fix: after TICK_KCS_JS returns 0 ticked and no modal was opened, try opening the
"Add tags" modal and retry TICK_KCS_JS.
"""
import asyncio
import pytest
import sys
import os
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Patch asyncio.sleep globally for this module so tests don't wait
_orig_sleep = asyncio.sleep

@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _noop(*a, **kw):
        pass
    monkeypatch.setattr(asyncio, "sleep", _noop)
    yield


class TestCurriculumModalRetry:
    """When inline kz-tree exists but has NO matching curriculum KCs
    (e.g. DOPS has a procedural-skill kz-tree), _fill_curriculum_links
    must open the 'Add tags' modal and retry TICK_KCS_JS."""

    @pytest.mark.asyncio
    async def test_retries_via_modal_when_inline_tree_has_no_kcs(self):
        """Simulates DOPS: tree_ready=True (non-curriculum kz-tree exists),
        first TICK_KCS_JS returns 0 ticked (wrong tree), modal click succeeds,
        second TICK_KCS_JS returns the KCs found."""
        from kaizen_form_filer import _fill_curriculum_links, TICK_KCS_JS

        page = AsyncMock()
        call_count = {"tick": 0}

        kc_prefixes = [
            "Higher SLO6 Key Capability 1",
            "Higher SLO6 Key Capability 2",
        ]

        async def mock_evaluate(js_or_fn, *args):
            js_str = js_or_fn if isinstance(js_or_fn, str) else ""

            # 1. tree_ready check — True (non-curriculum kz-tree on page)
            if "querySelectorAll('[kz-tree] li')" in js_str:
                return True

            # 4. Verify JS (post-scope check) — must come before TICK_KCS_JS match
            if "checkbox" in js_str and "missed" in js_str:
                return {"missed": []}

            # 2. TICK_KCS_JS calls — match by the JS body, not args
            if "kcPrefixes" in js_str:
                call_count["tick"] += 1
                if call_count["tick"] == 1:
                    # First call: inline tree, no matching KCs
                    return {
                        "ticked": 0, "expanded": 0,
                        "results": [
                            {"prefix": p, "found": False} for p in kc_prefixes
                        ],
                    }
                else:
                    # Second call: inside modal, KCs found
                    return {
                        "ticked": 2, "expanded": 1,
                        "results": [
                            {"prefix": p, "found": True, "leafId": f"id-{i}", "ancestors": 1}
                            for i, p in enumerate(kc_prefixes)
                        ],
                    }

            # 3. "Add tags" button click
            if "Add tags" in js_str and "click()" in js_str:
                return True

            # 5. Modal commit
            if "ctrl.success" in js_str:
                return "commit:success"

            return None

        page.evaluate = AsyncMock(side_effect=mock_evaluate)

        ticked, errors = await _fill_curriculum_links(page, kc_prefixes, "Higher")

        assert call_count["tick"] == 2, (
            f"Expected TICK_KCS_JS called twice (inline miss + modal retry), "
            f"got {call_count['tick']}"
        )
        assert len(ticked) == 2, f"Expected 2 KCs ticked, got {len(ticked)}: {ticked}"
        assert not any("not found" in e for e in errors), f"Unexpected errors: {errors}"

    @pytest.mark.asyncio
    async def test_no_retry_when_inline_tree_succeeds(self):
        """When inline kz-tree already has matching KCs, no modal retry."""
        from kaizen_form_filer import _fill_curriculum_links

        page = AsyncMock()
        call_count = {"tick": 0}

        kc_prefixes = ["Higher SLO9 Key Capability 1"]

        async def mock_evaluate(js_or_fn, *args):
            js_str = js_or_fn if isinstance(js_or_fn, str) else ""

            if "querySelectorAll('[kz-tree] li')" in js_str:
                return True

            # Verify JS — must come before TICK_KCS_JS match
            if "checkbox" in js_str and "missed" in js_str:
                return {"missed": []}

            # TICK_KCS_JS — match by JS body
            if "kcPrefixes" in js_str:
                call_count["tick"] += 1
                return {
                    "ticked": 1, "expanded": 1,
                    "results": [{"prefix": kc_prefixes[0], "found": True, "leafId": "id-0", "ancestors": 1}],
                }

            return None

        page.evaluate = AsyncMock(side_effect=mock_evaluate)

        ticked, errors = await _fill_curriculum_links(page, kc_prefixes, "Higher")

        assert call_count["tick"] == 1, "Should NOT retry when inline tree succeeds"
        assert len(ticked) == 1

    @pytest.mark.asyncio
    async def test_opens_modal_when_no_tree_at_all(self):
        """When no kz-tree exists on page, should click 'Add tags' on first pass."""
        from kaizen_form_filer import _fill_curriculum_links

        page = AsyncMock()
        modal_clicks = {"count": 0}

        kc_prefixes = ["Higher SLO6 Key Capability 1"]

        async def mock_evaluate(js_or_fn, *args):
            js_str = js_or_fn if isinstance(js_or_fn, str) else ""

            if "querySelectorAll('[kz-tree] li')" in js_str:
                return False  # no tree at all

            if "Add tags" in js_str and "click()" in js_str:
                modal_clicks["count"] += 1
                return True

            if args and args[0] is kc_prefixes:
                return {
                    "ticked": 1, "expanded": 1,
                    "results": [{"prefix": kc_prefixes[0], "found": True, "leafId": "id-0", "ancestors": 1}],
                }

            if "checkbox" in js_str and "missed" in js_str:
                return {"missed": []}

            if "ctrl.success" in js_str:
                return "commit:success"

            return None

        page.evaluate = AsyncMock(side_effect=mock_evaluate)

        ticked, errors = await _fill_curriculum_links(page, kc_prefixes, "Higher")

        assert modal_clicks["count"] == 1, "Should open modal when no tree on page"
        assert len(ticked) == 1

    def test_dops_in_forms_using_tag_based_curriculum(self):
        """DOPS must be listed in FORMS_USING_TAG_BASED_CURRICULUM."""
        from kaizen_form_filer import FORMS_USING_TAG_BASED_CURRICULUM
        assert "DOPS" in FORMS_USING_TAG_BASED_CURRICULUM
