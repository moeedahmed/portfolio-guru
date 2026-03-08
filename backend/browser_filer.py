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

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from selector_logger import SelectorLogger

logger = logging.getLogger(__name__)

BROWSER_USE_LOG_DIR = Path.home() / ".openclaw/data/portfolio-guru/browser-use-logs"


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
    """Build the browser-use agent task prompt."""

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
        nav_instruction = f"3. Navigate directly to this URL: {form_url}"
    else:
        nav_instruction = f'3. Find and open the form called "{form_name}". Look in menus, dashboards, or form lists.'

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

    return f"""You are filling in a medical e-portfolio form for a trainee doctor. Be precise and methodical.

STEPS:
1. Go to {platform_url}
2. Log in using the credentials provided (username: x_username, password: x_password)
   - Wait for the page to fully load after login (SPAs may take 10-20 seconds)
   - If you see an organisation/institution selector, choose the relevant one
   - If you see a "shared device" popup, dismiss it
{nav_instruction}
4. Wait for the form to fully load (may take 10-20 seconds on SPA sites)
5. Fill in each field as specified below
6. After filling ALL fields, save as DRAFT

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
- If login fails, stop immediately and report the error
- If the form doesn't load, stop and report what you see instead"""


async def file_with_browser_use(
    platform_url: str,
    form_name: str,
    fields: Dict[str, Any],
    credentials: Dict[str, str],
    form_url: Optional[str] = None,
    form_type: str = "unknown",
    curriculum_links: Optional[List[str]] = None,
    model: str = "gemini-3-flash-preview",
    platform: str = "unknown",
) -> Dict[str, Any]:
    """
    File a form using browser-use AI agent.

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

    Returns:
        {
            "status": "success" | "partial" | "failed",
            "filled": [field_keys...],
            "skipped": [field_keys...],
            "error": None | "error message",
            "method": "browser-use",
            "model_used": "gemini-3-flash-preview",
            "selectors_log": "/path/to/log.json" | None,
        }
    """
    from browser_use import Agent
    from browser_use.browser import BrowserProfile, BrowserSession

    # Extract domain for allowed_domains
    parsed = urlparse(platform_url)
    base_domain = parsed.hostname or ""
    # Allow the base domain and common subdomains
    allowed_domains = [f"*{base_domain.split('.')[-2]}.{base_domain.split('.')[-1]}*"]
    if "rcem" in base_domain:
        allowed_domains = ["*rcem*", "*kaizenep*", "*kaizen*"]

    # Set up selector logging
    sel_logger = SelectorLogger(platform, form_type)
    step_count = [0]

    def step_callback(state, output, step_num):
        """Capture each browser-use step for selector logging."""
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
        except Exception:
            pass

    # Build the task prompt
    task = _build_task_prompt(
        platform_url=platform_url,
        form_url=form_url,
        form_name=form_name,
        fields=fields,
        curriculum_links=curriculum_links,
    )

    # Prepare sensitive data with domain locking
    sensitive_data = {
        base_domain: {
            "x_username": credentials["username"],
            "x_password": credentials["password"],
        }
    }

    # Set up browser profile
    browser_profile = BrowserProfile(
        headless=True,
        allowed_domains=allowed_domains,
    )

    # Create LLM based on model choice
    llm = _create_llm(model)
    fallback_llm = None
    if model != "gpt-4o":
        fallback_llm = _create_llm("gpt-4o")

    # Session log directory
    log_dir = BROWSER_USE_LOG_DIR / platform / form_type
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    conversation_path = str(log_dir / f"{timestamp}_conversation.json")

    filled = []
    skipped = list(
        k for k, v in fields.items()
        if v is not None and v != "" and v != []
        and k not in ("curriculum_links", "key_capabilities", "form_type", "uuid")
    )

    try:
        agent = Agent(
            task=task,
            llm=llm,
            fallback_llm=fallback_llm,
            browser_profile=browser_profile,
            sensitive_data=sensitive_data,
            use_vision=True,
            step_timeout=180,
            max_steps=40,
            max_failures=3,
            register_new_step_callback=step_callback,
            save_conversation_path=conversation_path,
            generate_gif=str(log_dir / f"{timestamp}.gif"),
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
        }


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
            model="gemini-3-flash-preview",
            api_key=os.environ.get("GOOGLE_API_KEY"),
        )
