"""Where Portfolio Guru keeps its runtime data.

Production uses ``~/.openclaw/data/portfolio-guru``. ``PORTFOLIO_GURU_DATA_DIR``
moves every store at once; the test suite sets it so no test run can write
into the live bot's databases, logs or drafts. Per-store overrides (for
example ``PORTFOLIO_GURU_FILING_LOG_PATH``) still take precedence.
"""
import os
from pathlib import Path

DATA_DIR_ENV = "PORTFOLIO_GURU_DATA_DIR"


def data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".openclaw" / "data" / "portfolio-guru"


def data_path(*parts: str) -> Path:
    return data_dir().joinpath(*parts)
