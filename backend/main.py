"""
Legacy FastAPI app for Portfolio Guru.

The live bot runs in polling mode via launchd → start-bot.sh → run_local.sh
→ python bot.py. This module is retained for the Docker/supervisord deploy
path (backend/Dockerfile, backend/supervisord.conf) but the /api/file route
calls the deprecated filer.file_cbd_to_kaizen and will raise unless the
PORTFOLIO_GURU_ALLOW_LEGACY_FILER opt-in is set. Prefer filer_router for any
new programmatic entrypoint — see AGENTS.md § Filing Routing Discipline.
"""

import os
import base64
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from telegram import Update
from models import FileRequest, FileResponse, KaizenFillRequest, KaizenFillResponse
from extractor import extract_cbd_data
from filer import file_cbd_to_kaizen
from store import init
from bot import build_application


@asynccontextmanager
async def lifespan(app: FastAPI):
    init()
    bot_application = build_application()
    await bot_application.initialize()
    app.state.bot_application = bot_application
    yield
    await bot_application.shutdown()


app = FastAPI(title="Portfolio Guru API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "portfolio-guru"}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, app.state.bot_application.bot)
    await app.state.bot_application.process_update(update)
    return {"ok": True}


@app.post("/api/file", response_model=FileResponse)
async def file_entry(request: FileRequest):
    # Step 1: Extract
    try:
        cbd_data = extract_cbd_data(request.case_description)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM error: {str(e)}")

    if request.dry_run:
        return FileResponse(status="dry_run", extracted_data=cbd_data)

    # Step 2: Get credentials
    if request.telegram_user_id:
        from credentials import get_credentials
        creds = get_credentials(request.telegram_user_id)
        if not creds:
            raise HTTPException(status_code=401, detail="No credentials stored for this user. Run /setup.")
        username, password = creds
    else:
        # Fall back to env var / BWS credentials (for local testing)
        from config import get_kaizen_credentials
        username, password = get_kaizen_credentials()

    # Step 3: File
    try:
        status_val, action_log, screenshot_b64, assessor_warning = await file_cbd_to_kaizen(
            cbd_data, username, password
        )
    except Exception as e:
        return FileResponse(status="failed", extracted_data=cbd_data, error=str(e))

    # Step 4: Save screenshot
    screenshot_url = None
    if screenshot_b64:
        screenshot_dir = "/tmp/portfolio-guru-screenshots"
        os.makedirs(screenshot_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = f"{screenshot_dir}/cbd-draft-{ts}.png"
        with open(path, "wb") as f:
            f.write(base64.b64decode(screenshot_b64))
        screenshot_url = f"file://{path}"

    return FileResponse(
        status=status_val,
        extracted_data=cbd_data,
        action_log=action_log,
        screenshot_url=screenshot_url,
        assessor_warning=assessor_warning,
    )


@app.post("/api/kaizen/file", response_model=KaizenFillResponse)
async def kaizen_file(request: KaizenFillRequest):
    """Programmatic Kaizen filing entrypoint — draft-only, routed via filer_router.

    All filing flows through filer_router.route_filing (single source of truth
    for routing — see AGENTS.md § Filing Routing Discipline). submit=False is
    hard-coded; the KaizenFillRequest model already forbids save_as_draft=False
    at the API boundary. draft_uuid on the request body is ignored — drafts are
    a bot-side concept; programmatic callers create new drafts.
    """
    from filer_router import route_filing

    username = os.environ.get("KAIZEN_USERNAME")
    password = os.environ.get("KAIZEN_PASSWORD")
    if not username or not password:
        raise HTTPException(status_code=500, detail="Kaizen credentials not configured")

    result = await route_filing(
        platform="kaizen",
        form_type=request.form_type,
        fields=request.fields,
        credentials={"username": username, "password": password},
        submit=False,
    )

    errors: list[str] = []
    if result.get("error"):
        errors.append(result["error"])
    errors.extend(result.get("errors", []))

    return KaizenFillResponse(
        status=result.get("status", "failed"),
        filled=result.get("filled", []),
        skipped=result.get("skipped", []),
        errors=errors,
        screenshot_path=result.get("screenshot") or result.get("screenshot_path"),
    )
