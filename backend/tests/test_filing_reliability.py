"""Focused offline tests for the Kaizen filing reliability cleanup.

These guard the invariants of the active sprint:

1. Normal filing does NOT reuse old drafts.
2. Explicit retry DOES reuse drafts.
3. Failure recovery exits remain available (the retry button + cancel).
4. DOM-mapped forms never escalate to browser-use.
5. Tracked artefacts (filing_coverage.json, dom_learning_log.json,
   kaizen_form_filer.py) are not mutated by ordinary tests.
6. Alias routing keeps ESLE / Mini-CEX style forms on the deterministic path.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


# ─── Tracked artefact protection ──────────────────────────────────────────


def test_filing_coverage_path_is_redirected_to_tmp(tmp_path, monkeypatch):
    """The autouse conftest fixture must route writes away from the repo file."""
    from filing_coverage import _resolve_coverage_path, _DEFAULT_COVERAGE_PATH

    resolved = _resolve_coverage_path()
    assert resolved != _DEFAULT_COVERAGE_PATH
    assert str(tmp_path) in str(resolved)


def test_live_default_coverage_path_is_not_the_tracked_repo_file(monkeypatch):
    """With no env override, the live default must not be backend/filing_coverage.json.

    Pins the acceptance criterion for the reliability cleanup: ordinary runtime
    writes go to an untracked path under the user's data dir, not to the tracked
    in-repo file.
    """
    monkeypatch.delenv("PORTFOLIO_GURU_FILING_COVERAGE_PATH", raising=False)
    from filing_coverage import _resolve_coverage_path

    resolved = _resolve_coverage_path()
    repo_path = Path(__file__).resolve().parent.parent / "filing_coverage.json"
    assert resolved != repo_path
    assert "backend" not in resolved.parts or resolved.parent.name != "backend"


def test_live_default_dom_learning_log_path_is_not_the_tracked_repo_file(monkeypatch):
    """Same guard for dom_learning_log.json — autolearn is opt-in but the default
    path should still live outside the repo so a future caller toggling the flag
    can't dirty tracked source by accident."""
    monkeypatch.delenv("PORTFOLIO_GURU_DOM_LEARNING_LOG_PATH", raising=False)
    from dom_learner import _resolve_learning_log_path

    resolved = _resolve_learning_log_path()
    repo_path = Path(__file__).resolve().parent.parent / "dom_learning_log.json"
    assert resolved != repo_path


def test_record_run_writes_to_redirected_path(tmp_path):
    from filing_coverage import _resolve_coverage_path, record_run

    record_run("CBD", "deterministic", filled_fields=["reflection"], skipped_fields=[])

    target = _resolve_coverage_path()
    assert target.exists()
    # The default in-repo path must not be touched.
    repo_path = Path(__file__).resolve().parent.parent / "filing_coverage.json"
    repo_contents_before = repo_path.read_text() if repo_path.exists() else None
    record_run("CBD", "deterministic", filled_fields=["reflection"], skipped_fields=[])
    repo_contents_after = repo_path.read_text() if repo_path.exists() else None
    assert repo_contents_before == repo_contents_after


@pytest.mark.asyncio
async def test_dom_autolearn_is_off_by_default():
    """Without the opt-in env var, learn_from_browser_use_run is a no-op."""
    from dom_learner import learn_from_browser_use_run

    payload = {
        "discovered_uuids": {
            "totally_new_field": "00000000-1111-2222-3333-444444444444",
        }
    }
    result = await learn_from_browser_use_run("CBD", payload)
    assert result == {}


