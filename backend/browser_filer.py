"""
Generic browser-use filer — fills any e-portfolio form on any platform.
Uses AI-driven browser navigation when no deterministic mapping exists.

Usage:
    result = await file_with_browser_use(
        platform_url="https://eportfolio.rcem.ac.uk",
        form_name="Case-Based Discussion",
        fields={"case_to_discuss": "...", "reflection": "..."},
        credentials={"username": "...", "password": "..."},
    )
"""

from kaizen_offline import require_online

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from data_paths import data_path

from selector_logger import SelectorLogger
from model_config import browser_fallback_model, gemini_fast_model

logger = logging.getLogger(__name__)

BROWSER_USE_LOG_DIR = data_path("browser-use-logs")

# Safety guards, following Anthropic's "Run the toolset safely" checklist for
# browser agents. The agent reads untrusted page text, so it only ever gets a
# fresh browser holding one doctor's own session, reaches only the platform's
# own hosts, and cannot run script in the page or touch local files.

# Exact hosts per platform. "*.host" covers subdomains of that host only.
_PLATFORM_HOSTS = {
    "kaizen": ("eportfolio.rcem.ac.uk", "kaizenep.com", "*.kaizenep.com"),
}

# Ops escape hatch for a host Kaizen starts to need (comma-separated, exact
# names or "*.name"). Empty by default.
_EXTRA_HOSTS_ENV = "PG_BROWSER_USE_EXTRA_HOSTS"

# Actions the agent never gets: running JavaScript in the page, uploading or
# reading/writing local files, and web search (which leaves the allowlist).
EXCLUDED_ACTIONS = (
    "evaluate",
    "upload_file",
    "read_file",
    "write_file",
    "replace_file",
    "save_as_pdf",
    "search",
)

# browser-use sends usage telemetry (including the full task text, which holds
# the case details, and the URLs visited) to its vendor by default, and cloud
# sync follows the same switch. Both stay off for every run.
_TELEMETRY_OFF = {"ANONYMIZED_TELEMETRY": "false", "BROWSER_USE_CLOUD_SYNC": "false"}


def _disable_vendor_telemetry() -> None:
    os.environ.update(_TELEMETRY_OFF)


# Set on import too: browser-use builds its telemetry client once per process,
# so the first Agent anywhere in the process decides it.
_disable_vendor_telemetry()

_WORK_DIR_PREFIX = "pg-browser-use-"
_STALE_WORK_DIR_SECONDS = 3600


def _sweep_stale_work_dirs() -> None:
    """Remove throwaway browser dirs a crashed run left behind (they hold a
    decrypted session)."""
    cutoff = datetime.now().timestamp() - _STALE_WORK_DIR_SECONDS
    for path in Path(tempfile.gettempdir()).glob(f"{_WORK_DIR_PREFIX}*"):
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


_HOST_PATTERN = re.compile(r"^(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)+$")


def allowed_hosts(platform: str, platform_url: str) -> list[str]:
    """Hosts the agent may load, for this platform only. Empty means refuse."""
    hosts = list(_PLATFORM_HOSTS.get((platform or "").lower(), ()))
    if not hosts:
        parsed = urlparse(platform_url or "")
        if parsed.scheme == "https" and parsed.hostname:
            hosts = [parsed.hostname.lower()]
    extra = os.environ.get(_EXTRA_HOSTS_ENV, "")
    hosts.extend(h.strip().lower() for h in extra.split(",") if h.strip())
    return [h for h in dict.fromkeys(hosts) if _HOST_PATTERN.match(h)]


def url_is_allowed(url: str, hosts: list[str]) -> bool:
    """https URL whose host is listed exactly, or under a listed "*.host"."""
    try:
        parsed = urlparse((url or "").replace("\\", "/"))
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    for pattern in hosts:
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False


def host_resolver_rule(hosts: list[str]) -> str:
    """Chrome flag value that fails DNS for every host not on the list.

    This covers what a navigation check cannot see: images, scripts, fetches,
    websockets and service workers a page starts on its own.
    """
    excludes = ", ".join(f"EXCLUDE {h}" for h in hosts)
    return f"MAP * ~NOTFOUND, {excludes}"


