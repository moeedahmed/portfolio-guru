"""
Legacy browser-use CBD filer — DEPRECATED.

This module pre-dates filer_router.py. It embeds credentials directly in the
LLM prompt, which violates the filing routing discipline described in
AGENTS.md (browser-use must use a CDP-connected Chrome profile with the saved
session, never inject credentials into LLM prompts).

The live Telegram bot routes all filings through bot.handle_approval_approve
→ filer_router.route_filing. The only remaining caller of file_cbd_to_kaizen
is the legacy FastAPI /api/file route in main.py, which is not started by the
launchd service (start-bot.sh → run_local.sh → python bot.py).

Calls to file_cbd_to_kaizen now raise NotImplementedError unless the explicit
opt-in env var PORTFOLIO_GURU_ALLOW_LEGACY_FILER=1 is set. Operators who need
the legacy path for a one-off must set the env var, accept the credential-in-
prompt risk, and not commit that change.
"""

import asyncio
import os
import base64
from datetime import datetime
from typing import List, Optional
from models import CBDData, ActionStep
from model_config import gemini_fast_model


def _to_uk_date(iso_date: str) -> str:
    """Convert YYYY-MM-DD to d/m/yyyy for Kaizen."""
    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    return f"{dt.day}/{dt.month}/{dt.year}"


async def file_cbd_to_kaizen(
    cbd_data: CBDData,
    username: str,
    password: str,
) -> tuple[str, List[ActionStep], Optional[str], Optional[str]]:
    """
    DEPRECATED. File a CBD entry to Kaizen using browser-use.

    Use filer_router.route_filing instead. This entrypoint embeds credentials
    in the LLM prompt and is retained only for the legacy /api/file FastAPI
    route. Set PORTFOLIO_GURU_ALLOW_LEGACY_FILER=1 to unlock it.

    Returns: (status, action_log, screenshot_base64, assessor_warning)
    status: "success" | "partial" | "failed"
    """
    if os.environ.get("PORTFOLIO_GURU_ALLOW_LEGACY_FILER") != "1":
        raise NotImplementedError(
            "filer.file_cbd_to_kaizen is deprecated. Route filings through "
            "filer_router.route_filing. Set PORTFOLIO_GURU_ALLOW_LEGACY_FILER=1 "
            "to opt in to the legacy credentials-in-prompt path."
        )
    from browser_use import Agent
    from browser_use.browser import BrowserProfile, BrowserSession
    from browser_use.llm.google.chat import ChatGoogle

    action_log: List[ActionStep] = []
    screenshot_b64: Optional[str] = None
    assessor_warning: Optional[str] = None

    # Convert date to UK format
    date_uk = _to_uk_date(cbd_data.date_of_encounter)

    # Build curriculum links instruction
    curriculum_instruction = ""
    if cbd_data.curriculum_links:
        curriculum_instruction = f"""
For "Curriculum Links" (multi-select): Click to open the dropdown, then click each of these items:
{', '.join(cbd_data.curriculum_links)}
"""

    # Build assessor instruction
    if cbd_data.supervisor_name:
        if "@" in cbd_data.supervisor_name:
            assessor_instruction = f"""
For the "Assessor invite" field:
- Type the full email: {cbd_data.supervisor_name}
- Wait 2 seconds for results to appear
- Click the first result that appears
- If no results appear after typing, leave the field blank and note "ASSESSOR_NOT_FOUND"
"""
        else:
            assessor_instruction = f"""
For the "Assessor invite" field:
- Type the first 3+ characters of: {cbd_data.supervisor_name}
- Wait 2 seconds for results to appear
- Click the closest match
- If no results appear after typing, leave the field blank and note "ASSESSOR_NOT_FOUND"
"""
    else:
        assessor_instruction = "Skip the Assessor invite field entirely."

    task = f"""
Complete the following steps on the Kaizen ePortfolio website:

1. Go to https://eportfolio.rcem.ac.uk and log in with:
   - Username: {username}
   - Password: {password}

IMPORTANT: The site will redirect to kaizenep.com. Wait for the login form to fully load (you'll see "Username" and "Password" fields). The page may appear blank for a few seconds while the SPA loads.

2. After logging in, wait for the dashboard to fully load. If you see a "This is a shared device" popup, click it to dismiss.

3. Navigate DIRECTLY to this URL: https://kaizenep.com/events/new-section/3ce5989a-b61c-4c24-ab12-711bf928b181

Do NOT try to find the CBD option in menus. Go directly to that URL.

4. Wait for the CBD form to fully load (this may take 10-20 seconds).

5. Fill in the CBD form with these EXACT field mappings:

   KAIZEN FIELD                     | VALUE TO FILL
   ---------------------------------|------------------------------------------
   "Date occurred on" (date picker) | {date_uk}
   "Stage of training" (dropdown)   | {cbd_data.stage_of_training}
   "Date of event" (date picker)    | {date_uk}
   "Case to be discussed" (textarea)| {cbd_data.clinical_reasoning}
   "Reflection of event" (textarea) | {cbd_data.reflection}

{curriculum_instruction}

{assessor_instruction}

6. IMPORTANT: Save as DRAFT only. Do NOT submit. Do NOT send to supervisor.
   Look for a "Save as Draft", "Save Draft", or "Save" button (not "Submit" or "Send").

7. After saving, take a screenshot of the saved draft confirmation page.

CRITICAL RULES:
- Never click Submit or Send to Supervisor
- If you cannot find the CBD form, report exactly what you see
- If a field doesn't exist exactly as described, find the closest matching field
- If you see a CAPTCHA or MFA challenge, stop and report it
- Wait 5-10 seconds after each page load before proceeding
- If the page appears blank, wait longer (the SPA is loading)
"""

    browser_profile = BrowserProfile(headless=True)
    browser_session = BrowserSession(browser_profile=browser_profile)

    llm = ChatGoogle(
        model=gemini_fast_model(),
        api_key=os.environ.get("GOOGLE_API_KEY"),
    )

    agent = Agent(
        task=task,
        llm=llm,
        browser_session=browser_session,
        step_timeout=120,  # 2 minutes per step (Kaizen is slow)
        max_steps=30,
    )

    try:
        result = await agent.run()

        # Extract action history from result
        if hasattr(result, "history"):
            for i, step in enumerate(result.history):
                action_log.append(
                    ActionStep(
                        step=i + 1,
                        action=(
                            str(step.model_output)
                            if hasattr(step, "model_output")
                            else str(step)
                        ),
                        success=True,
                    )
                )

        # Take final screenshot via browser session
        try:
            page = await browser_session.get_current_page()
            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
        except Exception:
            pass

        # Check if draft was saved
        result_str = str(result).lower()
        if any(w in result_str for w in ["draft saved", "saved as draft", "draft created", "successfully saved"]):
            status = "success"
        elif any(w in result_str for w in ["saved", "created", "draft"]):
            status = "partial"
        else:
            status = "failed"

        # Check for assessor warning
        if "assessor_not_found" in result_str:
            assessor_warning = f"Assessor '{cbd_data.supervisor_name}' not found in Kaizen — add manually before submitting."

    except Exception as e:
        action_log.append(
            ActionStep(
                step=len(action_log) + 1,
                action="agent_error",
                success=False,
                detail=str(e),
            )
        )
        status = "failed"
        # Still try to get screenshot
        try:
            page = await browser_session.get_current_page()
            screenshot_bytes = await page.screenshot()
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
        except Exception:
            pass
    finally:
        try:
            await browser_session.close()
        except Exception:
            pass

    return status, action_log, screenshot_b64, assessor_warning
