"""Portfolio Guru Engine — modular browser automation for medical portfolios.

This engine powers Portfolio Guru across any frontend (Telegram, Web, API).
It connects to a browser (local CDP, cloud, or headless) and knows how to
navigate Kaizen (and eventually other portfolio platforms) to extract data
and fill forms.

Designed for:
- Local Mac Mini development (browser-harness CDP)
- Server deployment (Browser Use Cloud or Playwright headless)
- Multiple frontends (Telegram bot, Web UI, REST API)
"""

from .browser.adapter import BrowserAdapter, BrowserUseHarnessAdapter
from .portfoliotypes.base import (
    detect_portfolio_type,
    get_role_config,
    load_selectors,
    load_2025_uuids,
)
from .providers.kaizen import KaizenProvider

__version__ = "0.1.0"
__all__ = [
    "BrowserAdapter",
    "BrowserUseHarnessAdapter",
    "KaizenProvider",
    "detect_portfolio_type",
    "get_role_config",
    "load_selectors",
    "load_2025_uuids",
]
