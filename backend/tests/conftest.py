import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end tests requiring Telegram credentials")
    config.addinivalue_line("markers", "live: live Telegram tests (requires personal account session)")
    config.addinivalue_line("markers", "kaizen: live Kaizen integration tests (requires credentials, manual only)")


@pytest.fixture(autouse=True)
def _isolate_filing_artefacts(tmp_path, monkeypatch):
    """Redirect tracked filing artefacts to per-test tmp paths.

    filing_coverage.json and dom_learning_log.json are tracked in git, and the
    deterministic Playwright source kaizen_form_filer.py is the only DOM map.
    Tests that exercise filer_router.route_filing call record_run, which would
    otherwise mutate the tracked coverage file. Auto-learning is feature-gated
    in dom_learner, but we still null the paths here so a future caller that
    sets PORTFOLIO_GURU_DOM_AUTOLEARN=1 in CI cannot rewrite tracked source.
    """
    coverage_path = tmp_path / "filing_coverage.json"
    learning_log_path = tmp_path / "dom_learning_log.json"
    filer_copy = tmp_path / "kaizen_form_filer.py"

    monkeypatch.setenv("PORTFOLIO_GURU_FILING_COVERAGE_PATH", str(coverage_path))
    monkeypatch.setenv("PORTFOLIO_GURU_DOM_LEARNING_LOG_PATH", str(learning_log_path))
    monkeypatch.setenv("PORTFOLIO_GURU_KAIZEN_FILER_PATH", str(filer_copy))
    monkeypatch.delenv("PORTFOLIO_GURU_DOM_AUTOLEARN", raising=False)
    yield


@pytest.fixture(autouse=True)
def _default_gathering_mode_off(monkeypatch):
    """Default tests to the legacy instant-draft flow.

    Gathering mode is the production default, but most tests pre-date that and
    assert on the form-choice / draft preview paths. Setting PG_GATHERING_MODE
    to off here mirrors a deployment-level opt-out so those tests keep
    exercising the instant-draft flow. Tests that need to exercise gathering
    call monkeypatch.delenv("PG_GATHERING_MODE", raising=False) themselves.
    """
    monkeypatch.setenv("PG_GATHERING_MODE", "off")
    yield

@pytest.fixture
def mock_update():
    """Fake Telegram update — simulates a user sending a message."""
    update = MagicMock()
    update.effective_user.id = 99999999
    update.effective_user.first_name = "TestDoctor"
    update.message = MagicMock()
    update.message.text = None
    update.message.reply_text = AsyncMock()
    update.message.edit_text = AsyncMock()
    update.callback_query = None
    return update

@pytest.fixture
def mock_context():
    """Fake bot context with user_data store."""
    context = MagicMock()
    context.bot = AsyncMock()
    context.user_data = {}
    return context

@pytest.fixture
def mock_callback_update():
    """Fake Telegram update for button taps."""
    update = MagicMock()
    update.effective_user.id = 99999999
    update.callback_query = MagicMock()
    update.callback_query.data = None
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.text = "test"
    return update
