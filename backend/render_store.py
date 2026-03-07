"""
Persistent credential store using Render environment variables.
Credentials are stored as a JSON blob in the CREDENTIALS_JSON env var.
On each write, the Render API is called to update the env var.
"""
import os
import json
import base64
import requests
from cryptography.fernet import Fernet

RENDER_API_KEY = os.environ.get("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.environ.get("RENDER_SERVICE_ID", "")
FERNET_KEY = os.environ.get("FERNET_SECRET_KEY", "")

_fernet = None
def get_fernet():
    global _fernet
    if _fernet is None:
        _fernet = Fernet(FERNET_KEY.encode() if isinstance(FERNET_KEY, str) else FERNET_KEY)
    return _fernet

# In-memory cache loaded at startup
_credentials_cache: dict = {}

def _load_from_env() -> dict:
    raw = os.environ.get("CREDENTIALS_JSON", "{}")
    try:
        return json.loads(raw)
    except Exception:
        return {}

def init_store():
    """Load credentials from env var into memory on startup."""
    global _credentials_cache
    _credentials_cache = _load_from_env()

def _save_to_render(data: dict):
    """Write credentials JSON back to Render env var via API."""
    if not RENDER_API_KEY or not RENDER_SERVICE_ID:
        return  # local dev: skip
    try:
        requests.put(
            f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars",
            headers={"Authorization": f"Bearer {RENDER_API_KEY}", "Content-Type": "application/json"},
            json=[{"key": "CREDENTIALS_JSON", "value": json.dumps(data)}],
            timeout=10
        )
    except Exception:
        pass  # best-effort — credentials still in memory for this session

def store_credentials(telegram_user_id: int, username: str, password: str):
    f = get_fernet()
    enc_user = f.encrypt(username.encode()).decode()
    enc_pass = f.encrypt(password.encode()).decode()
    _credentials_cache[str(telegram_user_id)] = {"u": enc_user, "p": enc_pass}
    _save_to_render(_credentials_cache)

def get_credentials(telegram_user_id: int):
    entry = _credentials_cache.get(str(telegram_user_id))
    if not entry:
        return None
    f = get_fernet()
    try:
        username = f.decrypt(entry["u"].encode()).decode()
        password = f.decrypt(entry["p"].encode()).decode()
        return username, password
    except Exception:
        return None

def has_credentials(telegram_user_id: int) -> bool:
    return str(telegram_user_id) in _credentials_cache
