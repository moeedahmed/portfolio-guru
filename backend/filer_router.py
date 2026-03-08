"""
Filer router — single entry point for all form filing.
Picks the right approach: deterministic Playwright (fast, free) or browser-use (universal, AI-driven).

Usage:
    result = await route_filing(
        platform="kaizen",
        form_type="CBD",
        fields={...},
        credentials={"username": "...", "password": "..."},
    )
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Registry of platforms with deterministic filers
PLATFORM_REGISTRY = {
    "kaizen": {
        "login_url": "https://eportfolio.rcem.ac.uk",
        "form_url_pattern": "https://kaizenep.com/events/new-section/{uuid}",
        "deterministic": True,
        "supported_forms": [
            "CBD", "DOPS", "LAT", "ACAT", "ACAF", "STAT", "MSF",
            "MINI_CEX", "JCF", "QIAT", "TEACH", "PROC_LOG", "SDL",
            "US_CASE", "ESLE", "COMPLAINT", "SERIOUS_INC", "EDU_ACT",
            "FORMAL_COURSE",
        ],
    },
    # Future platforms — browser-use only until DOM mappings built
    "horus": {
        "login_url": "https://horus.nhs.uk",
        "form_url_pattern": None,
        "deterministic": False,
        "supported_forms": [],  # All forms go through browser-use
    },
    "soar": {
        "login_url": "https://soar.nhs.uk",
        "form_url_pattern": None,
        "deterministic": False,
        "supported_forms": [],
    },
}

# Form UUIDs for Kaizen (imported at call time to avoid circular imports)
_kaizen_uuids = None


def _get_kaizen_uuids():
    global _kaizen_uuids
    if _kaizen_uuids is None:
        from kaizen_filer import FORM_UUIDS
        _kaizen_uuids = FORM_UUIDS
    return _kaizen_uuids


async def route_filing(
    platform: str,
    form_type: str,
    fields: Dict[str, Any],
    credentials: Dict[str, str],
    curriculum_links: Optional[List[str]] = None,
    form_name: Optional[str] = None,
    platform_url: Optional[str] = None,
    form_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Route a filing request to the appropriate filer.

    Args:
        platform: Platform identifier ("kaizen", "horus", "soar", etc.)
        form_type: Form short code ("CBD", "DOPS", etc.)
        fields: Extracted field data
        credentials: {"username": "...", "password": "..."}
        curriculum_links: SLO codes to tick (optional)
        form_name: Human-readable form name (for browser-use)
        platform_url: Override login URL (for unknown platforms)
        form_url: Direct URL to form (for browser-use with known form URLs)

    Returns:
        {
            "status": "success" | "partial" | "failed",
            "filled": [...],
            "skipped": [...],
            "error": str | None,
            "method": "deterministic" | "browser-use",
        }
    """
    platform_lower = platform.lower()
    platform_config = PLATFORM_REGISTRY.get(platform_lower)

    # Deterministic path: known platform with mapped forms
    if platform_config and platform_config.get("deterministic") and form_type in platform_config.get("supported_forms", []):
        logger.info(f"Using deterministic filer for {platform}/{form_type}")
        return await _route_deterministic(platform_lower, form_type, fields, credentials, curriculum_links)

    # Browser-use path: unmapped platform or unmapped form type
    logger.info(f"Using browser-use filer for {platform}/{form_type}")
    resolved_url = platform_url or (platform_config or {}).get("login_url")
    if not resolved_url:
        return {
            "status": "failed",
            "filled": [],
            "skipped": list(fields.keys()),
            "error": f"Unknown platform '{platform}' — no login URL configured",
            "method": "browser-use",
        }

    # Build form URL if pattern exists and we have a UUID
    if not form_url and platform_config and platform_config.get("form_url_pattern"):
        uuids = _get_kaizen_uuids() if platform_lower == "kaizen" else {}
        uuid = uuids.get(form_type)
        if uuid:
            form_url = platform_config["form_url_pattern"].format(uuid=uuid)

    return await _route_browser_use(
        platform=platform_lower,
        platform_url=resolved_url,
        form_url=form_url,
        form_name=form_name or form_type,
        form_type=form_type,
        fields=fields,
        credentials=credentials,
        curriculum_links=curriculum_links,
    )


async def _route_deterministic(
    platform: str,
    form_type: str,
    fields: Dict[str, Any],
    credentials: Dict[str, str],
    curriculum_links: Optional[List[str]],
) -> Dict[str, Any]:
    """Route to the deterministic Playwright filer."""
    if platform == "kaizen":
        from kaizen_filer import file_to_kaizen
        result = await file_to_kaizen(
            form_type=form_type,
            fields=fields,
            username=credentials["username"],
            password=credentials["password"],
            curriculum_links=curriculum_links,
        )
        result["method"] = "deterministic"
        return result

    # Future: other platform-specific filers
    return {
        "status": "failed",
        "filled": [],
        "skipped": list(fields.keys()),
        "error": f"No deterministic filer implemented for {platform}",
        "method": "deterministic",
    }


async def _route_browser_use(
    platform: str,
    platform_url: str,
    form_url: Optional[str],
    form_name: str,
    form_type: str,
    fields: Dict[str, Any],
    credentials: Dict[str, str],
    curriculum_links: Optional[List[str]],
) -> Dict[str, Any]:
    """Route to the browser-use AI filer."""
    from browser_filer import file_with_browser_use

    result = await file_with_browser_use(
        platform_url=platform_url,
        form_name=form_name,
        fields=fields,
        credentials=credentials,
        form_url=form_url,
        form_type=form_type,
        curriculum_links=curriculum_links,
        platform=platform,
    )
    return result