@pytest.mark.asyncio
async def test_dom_autolearn_opt_in_uses_redirected_filer_path(monkeypatch, tmp_path):
    """With opt-in + redirected path, learning patches the tmp copy, not the repo."""
    from dom_learner import learn_from_browser_use_run

    # Pre-populate a minimal FORM_FIELD_MAP block so the patcher can match it.
    filer_copy = tmp_path / "kaizen_form_filer.py"
    filer_copy.write_text(
        'FORM_FIELD_MAP = {\n'
        '    "CBD": {\n'
        '        "reflection": "existing-uuid",\n'
        '    },\n'
        '}\n'
    )
    monkeypatch.setenv("PORTFOLIO_GURU_KAIZEN_FILER_PATH", str(filer_copy))
    monkeypatch.setenv("PORTFOLIO_GURU_DOM_AUTOLEARN", "1")

    # Stub the existing-map lookup so we don't hit the real module.
    with patch("dom_learner._get_current_field_map", return_value={}):
        payload = {
            "discovered_uuids": {
                "totally_new_field": "00000000-1111-2222-3333-444444444444",
            }
        }
        result = await learn_from_browser_use_run("CBD", payload)

    assert "totally_new_field" in result
    assert "totally_new_field" in filer_copy.read_text()
    # The real, tracked filer must remain untouched.
    real_filer = Path(__file__).resolve().parent.parent / "kaizen_form_filer.py"
    assert "totally_new_field" not in real_filer.read_text()


# ─── Browser-use isolation for DOM-mapped forms ──────────────────────────


@pytest.mark.asyncio
async def test_dom_mapped_form_takes_deterministic_path():
    from filer_router import route_filing

    deterministic = AsyncMock(return_value={
        "status": "success",
        "filled": ["reflection"],
        "skipped": [],
    })
    browser_use = AsyncMock(return_value={
        "status": "success",
        "filled": [],
        "skipped": [],
        "method": "browser-use",
    })

    with patch("filer_router._route_deterministic", new=deterministic), \
         patch("filer_router._route_browser_use", new=browser_use):
        result = await route_filing(
            platform="kaizen",
            form_type="CBD",
            fields={"reflection": "Sample reflection"},
            credentials={"username": "u", "password": "p"},
        )

    assert result["status"] == "success"
    deterministic.assert_awaited_once()
    browser_use.assert_not_awaited()


@pytest.mark.asyncio
async def test_dom_mapped_form_refused_if_routed_to_browser_use(monkeypatch):
    """If a future refactor falls through, the explicit guard refuses to escalate."""
    from filer_router import route_filing

    # Force the deterministic branch to be skipped by treating the form as
    # "supported but no DOM mapping" via patching the lookup. Then patch the
    # has_dom_mapping check to True so the guard triggers.
    browser_use = AsyncMock(return_value={
        "status": "success",
        "filled": ["reflection"],
        "skipped": [],
        "method": "browser-use",
    })

    with patch("filer_router._route_browser_use", new=browser_use), \
         patch("filer_router._route_deterministic", new=AsyncMock()):
        # Simulate the buggy fall-through: pretend the deterministic block did
        # not fire (form not in supported list) but a DOM map still exists.
        with patch.dict("filer_router.PLATFORM_REGISTRY", {
            "kaizen": {
                "login_url": "https://eportfolio.rcem.ac.uk",
                "form_url_pattern": "https://kaizenep.com/events/new-section/{uuid}",
                "deterministic": True,
                "supported_forms": [],  # force fall-through
            },
        }, clear=True):
            with patch("kaizen_form_filer.FORM_FIELD_MAP", {"CBD": {"reflection": "uuid"}}):
                result = await route_filing(
                    platform="kaizen",
                    form_type="CBD",
                    fields={"reflection": "Sample"},
                    credentials={"username": "u", "password": "p"},
                )

    assert result["status"] == "failed"
    assert "DOM mapping" in result["error"]
    browser_use.assert_not_awaited()


# ─── Reuse / retry contract ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_filing_does_not_reuse_drafts_by_default():
    from filer_router import route_filing

    deterministic = AsyncMock(return_value={
        "status": "success",
        "filled": ["reflection"],
        "skipped": [],
    })
    with patch("filer_router._route_deterministic", new=deterministic):
        await route_filing(
            platform="kaizen",
            form_type="CBD",
            fields={"reflection": "Sample"},
            credentials={"username": "u", "password": "p"},
        )
    assert deterministic.await_args.kwargs["reuse_draft"] is False


