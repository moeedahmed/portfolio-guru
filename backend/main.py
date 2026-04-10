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
    from kaizen_form_filer import fill_kaizen_form

    username = os.environ.get("KAIZEN_USERNAME")
    password = os.environ.get("KAIZEN_PASSWORD")
    if not username or not password:
        raise HTTPException(status_code=500, detail="Kaizen credentials not configured")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    screenshot_path = f"/tmp/portfolio-guru-screenshots/kaizen-{request.form_type.lower()}-{ts}.png"
    os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)

    result = await fill_kaizen_form(
        form_type=request.form_type,
        fields=request.fields,
        username=username,
        password=password,
        draft_uuid=request.draft_uuid,
        save_as_draft=request.save_as_draft,
        screenshot_path=screenshot_path,
    )
    return KaizenFillResponse(**result)
