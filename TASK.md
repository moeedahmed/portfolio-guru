# TASK.md — Portfolio Guru Phase 2

## Context
Read CLAUDE.md first. Phase 1 built the FastAPI filing engine. Phase 2 adds the Telegram bot
layer and upgrades the extraction/filing engine with real Kaizen field mappings.

## Decisions (locked — do not change)
- Default stage of training: "Higher/ST4-ST6"
- Curriculum Links: always infer best match from case content — never leave blank unless truly impossible
- Credentials: Fernet-encrypted, stored in SQLite keyed by telegram_user_id
- Bot: new standalone bot (token will be in TELEGRAM_BOT_TOKEN env var)
- Hosting target: Railway (Docker)

---

## Task 1 — Upgrade models.py

Replace the existing CBDData model with the upgraded version. Add new models.

```python
from pydantic import BaseModel
from typing import Optional, List, Literal

class CBDData(BaseModel):
    form_type: Literal["CBD"] = "CBD"
    date_of_encounter: str           # YYYY-MM-DD
    patient_age: str                 # e.g. "45-year-old"
    patient_presentation: str        # chief complaint
    clinical_setting: str            # e.g. "Emergency Department - Resus"
    stage_of_training: str           # "Intermediate/ST3" | "Higher/ST4-ST6" | "PEM" | "ACCS"
    trainee_role: str                # what the trainee did
    clinical_reasoning: str          # maps to "Case to be discussed" field
    reflection: str                  # maps to "Reflection of event" field
    level_of_supervision: str        # "Direct" | "Indirect" | "Distant"
    supervisor_name: Optional[str] = None   # name or email
    curriculum_links: List[str] = []        # SLO labels e.g. ["SLO3", "SLO6"]

class FileRequest(BaseModel):
    case_description: str
    telegram_user_id: Optional[int] = None  # if set, fetch creds from credential store
    dry_run: bool = False

class ActionStep(BaseModel):
    step: int
    action: str
    success: bool
    detail: Optional[str] = None

class FileResponse(BaseModel):
    status: str   # "success" | "partial" | "failed" | "dry_run"
    extracted_data: Optional[CBDData] = None
    action_log: List[ActionStep] = []
    screenshot_url: Optional[str] = None
    error: Optional[str] = None
    assessor_warning: Optional[str] = None  # set if assessor lookup failed
```

---

## Task 2 — Upgrade extractor.py

Replace extract_cbd_data() with upgraded version that returns the new CBDData schema.

Key changes:
- Add stage_of_training to extracted fields (default: "Higher/ST4-ST6" if not inferable)
- Rename learning_points → reflection (maps to Kaizen "Reflection of event" field)
- Add curriculum_links extraction

The SLO inference rules (include in system prompt):
```
Curriculum Links — select the most relevant SLOs from this list (pick 1-3):
SLO1: Managing stable patients with undifferentiated presentations
SLO2: Formulating clinical questions and finding answers
SLO3: Resuscitating and stabilising patients
SLO4: Managing patients with injuries
SLO5: Managing children and young people
SLO6: Performing procedural skills
SLO7: Managing complex situations
SLO8: Leading a shift
SLO9: Supervising and educating others
SLO10: Conducting research and managing data
SLO11: Improving quality and patient safety
SLO12: Leading and managing the department

Inference rules:
- Resus/arrest/critical care → SLO3
- Paediatric case → SLO5
- Procedure performed → SLO6
- Trauma/injury → SLO4
- Teaching/supervision → SLO9
- Quality improvement/audit → SLO11
- Management/leadership → SLO8 or SLO12
- Diagnostic uncertainty / stable presentations → SLO1 or SLO2
Return SLO labels only (e.g. ["SLO3", "SLO4"]) — max 3.
```

The updated JSON schema to extract:
```json
{
  "form_type": "CBD",
  "date_of_encounter": "YYYY-MM-DD",
  "patient_age": "...",
  "patient_presentation": "...",
  "clinical_setting": "...",
  "stage_of_training": "Higher/ST4-ST6",
  "trainee_role": "...",
  "clinical_reasoning": "...",
  "reflection": "...",
  "level_of_supervision": "Direct|Indirect|Distant",
  "supervisor_name": null,
  "curriculum_links": ["SLO3"]
}
```

Keep the retry-on-parse-failure logic from Phase 1.

---

## Task 3 — Create credentials.py

New file: `backend/credentials.py`

Fernet-encrypted credential store using SQLite (via SQLModel).