# Field key → human-readable label mapping for task prompt
FIELD_LABELS = {
    # Common across forms
    "date_of_encounter": "Date occurred on",
    "date_of_event": "Date of event",
    "stage_of_training": "Stage of training",
    "clinical_reasoning": "Case to be discussed",
    "case_observed": "Case observed",
    "cases_observed": "Cases observed",
    "reflection": "Reflection of event",
    "placement": "Placement",
    "clinical_setting": "Clinical setting",
    "description": "Description",
    # CBD
    "case_to_discuss": "Case to be discussed",
    # DOPS / Mini-CEX
    "case_description": "Case observed",
    # LAT
    "trainee_post": "Trainee Post (current position)",
    "leadership_priorities": "Leadership priorities for the year",
    "description_of_event": "Describe what happened in detail",
    # ACAF
    "situation": "Situation description",
    "population": "Population or Problem",
    "intervention": "Intervention",
    "comparison": "Comparison",
    "outcome": "Outcome",
    "other": "Other",
    "search_methodology": "Search methodology",
    "evidence_evaluation": "Evidence evaluation",
    "application": "Apply evidence to practice",
    "patient_communication": "Communicate findings to patient",
    "future_research": "Future research ideas",
    # STAT / JCF
    "learner_group": "Learner Group",
    "setting": "Setting",
    "delivery": "Delivery",
    "number_of_learners": "Number of Learners",
    "session_length": "Length of Session",
    "session_title": "Title of Teaching Session",
    "paper_title": "Title of Paper",
    # QIAT
    "qi_pdp": "QI PDP summary",
    "qi_involvement": "QI education involvement",
    "qi_learning": "QI learning and development",
    "qi_project_involved": "Were you involved in a QI project?",
    "qi_reflections": "QI reflections and learning",
    "qi_next_year": "Next year's QI PDP",
    # TEACH
    "date_of_activity": "Date of teaching activity",
    "learning_outcomes": "Learning outcomes used",
    "recognised_course": "Recognised course",
    # PROC_LOG
    "year_of_training": "Year of training",
    "patient_age": "Age of patient",
    "reflective_comments": "Reflective comments on procedure",
    # SDL
    "reflection_title": "Reflection Title",
    "learning_resource": "Learning resource details",
    # US_CASE
    "case_title": "Case reflection title",
    "location": "Location",
    "patient_gender": "Patient's Gender",
    "equipment_used": "Equipment Used",
    "clinical_scenario": "Clinical scenario description",
    "how_us_used": "How ultrasound was used",
    "usable_images": "Were usable images obtained?",
    "interpret_images": "Were images interpretable?",
    "changed_management": "Did ultrasound change management?",
    "what_learned": "What did you learn?",
    "other_comments": "Other comments",
    # ESLE
    "date_of_esle": "Date of ESLE",
    "circumstances": "Describe the circumstances",
    "done_differently": "What would you have done differently?",
    "why": "Why?",
    "different_outcome": "How would the outcome differ?",
    "future_changes": "What to change for the future?",
    "further_learning": "Further learning needs",
    # COMPLAINT
    "date_of_complaint": "Date of complaint",
    "key_features": "Key features of complaint",
    "care_given": "Care given by trainee",
    "learning_points": "Learning points",
    "further_action": "Further action required",
    # SERIOUS_INC
    "date_of_incident": "Date of incident",
    "root_causes": "Root causes of events",
    "contributing_factors": "Contributing factors",
    # EDU_ACT
    "date_of_education": "Date of education",
    "education_title": "Title of education",
    "delivered_by": "Who delivered the education",
    "curriculum_section": "Section of curriculum covered",
    # FORMAL_COURSE
    "project_description": "Project Description",
    "reflective_notes": "Reflective notes",
    "resources_used": "Resources Used",
    "lessons_learned": "Lessons learned",
    # Date variants
    "date": "Date",
    "date_of_case": "Date of case",
    "date_of_completion": "Date of completion",
}


def _field_to_label(key: str) -> str:
    """Convert a field key to a human-readable label for the task prompt."""
    return FIELD_LABELS.get(key, key.replace("_", " ").title())