@pytest.mark.asyncio
async def test_route_filing_reuses_drafts_when_explicitly_requested():
    from filer_router import route_filing

    deterministic = AsyncMock(return_value={
        "status": "success",
        "filled": ["reflection"],
        "skipped": [],
    })
    with patch("filer_router._route_deterministic", new=deterministic):
        await route_filing(
            platform="kaizen",
            form_type="CBD",
            fields={"reflection": "Sample"},
            credentials={"username": "u", "password": "p"},
            reuse_draft=True,
        )
    assert deterministic.await_args.kwargs["reuse_draft"] is True


@pytest.mark.asyncio
async def test_retry_after_dom_drift_reuses_draft_and_surfaces_changed_field():
    from filer_router import route_filing

    draft_url = "https://kaizenep.com/events/fillin/draft-doc-id?autosave=auto-1"
    calls = []

    async def deterministic(
        platform,
        form_type,
        fields,
        credentials,
        curriculum_links,
        **kwargs,
    ):
        calls.append({
            "fields": dict(fields),
            "reuse_draft": kwargs["reuse_draft"],
        })
        if kwargs["reuse_draft"]:
            return {
                "status": "partial",
                "filled": ["reflection"],
                "skipped": ["clinical_reasoning_renamed"],
                "error": None,
                "saved_url": draft_url,
            }
        return {
            "status": "success",
            "filled": ["reflection", "clinical_reasoning"],
            "skipped": [],
            "error": None,
            "saved_url": draft_url,
        }

    with patch("filer_router._route_deterministic", new=deterministic):
        first = await route_filing(
            platform="kaizen",
            form_type="CBD",
            fields={
                "reflection": "Initial reflection",
                "clinical_reasoning": "Initial reasoning",
            },
            credentials={"username": "u", "password": "p"},
        )
        retry = await route_filing(
            platform="kaizen",
            form_type="CBD",
            fields={
                "reflection": "Initial reflection",
                "clinical_reasoning_renamed": "DOM drifted field",
            },
            credentials={"username": "u", "password": "p"},
            reuse_draft=True,
        )

    assert calls == [
        {
            "fields": {
                "reflection": "Initial reflection",
                "clinical_reasoning": "Initial reasoning",
            },
            "reuse_draft": False,
        },
        {
            "fields": {
                "reflection": "Initial reflection",
                "clinical_reasoning_renamed": "DOM drifted field",
            },
            "reuse_draft": True,
        },
    ]
    assert first["saved_url"] == draft_url
    assert retry["status"] == "partial"
    assert retry["saved_url"] == draft_url
    assert retry["skipped"] == ["clinical_reasoning_renamed"]
    assert "new-section" not in retry["saved_url"]


# ─── Alias routing safeguards (ESLE / Mini-CEX style) ────────────────────


@pytest.mark.asyncio
async def test_mini_cex_2021_stays_on_deterministic_path():
    """Mini-CEX 2021 has a DOM map alias to MINI_CEX — it must use Playwright."""
    from filer_router import route_filing

    deterministic = AsyncMock(return_value={
        "status": "success",
        "filled": ["patient_presentation"],
        "skipped": [],
    })
    browser_use = AsyncMock(return_value={
        "status": "success",
        "filled": [],
        "skipped": [],
        "method": "browser-use",
    })
    with patch("filer_router._route_deterministic", new=deterministic), \
         patch("filer_router._route_browser_use", new=browser_use):
        await route_filing(
            platform="kaizen",
            form_type="MINI_CEX_2021",
            fields={"patient_presentation": "Unstable AF"},
            credentials={"username": "u", "password": "p"},
        )

    deterministic.assert_awaited_once()
    browser_use.assert_not_awaited()
    # The form_type passed to the deterministic filer is the canonical alias
    # target so the underlying Playwright code reuses the existing DOM map.
    assert deterministic.await_args.args[1] == "MINI_CEX_2021"


@pytest.mark.asyncio
async def test_user_facing_esle_routes_to_deterministic_assessed_form():
    """User-facing ESLE → ESLE_ASSESS at extractor → ESLE_PART1_2 at filer.

    The extractor's canonical_form_type maps "ESLE" → "ESLE_ASSESS" before the
    bot ever calls route_filing, so the router only sees ESLE_ASSESS / ESLE_2021
    / ESLE_PART1_2 in practice. This test pins the router-side behaviour: when
    given ESLE_ASSESS, the deterministic filer receives the historical DOM
    map name ESLE_PART1_2.
    """
    from filer_router import route_filing

    deterministic = AsyncMock(return_value={
        "status": "success",
        "filled": ["reflection"],
        "skipped": [],
    })
    with patch("filer_router._route_deterministic", new=deterministic):
        await route_filing(
            platform="kaizen",
            form_type="ESLE_ASSESS",
            fields={"reflection": "Sample"},
            credentials={"username": "u", "password": "p"},
        )

    assert deterministic.await_args.args[1] == "ESLE_PART1_2"


