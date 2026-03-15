"""
browser-use starter for portfolio-guru
Uses Google Gemini (free tier) via GOOGLE_API_KEY from BWS.

Model: gemini-2.0-flash-lite (free tier: 1,500 req/day, 1M tokens/day)
Fallback: gemini-2.0-flash (free tier: 1,500 req/day — richer reasoning)

Usage:
    GOOGLE_API_KEY=<key> python3 browser_use_starter.py

Or from run_local.sh which already fetches GOOGLE_API_KEY from BWS.
"""

import asyncio
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from browser_use import Agent


async def fill_cbd_form(task_description: str) -> str:
    """
    Fill a Kaizen CBD form using browser-use + Gemini.
    
    Args:
        task_description: Natural language description of what to fill in.
                         e.g. "Go to https://kaizenep.com/... and fill the CBD form with:
                               - Clinical setting: ED Resus
                               - Case: 45yo chest pain, STEMI
                               - Outcome discussed: Yes
                               Stop before submitting — take a screenshot and report what you see."
    
    Returns:
        Summary of what was done.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY not set — fetch from BWS first")

    # gemini-2.0-flash-lite: free tier, fast, good enough for form filling
    # Switch to gemini-2.0-flash if it struggles with complex layouts
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-lite",
        google_api_key=api_key,
        temperature=0,
    )

    agent = Agent(
        task=task_description,
        llm=llm,
    )

    result = await agent.run()
    return result


if __name__ == "__main__":
    # Quick smoke test — navigates to Google and reports what it sees
    # Replace with a real Kaizen URL + form instructions for live use
    test_task = (
        "Navigate to https://www.google.com and search for 'Kaizen ePortfolio RCEM'. "
        "Report the first 3 search results. Do not click anything."
    )
    result = asyncio.run(fill_cbd_form(test_task))
    print("Result:", result)