def _build_task_prompt(
    platform_url: str,
    form_url: Optional[str],
    form_name: str,
    fields: Dict[str, Any],
    curriculum_links: Optional[List[str]] = None,
) -> str:
    """Build the browser-use agent task prompt.

    Credentials are NOT embedded — the agent uses an existing browser profile
    (CDP-connected persistent Chrome) with saved login session.
    """

    # Build field instructions
    field_instructions = []
    for key, value in fields.items():
        if value is None or value == "" or value == []:
            continue
        # Skip internal keys that aren't form fields
        if key in ("curriculum_links", "key_capabilities", "form_type", "uuid"):
            continue
        label = _field_to_label(key)
        if isinstance(value, list):
            value_str = ", ".join(str(v) for v in value)
        else:
            value_str = str(value)
        field_instructions.append(f'- Find the field labelled "{label}" and enter: {value_str}')

    fields_text = "\n".join(field_instructions)

    # Navigation instruction
    if form_url:
        nav_instruction = f"2. Navigate directly to this URL: {form_url}"
    else:
        nav_instruction = f'2. Find and open the form called "{form_name}". Look in menus, dashboards, or form lists.'

    # Curriculum instruction
    curriculum_text = ""
    if curriculum_links:
        slo_list = ", ".join(curriculum_links)
        curriculum_text = f"""

CURRICULUM / KC CHECKBOXES:
After filling the text fields, find the curriculum alignment section.
It may be labelled "Curriculum Links", "2021 EM Curriculum", or similar.
Expand each relevant section and tick the checkboxes for: {slo_list}
If you cannot find the curriculum section, skip this step and note it."""

    return f"""You are filling in a medical e-portfolio form for a trainee doctor using a browser
that already has an active login session. Be precise and methodical.

STEPS:
1. Go to {platform_url}
   - You should already be logged in (session is preserved via CDP)
   - If you see a login page, stop and report "SESSION_EXPIRED"
   - If you see a "shared device" or similar popup, dismiss it
{nav_instruction}
3. Wait for the form to fully load (may take 10-20 seconds on SPA sites)
4. Fill in each field as specified below
5. After filling ALL fields, save as DRAFT

FIELDS TO FILL:
{fields_text}
{curriculum_text}

VERIFICATION:
After filling each field, briefly check the value appears correctly in the form.
If a dropdown doesn't have the exact value, pick the closest match.
If a field cannot be found by its label, look for similar labels nearby.

SAVE AS DRAFT:
- Look for buttons labelled "Save as Draft", "Save Draft", or just "Save"
- Click the save button
- Wait for confirmation that the draft was saved
- Take note of any confirmation message

CRITICAL SAFETY RULES:
- NEVER click Submit, Send, Send to Supervisor, Send to Assessor, or any similar button
- ONLY click Save/Save Draft/Save as Draft
- If you're unsure whether a button submits or saves, DO NOT click it
- If session appears expired (login page shown), stop immediately and report "SESSION_EXPIRED"
- If the form doesn't load, stop and report what you see instead"""