# ─── Legacy filer deprecation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_file_cbd_to_kaizen_raises_without_opt_in(monkeypatch):
    """The legacy credentials-in-prompt path must not fire by accident."""
    from filer import file_cbd_to_kaizen
    from models import CBDData

    monkeypatch.delenv("PORTFOLIO_GURU_ALLOW_LEGACY_FILER", raising=False)
    cbd = CBDData(
        date_of_encounter="2026-05-21",
        stage_of_training="Higher/ST4-ST6",
        clinical_reasoning="Sample",
        reflection="Sample",
    )
    with pytest.raises(NotImplementedError):
        await file_cbd_to_kaizen(cbd, "user", "pass")


# ─── Non-bot entrypoints stay draft-only ─────────────────────────────────


def test_kaizen_fill_request_rejects_submit_payload():
    """The /api/kaizen/file model must refuse save_as_draft=False at the boundary."""
    from pydantic import ValidationError
    from models import KaizenFillRequest

    # Sanity: defaults and explicit True both parse.
    KaizenFillRequest(form_type="CBD", fields={"reflection": "x"})
    KaizenFillRequest(form_type="CBD", fields={"reflection": "x"}, save_as_draft=True)

    with pytest.raises(ValidationError):
        KaizenFillRequest(form_type="CBD", fields={"reflection": "x"}, save_as_draft=False)


@pytest.mark.asyncio
async def test_kaizen_file_endpoint_routes_through_filer_router(monkeypatch):
    """/api/kaizen/file must route via filer_router with submit=False, never
    call fill_kaizen_form directly."""
    import main
    from models import KaizenFillRequest

    monkeypatch.setenv("KAIZEN_USERNAME", "u@example.com")
    monkeypatch.setenv("KAIZEN_PASSWORD", "secret")

    route_mock = AsyncMock(return_value={
        "status": "success",
        "filled": ["reflection"],
        "skipped": [],
        "method": "deterministic",
    })
    direct_fill = AsyncMock()

    with patch("filer_router.route_filing", new=route_mock), \
         patch("kaizen_form_filer.fill_kaizen_form", new=direct_fill):
        response = await main.kaizen_file(
            KaizenFillRequest(form_type="CBD", fields={"reflection": "Sample"}),
        )

    route_mock.assert_awaited_once()
    assert route_mock.await_args.kwargs["platform"] == "kaizen"
    assert route_mock.await_args.kwargs["form_type"] == "CBD"
    assert route_mock.await_args.kwargs["submit"] is False
    direct_fill.assert_not_awaited()
    assert response.status == "success"
    assert response.filled == ["reflection"]


def test_fill_one_validate_rejects_save_as_draft_false():
    """fill_one.py must refuse tickets with save_as_draft=false."""
    import fill_one

    ticket = {
        "form_type": "CBD",
        "fields": {"reflection": "x"},
        "save_as_draft": False,
    }
    with pytest.raises(SystemExit) as exc_info:
        fill_one._validate(ticket)
    assert exc_info.value.code == 3


def test_fill_one_validate_accepts_draft_only_default():
    """Default + explicit True both pass validation (smoke for the guard)."""
    import fill_one

    # Default (no save_as_draft key) → True.
    form_type, fields, draft_uuid, save_as_draft, _, _ = fill_one._validate({
        "form_type": "CBD",
        "fields": {"reflection": "x"},
    })
    assert save_as_draft is True

    # Explicit True.
    _, _, _, save_as_draft, _, _ = fill_one._validate({
        "form_type": "CBD",
        "fields": {"reflection": "x"},
        "save_as_draft": True,
    })
    assert save_as_draft is True
