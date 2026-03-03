import os
import base64
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from models import FileRequest, FileResponse
from extractor import extract_cbd_data
from filer import file_cbd_to_kaizen

app = FastAPI(title="Portfolio Guru API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "portfolio-guru"}


@app.post("/api/file", response_model=FileResponse)
async def file_entry(request: FileRequest):
    # Step 1: Extract structured data
    try:
        cbd_data = extract_cbd_data(request.case_description)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM error: {str(e)}")

    # Dry run: return extracted data only
    if request.dry_run:
        return FileResponse(
            status="dry_run",
            extracted_data=cbd_data,
        )

    # Step 2: File to Kaizen via browser-use
    try:
        status, action_log, screenshot_b64 = await file_cbd_to_kaizen(cbd_data)
    except Exception as e:
        return FileResponse(status="failed", extracted_data=cbd_data, error=str(e))

    # Step 3: Store screenshot (save locally for MVP, Supabase in V2)
    screenshot_url = None
    if screenshot_b64:
        screenshot_dir = "/tmp/portfolio-guru-screenshots"
        os.makedirs(screenshot_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        screenshot_path = f"{screenshot_dir}/cbd-draft-{timestamp}.png"
        with open(screenshot_path, "wb") as f:
            f.write(base64.b64decode(screenshot_b64))
        screenshot_url = f"file://{screenshot_path}"  # local for MVP

    return FileResponse(
        status=status,
        extracted_data=cbd_data,
        action_log=action_log,
        screenshot_url=screenshot_url,
    )