```python
"""
Credential store for Portfolio Guru.
Stores Kaizen username/password encrypted with Fernet, keyed by telegram_user_id.
"""
import os
from typing import Optional
from datetime import datetime
from cryptography.fernet import Fernet
from sqlmodel import Field, Session, SQLModel, create_engine, select


DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./portfolio_guru.db")
FERNET_KEY = os.environ.get("FERNET_SECRET_KEY", "").encode()

engine = create_engine(DATABASE_URL)


class UserCredential(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    telegram_user_id: int = Field(unique=True, index=True)
    kaizen_username_enc: bytes
    kaizen_password_enc: bytes
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


def init_db():
    SQLModel.metadata.create_all(engine)


def _fernet() -> Fernet:
    if not FERNET_KEY:
        raise ValueError("FERNET_SECRET_KEY env var not set")
    return Fernet(FERNET_KEY)


def store_credentials(telegram_user_id: int, username: str, password: str) -> None:
    """Encrypt and store credentials for a user. Upsert."""
    f = _fernet()
    enc_user = f.encrypt(username.encode())
    enc_pass = f.encrypt(password.encode())
    with Session(engine) as session:
        existing = session.exec(
            select(UserCredential).where(UserCredential.telegram_user_id == telegram_user_id)
        ).first()
        if existing:
            existing.kaizen_username_enc = enc_user
            existing.kaizen_password_enc = enc_pass
            existing.updated_at = datetime.utcnow()
            session.add(existing)
        else:
            cred = UserCredential(
                telegram_user_id=telegram_user_id,
                kaizen_username_enc=enc_user,
                kaizen_password_enc=enc_pass,
            )
            session.add(cred)
        session.commit()


def get_credentials(telegram_user_id: int) -> Optional[tuple[str, str]]:
    """Return (username, password) or None if not found."""
    f = _fernet()
    with Session(engine) as session:
        cred = session.exec(
            select(UserCredential).where(UserCredential.telegram_user_id == telegram_user_id)
        ).first()
        if not cred:
            return None
        username = f.decrypt(cred.kaizen_username_enc).decode()
        password = f.decrypt(cred.kaizen_password_enc).decode()
        return username, password


def has_credentials(telegram_user_id: int) -> bool:
    with Session(engine) as session:
        cred = session.exec(
            select(UserCredential).where(UserCredential.telegram_user_id == telegram_user_id)
        ).first()
        return cred is not None
```

---

## Task 4 — Upgrade filer.py

Major upgrade. Key changes:

### 4a — Accept credentials as parameters (not fetched from BWS)
Change signature:
```python
async def file_cbd_to_kaizen(
    cbd_data: CBDData,
    username: str,
    password: str,
) -> tuple[str, List[ActionStep], Optional[str], Optional[str]]:
    # Returns: (status, action_log, screenshot_b64, assessor_warning)
```

Remove the call to get_kaizen_credentials(). Credentials passed in from caller.

### 4b — Direct UUID navigation
The task prompt must use direct URL navigation instead of menu-based navigation:

```
Login URL: https://eportfolio.rcem.ac.uk → wait for kaizenep.com redirect
After login, navigate DIRECTLY to: https://kaizenep.com/events/new-section/3ce5989a-b61c-4c24-ab12-711bf928b181
Do NOT try to find the CBD option in menus. Go directly to that URL.
```

### 4c — Full field mapping in task prompt

Build the task prompt using this exact field mapping:

```
KAIZEN FIELD                    | VALUE TO FILL
--------------------------------|------------------------------------------
"Date occurred on" (date)       | {date_uk}  ← convert YYYY-MM-DD to d/m/yyyy
"Stage of training" (dropdown)  | {cbd_data.stage_of_training}
"Date of event" (date)          | {date_uk}  ← same value, appears twice
"Case to be discussed" (textarea)| {cbd_data.clinical_reasoning}
"Reflection of event" (textarea) | {cbd_data.reflection}
"Curriculum Links" (multi-select)| Click each: {', '.join(cbd_data.curriculum_links)}
"Assessor invite" (type-ahead)  | {cbd_data.supervisor_name or 'SKIP'}
```

Date conversion helper (add to filer.py):
```python
def _to_uk_date(iso_date: str) -> str:
    """Convert YYYY-MM-DD to d/m/yyyy for Kaizen."""
    from datetime import datetime
    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    return f"{dt.day}/{dt.month}/{dt.year}"
```

