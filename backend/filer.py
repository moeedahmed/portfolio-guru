import asyncio
import os
import base64
from datetime import datetime
from typing import List, Optional
from models import CBDData, ActionStep
from config import get_kaizen_credentials


async def file_cbd_to_kaizen(
    cbd_data: CBDData,
) -> tuple[str, List[ActionStep], Optional[str]]:
    """
    File a CBD entry to Kaizen using browser-use.

    Returns: (status, action_log, screenshot_base64)
    status: "success" | "partial" | "failed"
    """
    from browser_use import Agent, Browser, BrowserConfig
    from langchain_anthropic import ChatAnthropic

    username, password = get_kaizen_credentials()
    action_log: List[ActionStep] = []
    screenshot_b64: Optional[str] = None

    # Build the task description for the browser-use agent
    task = f"""
    Complete the following steps on the Kaizen ePortfolio website (https://eportfolio.rcem.ac.uk):

    1. Go to https://eportfolio.rcem.ac.uk and log in with:
       - Username: {username}
       - Password: {password}

    2. Navigate to create a new Case-Based Discussion (CBD) assessment/entry.
       Look for "New Entry", "Add Entry", "CBD", or similar in the navigation.

    3. Fill in the CBD form with these values:
       - Patient age / details: {cbd_data.patient_age}
       - Presenting complaint: {cbd_data.patient_presentation}
       - Clinical setting: {cbd_data.clinical_setting}
       - Your role: {cbd_data.trainee_role}
       - Clinical reasoning / discussion: {cbd_data.clinical_reasoning}
       - Learning points: {cbd_data.learning_points}
       - Level of supervision: {cbd_data.level_of_supervision}
       - Date of encounter: {cbd_data.date_of_encounter}
       {f"- Supervisor name: {cbd_data.supervisor_name}" if cbd_data.supervisor_name else "- Leave supervisor field blank or as 'TBC'"}

    4. IMPORTANT: Save as DRAFT only. Do NOT submit. Do NOT send to supervisor.
       Look for a "Save as Draft", "Save Draft", or "Save" button (not "Submit" or "Send").

    5. After saving, take a screenshot of the saved draft confirmation page.

    CRITICAL RULES:
    - Never click Submit or Send to Supervisor
    - If you cannot find the CBD form, report exactly what you see on the navigation menu
    - If a field doesn't exist exactly as described, find the closest matching field
    - If you see a CAPTCHA or MFA challenge, stop and report it
    """

    browser_config = BrowserConfig(headless=True)
    browser = Browser(config=browser_config)

    llm = ChatAnthropic(
        model="claude-opus-4-5",  # Use Opus for reliable browser navigation
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
    )

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
    )

    try:
        result = await agent.run(max_steps=30)

        # Extract action history from result
        # browser-use result contains action history
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

        # Take final screenshot
        try:
            page = await browser.get_current_page()
            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
        except Exception:
            pass

        # Check if draft was saved (look for success indicators in result)
        result_str = str(result).lower()
        if any(word in result_str for word in ["draft", "saved", "success", "created"]):
            status = "success"
        else:
            status = "partial"

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
            page = await browser.get_current_page()
            screenshot_bytes = await page.screenshot()
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
        except Exception:
            pass
    finally:
        await browser.close()

    return status, action_log, screenshot_b64