async def file_with_browser_use(
    platform_url: str,
    form_name: str,
    fields: Dict[str, Any],
    credentials: Dict[str, str],
    form_url: Optional[str] = None,
    form_type: str = "unknown",
    curriculum_links: Optional[List[str]] = None,
    model: Optional[str] = None,
    platform: str = "unknown",
    telegram_user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    File a form using browser-use AI agent.

    Runs only in a fresh, throwaway browser seeded with this doctor's own saved
    Kaizen session; it never attaches to the shared CDP Chrome, which may be
    signed in to another doctor's account.

    Args:
        platform_url: Login URL for the e-portfolio (e.g. "https://eportfolio.rcem.ac.uk")
        form_name: Human-readable form name (e.g. "Case-Based Discussion")
        fields: Dict of field_key → value
        credentials: {"username": "...", "password": "..."}
        form_url: Direct URL to the form (if known)
        form_type: Short code for logging (e.g. "CBD")
        curriculum_links: SLO codes to tick
        model: LLM model to use for navigation
        platform: Platform name for logging (e.g. "kaizen", "horus")
        telegram_user_id: Whose saved session to load. Required.

    Returns:
        {
            "status": "success" | "partial" | "failed",
            "filled": [field_keys...],
            "skipped": [field_keys...],
            "error": None | "error message",
            "method": "browser-use",
            "model_used": "<configured model>",
            "selectors_log": "/path/to/log.json" | None,
        }
    """
    require_online()
    _disable_vendor_telemetry()
    from browser_use import Agent, Tools
    from browser_use.browser import BrowserProfile, BrowserSession

    skipped = list(
        k for k, v in fields.items()
        if v is not None and v != "" and v != []
        and k not in ("curriculum_links", "key_capabilities", "form_type", "uuid")
    )

    def _refused(error: str) -> Dict[str, Any]:
        return {
            "status": "failed",
            "filled": [],
            "skipped": skipped,
            "error": error,
            "method": "browser-use",
            "model_used": model,
            "selectors_log": None,
            "discovered_uuids": {},
        }

    hosts = allowed_hosts(platform, platform_url)
    if not hosts:
        return _refused("No allowed hosts for this platform; browser-use refused.")
    for url in (platform_url, form_url):
        if url and not url_is_allowed(url, hosts):
            return _refused("Form address is outside the platform's allowed hosts; browser-use refused.")

    session_state = _load_doctor_session(telegram_user_id, credentials, platform)
    if session_state is None:
        return _refused(
            "No saved session for this doctor; browser-use never borrows the shared browser."
        )
    # Set up selector logging and UUID discovery
    sel_logger = SelectorLogger(platform, form_type)
    step_count = [0]
    discovered_uuids: Dict[str, str] = {}

    # UUID pattern for Angular node IDs (Kaizen uses these as field identifiers)
    _uuid_re = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

    def step_callback(state, output, step_num):
        """Capture each browser-use step for selector logging and UUID extraction."""
        step_count[0] = step_num
        try:
            action_str = str(output) if output else ""
            # Try to extract selector info from the action
            sel_logger.log_step(
                step_num=step_num,
                action_type="browser_step",
                raw_action=action_str[:500],
                success=True,
            )

            # Extract UUIDs from the action — browser-use often interacts
            # with elements whose IDs contain Angular node UUIDs
            uuids_found = _uuid_re.findall(action_str)
            if uuids_found:
                # Try to associate UUID with a field by looking for field labels nearby
                action_lower = action_str.lower()
                for field_key, label in FIELD_LABELS.items():
                    if label.lower() in action_lower or field_key in action_lower:
                        for uid in uuids_found:
                            if uid not in discovered_uuids.values():
                                discovered_uuids[field_key] = uid
                                break
        except Exception:
            pass

    # Build the task prompt — credentials intentionally NOT embedded.
    # The CDP-connected persistent Chrome profile already has a saved
    # Kaizen login session, so the agent just navigates and fills.
    task = _build_task_prompt(
        platform_url=platform_url,
        form_url=form_url,
        form_name=form_name,
        fields=fields,
        curriculum_links=curriculum_links,
    )

    # Create LLM based on model choice
    model = model or gemini_fast_model()
    llm = _create_llm(model)
    fallback_llm = None
    fallback_model = browser_fallback_model()
    if model != fallback_model:
        fallback_llm = _create_llm(fallback_model)

    # Session log directory
    log_dir = BROWSER_USE_LOG_DIR / platform / form_type
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    conversation_path = str(log_dir / f"{timestamp}_conversation.json")

    filled = []

    # Fresh, throwaway browser per filing: its own profile directory, this
    # doctor's cookies only, no CDP attach to the shared Chrome, and DNS
    # blocked for every host off the allowlist. The directory holds the
    # decrypted session, so it is private and always removed.
    _sweep_stale_work_dirs()
    work_dir = tempfile.mkdtemp(prefix=_WORK_DIR_PREFIX)
    browser_session = None
    try:
        os.chmod(work_dir, 0o700)
        state_path = Path(work_dir) / "state.json"
        state_path.touch(mode=0o600)
        state_path.write_text(json.dumps(session_state))
        browser_profile = BrowserProfile(
            headless=True,
            user_data_dir=str(Path(work_dir) / "profile"),
            storage_state=str(state_path),
            allowed_domains=hosts,
            block_ip_addresses=True,
            keep_alive=False,
            accept_downloads=False,
            auto_download_pdfs=False,
            downloads_path=str(Path(work_dir) / "downloads"),
            args=[f"--host-resolver-rules={host_resolver_rule(hosts)}"],
        )
        browser_session = BrowserSession(browser_profile=browser_profile)
        agent = Agent(
            task=task,
            llm=llm,
            fallback_llm=fallback_llm,
            browser_session=browser_session,
            tools=Tools(exclude_actions=list(EXCLUDED_ACTIONS)),
            available_file_paths=[],
            use_vision=True,
            step_timeout=180,
            max_steps=40,
            max_failures=3,
            register_new_step_callback=step_callback,
            # save_conversation_path deliberately omitted — CDP profile means
            # no credentials in the prompt, but conversation logs still contain
            # clinical field data and LLM-traversable page content.
            # Omit GIF recording for the same reason.
        )

        result = await asyncio.wait_for(
            agent.run(),
            timeout=300,  # 5 minutes total
        )

        # Parse result to determine status
        result_text = str(result).lower() if result else ""

        if any(w in result_text for w in [
            "draft saved", "saved as draft", "draft created",
            "successfully saved", "save successful", "saved successfully",
        ]):
            status = "success"
            filled = skipped.copy()
            skipped = []
        elif any(w in result_text for w in ["saved", "draft", "save"]):
            status = "partial"
            # Approximate: assume half filled
            half = len(skipped) // 2
            filled = skipped[:half]
            skipped = skipped[half:]
        elif any(w in result_text for w in [
            "login failed", "authentication", "incorrect password",
            "invalid credentials",
        ]):
            status = "failed"
            filled = []
            return {
                "status": status,
                "filled": filled,
                "skipped": skipped,
                "error": "Login failed — check your credentials",
                "method": "browser-use",
                "model_used": model,
                "selectors_log": sel_logger.save(),
                "discovered_uuids": discovered_uuids,
            }
        elif any(w in result_text for w in [
            "form not found", "could not find", "page not found", "404",
        ]):
            status = "failed"
            filled = []
            return {
                "status": status,
                "filled": filled,
                "skipped": skipped,
                "error": "Could not find the form on this platform",
                "method": "browser-use",
                "model_used": model,
                "selectors_log": sel_logger.save(),
                "discovered_uuids": discovered_uuids,
            }
        else:
            # Ambiguous — check step count
            if step_count[0] > 10:
                status = "partial"
                half = len(skipped) // 2
                filled = skipped[:half]
                skipped = skipped[half:]
            else:
                status = "failed"
                filled = []

        # Save selector log
        log_path = sel_logger.save()

        return {
            "status": status,
            "filled": filled,
            "skipped": skipped,
            "error": None if status in ("success", "partial") else "Filing did not complete successfully",
            "method": "browser-use",
            "model_used": model,
            "selectors_log": log_path,
            "discovered_uuids": discovered_uuids,
        }

    except asyncio.TimeoutError:
        sel_logger.save()
        return {
            "status": "failed",
            "filled": [],
            "skipped": skipped,
            "error": "Browser-use timed out (5 min). The form may be too complex for AI navigation.",
            "method": "browser-use",
            "model_used": model,
            "selectors_log": sel_logger.save(),
            "discovered_uuids": discovered_uuids,
        }
    except Exception as e:
        logger.error(f"Browser-use filer error: {e}", exc_info=True)
        sel_logger.save()
        return {
            "status": "failed",
            "filled": [],
            "skipped": skipped,
            "error": str(e),
            "method": "browser-use",
            "model_used": model,
            "selectors_log": sel_logger.save(),
            "discovered_uuids": discovered_uuids,
        }
    finally:
        if browser_session is not None:
            try:
                await browser_session.kill()
            except Exception:
                pass
        shutil.rmtree(work_dir, ignore_errors=True)


def _load_doctor_session(
    telegram_user_id: Optional[int],
    credentials: Dict[str, str],
    platform: str,
) -> Optional[dict]:
    """This doctor's own saved Kaizen session, or None (refuse the run)."""
    if telegram_user_id is None or (platform or "").lower() != "kaizen":
        return None
    from kaizen_form_filer import load_session_state
    return load_session_state(telegram_user_id, (credentials or {}).get("username"))


def _create_llm(model: str):
    """Create the appropriate LLM instance for browser-use."""
    if model.startswith("gemini"):
        from browser_use.llm.google.chat import ChatGoogle
        return ChatGoogle(
            model=model,
            api_key=os.environ.get("GOOGLE_API_KEY"),
        )
    elif model.startswith("gpt"):
        from browser_use.llm.openai.chat import ChatOpenAI
        return ChatOpenAI(
            model=model,
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
    elif model.startswith("claude"):
        from browser_use.llm.anthropic.chat import ChatAnthropic
        return ChatAnthropic(
            model=model,
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
        )
    else:
        # Default to Gemini
        from browser_use.llm.google.chat import ChatGoogle
        return ChatGoogle(
            model=gemini_fast_model(),
            api_key=os.environ.get("GOOGLE_API_KEY"),
        )