### 4d — Assessor lookup handling
In the task prompt, instruct the agent:
```
For the "Assessor invite" field:
- If supervisor_name contains '@': type the full email, wait 2s, click the first result
- If supervisor_name is a name: type first 3+ characters, wait 2s, click the closest match
- If no results appear after typing: leave the field blank and note "ASSESSOR_NOT_FOUND"
- If supervisor_name is null/empty: skip this field entirely
```

Return the assessor warning in the response if ASSESSOR_NOT_FOUND appears in the result.

### 4e — Result detection
Check for success more robustly:
```python
result_str = str(result).lower()
if any(w in result_str for w in ["draft saved", "saved as draft", "draft created", "successfully saved"]):
    status = "success"
elif any(w in result_str for w in ["saved", "created", "draft"]):
    status = "partial"
else:
    status = "failed"

assessor_warning = None
if "assessor_not_found" in result_str:
    assessor_warning = f"Assessor '{cbd_data.supervisor_name}' not found in Kaizen — add manually before submitting."
```

---

## Task 5 — Create bot.py

New file: `backend/bot.py`

Telegram bot using python-telegram-bot (v21+, async). Handles:
- /start — welcome + onboarding prompt
- /setup — collect credentials (2-step: username then password)
- /status — show whether credentials are stored
- Any non-command text message → trigger filing pipeline

```python
"""
Portfolio Guru Telegram Bot
Run: python bot.py  (or as part of main FastAPI app via lifespan)
"""
import asyncio
import logging
import os
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler,
)
from credentials import init_db, store_credentials, get_credentials, has_credentials
from extractor import extract_cbd_data
from filer import file_cbd_to_kaizen

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ConversationHandler states
AWAIT_USERNAME, AWAIT_PASSWORD = range(2)

WELCOME_MSG = """👋 Welcome to Portfolio Guru!

I'll file your clinical cases to Kaizen automatically.

First, run /setup to store your Kaizen credentials securely.
Then just send me a text description of any case — I'll handle the rest.

Commands:
/setup — Store your Kaizen credentials
/status — Check if credentials are saved"""

SETUP_START_MSG = """🔐 Let's store your Kaizen credentials.

These are encrypted and stored securely on the server.

What's your Kaizen username (usually your email)?"""

SETUP_PASSWORD_MSG = "Got it. Now send your Kaizen password:"

SETUP_DONE_MSG = """✅ Credentials saved securely.

Now just send me a description of any clinical case and I'll file it to Kaizen as a CBD draft.

Example: "67yo male with STEMI in resus. I was the ST5 on shift, took the call from triage, recognised the STEMI on ECG and activated the cath lab with SpR supervision from Dr Ahmed. Learning point: always get ECG within 10 minutes of arrival.\"
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_MSG)


async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(SETUP_START_MSG)
    return AWAIT_USERNAME


async def setup_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["setup_username"] = update.message.text.strip()
    await update.message.reply_text(SETUP_PASSWORD_MSG)
    return AWAIT_PASSWORD


async def setup_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    username = context.user_data.get("setup_username", "")
    password = update.message.text.strip()
    user_id = update.effective_user.id
    # Delete the password message immediately for security
    try:
        await update.message.delete()
    except Exception:
        pass
    store_credentials(user_id, username, password)
    context.user_data.clear()
    await update.effective_chat.send_message(SETUP_DONE_MSG)
    return ConversationHandler.END


async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Setup cancelled.")
    return ConversationHandler.END


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if has_credentials(user_id):
        await update.message.reply_text("✅ Credentials are stored. Ready to file cases.")
    else:
        await update.message.reply_text("❌ No credentials stored. Run /setup first.")


async def handle_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    # Check credentials
    creds = get_credentials(user_id)
    if not creds:
        await update.message.reply_text(
            "❌ No credentials stored. Run /setup first."
        )
        return

    username, password = creds
    case_text = update.message.text.strip()

    # Acknowledge immediately
    ack = await update.message.reply_text("⏳ Filing your case to Kaizen...")

    try:
        # Step 1: Extract
        cbd_data = extract_cbd_data(case_text)
    except Exception as e:
        await ack.edit_text(
            f"❌ Could not extract case data from your description.\n\n"
            f"Try rephrasing with more detail.\n\nError: {str(e)[:200]}"
        )
        return

    try:
        # Step 2: File
        status_result, action_log, screenshot_b64, assessor_warning = await file_cbd_to_kaizen(
            cbd_data, username, password
        )
    except Exception as e:
        await ack.edit_text(
            f"❌ Filing failed: {str(e)[:300]}\n\n"
            f"Your case description has been received. Reply /retry to try again."
        )
        # Store last CBD data for retry
        context.user_data["last_cbd"] = cbd_data
        return

    # Build reply
    if status_result == "success":
        msg = (
            f"✅ CBD draft saved to Kaizen!\n\n"
            f"📅 Date: {cbd_data.date_of_encounter}\n"
            f"🏥 Case: {cbd_data.patient_presentation[:80]}...\n"
            f"📚 SLOs: {', '.join(cbd_data.curriculum_links) or 'None selected'}\n\n"
            f"Review your draft in Kaizen before submitting."
        )
    elif status_result == "partial":
        msg = (
            f"⚠️ Draft saved but some fields may be incomplete. "
            f"Please review in Kaizen before submitting.\n\n"
            f"📅 Date: {cbd_data.date_of_encounter}"
        )
    else:
        msg = (
            f"❌ Filing failed at the save step. "
            f"Screenshot attached for debugging.\n\n"
            f"Try again or check Kaizen manually."
        )

    if assessor_warning:
        msg += f"\n\n⚠️ {assessor_warning}"

    await ack.edit_text(msg)


def main():
    init_db()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN env var not set")

    app = Application.builder().token(token).build()

    # /setup conversation
    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            AWAIT_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_username)],
            AWAIT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_password)],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(setup_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_case))

    logger.info("Portfolio Guru bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
```

