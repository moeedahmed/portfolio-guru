"""
Filer router — single entry point for all form filing.

Routing strategy (canonical — see AGENTS.md § Filing Routing Discipline):
- DOM-mapped forms → deterministic Playwright via browser-harness CDP only.
  Never escalate to browser-use. If Playwright returns partial, fix the DOM map.
- Unknown form types (no DOM mapping) → browser-use via CDP (localhost:18800).
  Credentials never enter LLM prompts — the persistent Chrome session handles auth.
- Unknown platforms → browser-harness + domain skills is the default.
  browser-use is an emergency bridge, replaced by written domain skills.

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
            "US_CASE", "ESLE", "ESLE_ASSESS", "ESLE_PART1_2", "COMPLAINT", "SERIOUS_INC",
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
    reuse_draft: bool = False,
    attachment_path: Optional[str] = None,
    attachment_drive_url: Optional[str] = None,
    telegram_user_id: Optional[int] = None,
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
        attachment_path: Local file path to upload (deterministic Kaizen path only).
        attachment_drive_url: Drive URL the filer downloads then uploads
            (deterministic Kaizen path only). Both attachment params are
            forwarded to file_to_kaizen; supplying them on the browser-use
            path returns an unsupported error rather than silently dropping
            the attachment.

    Returns:
        {
            "status": "success" | "partial" | "failed",
            "filled": [...],
            "skipped": [...],
            "error": str | None,
            "method": "deterministic" | "browser-use",
        }
    """
    from filing_coverage import record_run

    platform_lower = platform.lower()
    platform_config = PLATFORM_REGISTRY.get(platform_lower)
    requested_form_type = form_type
    if platform_lower == "kaizen":
        try:
            from kaizen_form_filer import canonical_form_type as canonical_kaizen_form_type
            form_type = canonical_kaizen_form_type(form_type)
        except ImportError:
            pass

    # Check if this form type has a deterministic DOM mapping
    try:
        from kaizen_form_filer import FORM_FIELD_MAP
        has_dom_mapping = form_type in FORM_FIELD_MAP
    except ImportError:
        has_dom_mapping = False

    # Strategy:
    # - Forms with DOM mappings → always try Playwright. Never escalate to browser-use.
    #   If Playwright returns partial, the DOM map gap is logged and returned — fix the map.
    # - Forms without DOM mappings → use browser-use (CDP-connected, no credentials in prompts).
    # - Unknown platforms → browser-use path.

    supported_forms = set(platform_config.get("supported_forms", [])) if platform_config else set()
    if platform_config and platform_config.get("deterministic") and (
        form_type in supported_forms or requested_form_type in supported_forms
    ):
        if not has_dom_mapping:
            # No DOM mapping — browser-use needed
            logger.info(f"No DOM mapping for {form_type} — using browser-use")
        else:
            # Has a DOM mapping — always try Playwright, never escalate
            logger.info(f"Using deterministic filer for {platform}/{form_type}")
            result = await _route_deterministic(
                platform_lower,
                form_type,
                fields,
                credentials,
                curriculum_links,
                submit=submit,
                reuse_draft=reuse_draft,
                attachment_path=attachment_path,
                attachment_drive_url=attachment_drive_url,
                telegram_user_id=telegram_user_id,
            )

            # Record and return regardless of status
            # If partial, the DOM map needs fixing — return it as-is
            record_run(form_type, "deterministic", result.get("filled", []), result.get("skipped", []))
            if result["status"] == "partial":
                logger.warning(f"Playwright partial for {form_type} (has DOM map) — skipped: {result.get('skipped', [])}")
            return result

    # Browser-use path (forms without DOM mappings + unknown platforms).
    # Hard guard: a form with a DOM mapping must never reach this branch. The
    # deterministic block above always returns when has_dom_mapping is True,
    # so this is defence-in-depth against a future refactor accidentally
    # falling through.
    if has_dom_mapping:
        logger.error(
            "Refusing to escalate %s/%s to browser-use: form has a DOM mapping",
            platform,
            form_type,
        )
        return {
            "status": "failed",
            "filled": [],
            "skipped": list(fields.keys()),
            "error": (
                f"Internal routing error: {form_type} has a DOM mapping but was "
                "routed to browser-use. Refused to escalate."
            ),
            "method": "deterministic",
        }
    if attachment_path or attachment_drive_url:
        return {
            "status": "failed",
            "filled": [],
            "skipped": list(fields.keys()),
            "error": (
                f"Attachments are not supported on the browser-use path "
                f"(form_type={form_type!r}). Build a DOM mapping for this form "
                "or remove attachment_path/attachment_drive_url."
            ),
            "method": "browser-use",
        }
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
    reuse_draft: bool = False,
    attachment_path: Optional[str] = None,
    attachment_drive_url: Optional[str] = None,
    telegram_user_id: Optional[int] = None,
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
            reuse_draft=reuse_draft,
            attachment_path=attachment_path,
            attachment_drive_url=attachment_drive_url,
            telegram_user_id=telegram_user_id,
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
