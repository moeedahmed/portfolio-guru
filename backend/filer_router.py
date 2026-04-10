"""
Filer router — single entry point for all form filing.
Picks the right approach: deterministic Playwright (fast, free) or browser-use (universal, AI-driven).
Uses filing coverage data to auto-escalate when Playwright reliability is low.

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
            # 2025 Update forms
            "CBD", "DOPS", "LAT", "ACAT", "ACAF", "STAT", "MSF",
            "MINI_CEX", "JCF", "QIAT", "TEACH", "PROC_LOG", "SDL",
            "US_CASE", "ESLE", "ESLE_ASSESS", "COMPLAINT", "SERIOUS_INC",
            "EDU_ACT", "FORMAL_COURSE", "REFLECT_LOG", "TEACH_OBS",
            # 2021 versions
            "CBD_2021", "DOPS_2021", "ACAT_2021", "ACAF_2021", "STAT_2021",
            "MINI_CEX_2021", "JCF_2021", "ESLE_2021", "TEACH_2021",
            "PROC_LOG_2021", "SDL_2021", "US_CASE_2021", "COMPLAINT_2021",
            "SERIOUS_INC_2021", "EDU_ACT_2021", "FORMAL_COURSE_2021",
            "TEACH_OBS_2021", "TEACH_CONFID_2021",
            # Management section
            "MGMT_ROTA", "MGMT_RISK", "MGMT_RECRUIT", "MGMT_PROJECT",
            "MGMT_RISK_PROC", "MGMT_TRAINING_EVT", "MGMT_GUIDELINE",
            "MGMT_INFO", "MGMT_INDUCTION", "MGMT_EXPERIENCE", "MGMT_REPORT",
            "TEACH_CONFID", "APPRAISAL", "BUSINESS_CASE", "CLIN_GOV",
            "MGMT_COMPLAINT", "COST_IMPROVE", "CRIT_INCIDENT", "EQUIP_SERVICE",
            # Research, Audit & QI
            "AUDIT", "RESEARCH",
            # Educational Review & Meetings
            "EDU_MEETING", "EDU_MEETING_SUPP", "PDP",
            # Other
            "ADD_POST", "ADD_SUPERVISOR", "HIGHER_PROG",
            "ABSENCE", "CCT", "FILE_UPLOAD", "FILE_UPLOAD_2021", "OOP",
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
        from kaizen_form_filer import FORM_UUIDS
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
    submit: bool = False,
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
    from filing_coverage import should_use_browser_use, record_run

    platform_lower = platform.lower()
    platform_config = PLATFORM_REGISTRY.get(platform_lower)

    # Coverage-based escalation: check if browser-use should override deterministic
    has_curriculum = bool(curriculum_links)
    use_browser = should_use_browser_use(form_type, has_curriculum)

    if platform_config and platform_config.get("deterministic") and form_type in platform_config.get("supported_forms", []):
        if use_browser:
            # Coverage says Playwright isn't reliable enough yet — escalate
            logger.info(f"Coverage check: escalating {form_type} to browser-use (low confidence in Playwright)")
        else:
            # Playwright is reliable — use deterministic path
            logger.info(f"Using deterministic filer for {platform}/{form_type}")
            result = await _route_deterministic(platform_lower, form_type, fields, credentials, curriculum_links, submit=submit)

            # Auto-escalation: if Playwright returned partial with important fields skipped
            # OR if no fields were filled at all (no DOM mapping for this form type)
            skipped = set(result.get("skipped", []))
            filled = result.get("filled", [])
            important_skipped = skipped & {"curriculum_links", "key_capabilities"}
            no_fields_filled = result["status"] == "partial" and len(filled) == 0
            if result["status"] == "partial" and (important_skipped or no_fields_filled):
                logger.info(f"Playwright partial for {form_type}, escalating to browser-use (skipped: {important_skipped})")
                # Record the partial Playwright run
                record_run(form_type, "deterministic", result.get("filled", []), result.get("skipped", []))
                # Fall through to browser-use below
            else:
                # Record and return
                record_run(form_type, "deterministic", result.get("filled", []), result.get("skipped", []))
                return result

    # Browser-use path
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

    result = await _route_browser_use(
        platform=platform_lower,
        platform_url=resolved_url,
        form_url=form_url,
        form_name=form_name or form_type,
        form_type=form_type,
        fields=fields,
        credentials=credentials,
        curriculum_links=curriculum_links,
    )

    # Record browser-use run
    record_run(form_type, "browser-use", result.get("filled", []), result.get("skipped", []))

    # DOM learning: if browser-use discovered new UUIDs, patch kaizen_form_filer.py
    if result.get("discovered_uuids"):
        try:
            from dom_learner import learn_from_browser_use_run
            learned = await learn_from_browser_use_run(form_type, result)
            if learned:
                logger.info(f"DOM learner added {len(learned)} new fields for {form_type}")
        except Exception as e:
            logger.warning(f"DOM learner failed for {form_type}: {e}")

    return result


async def _route_deterministic(
    platform: str,
    form_type: str,
    fields: Dict[str, Any],
    credentials: Dict[str, str],
    curriculum_links: Optional[List[str]],
    submit: bool = False,
) -> Dict[str, Any]:
    """Route to the deterministic Playwright filer."""
    if platform == "kaizen":
        from kaizen_form_filer import file_to_kaizen
        result = await file_to_kaizen(
            form_type=form_type,
            fields=fields,
            username=credentials["username"],
            password=credentials["password"],
            curriculum_links=curriculum_links,
            submit=submit,
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