---

## Task 6 — Update main.py

Update the filing endpoint to use credentials from the credential store when telegram_user_id is provided:

```python
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
        import base64
        from datetime import datetime
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
```

Also add startup event to init DB:
```python
from contextlib import asynccontextmanager
from credentials import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="Portfolio Guru API", version="0.2.0", lifespan=lifespan)
```

---

## Task 7 — Update requirements.txt

Replace the existing file with:
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
anthropic>=0.40.0
browser-use>=0.1.40
playwright>=1.49.0
pydantic>=2.0.0
httpx>=0.27.0
python-telegram-bot>=21.0
cryptography>=42.0
sqlmodel>=0.0.18
```

Remove langchain-anthropic (no longer needed — we use browser-use's own ChatAnthropic import).

---

## Task 8 — Update .env.example

```
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
FERNET_SECRET_KEY=...   # generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
DATABASE_URL=sqlite:///./portfolio_guru.db
# Optional (falls back to BWS if not set):
# KAIZEN_USERNAME=your@email.com
# KAIZEN_PASSWORD=yourpassword
```

---

## Verification Steps

Run in order from `backend/` directory with venv activated:

1. Install new deps:
   ```
   pip install python-telegram-bot cryptography sqlmodel
   ```

2. Verify extraction still works with new schema:
   ```
   python test_extraction.py
   ```
   Expected: JSON now includes `stage_of_training`, `reflection`, `curriculum_links` fields.

3. Test credentials store:
   ```python
   python -c "
   import os; os.environ['FERNET_SECRET_KEY'] = 'test-key-32bytes-padded-padding123='
   from cryptography.fernet import Fernet
   key = Fernet.generate_key().decode()
   os.environ['FERNET_SECRET_KEY'] = key
   from credentials import init_db, store_credentials, get_credentials, has_credentials
   init_db()
   store_credentials(12345, 'test@test.com', 'password123')
   print(get_credentials(12345))
   print(has_credentials(12345))
   print('✅ Credential store OK')
   "
   ```

4. Test dry_run API with new schema:
   ```
   uvicorn main:app --port 8001 &
   curl -X POST http://localhost:8001/api/file \
     -H "Content-Type: application/json" \
     -d '{"case_description": "67yo male STEMI in resus. ST5 on shift. Recognised ECG changes, activated cath lab. SpR Dr Ahmed supervising from distance. Learning: ECG interpretation speed matters.", "dry_run": true}'
   ```
   Expected: response includes `stage_of_training`, `curriculum_links`, `reflection`.

5. Verify bot.py imports cleanly:
   ```
   python -c "import bot; print('✅ bot.py imports OK')"
   ```

## Done Criteria
- [ ] models.py has upgraded CBDData with stage_of_training, reflection, curriculum_links
- [ ] extractor.py returns all new fields including curriculum_links SLO inference
- [ ] credentials.py created and working (encrypt/decrypt round-trip verified)
- [ ] filer.py uses direct UUID navigation, full field mapping, date conversion, assessor warning
- [ ] bot.py created with /start, /setup, /status, and free-text case handler
- [ ] main.py updated to use credential store when telegram_user_id provided
- [ ] requirements.txt updated (langchain-anthropic removed, new deps added)
- [ ] dry_run API returns new schema fields correctly
- [ ] All imports clean — no missing modules
