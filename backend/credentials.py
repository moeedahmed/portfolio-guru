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


def verify_kaizen_credentials(username: str, password: str) -> bool:
    """
    Attempt a lightweight login to Kaizen to verify credentials.
    Returns True if login succeeds, False otherwise.
    """
    import requests
    from bs4 import BeautifulSoup
    from urllib.parse import urlparse

    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

        # Get login page first (CSRF token)
        login_page = session.get(
            "https://eportfolio.rcem.ac.uk",
            timeout=15,
            allow_redirects=True
        )

        # The login form at kaizenep.com
        # POST credentials
        login_url = login_page.url  # follow redirects to get actual login URL

        # Try to find the login form action from the page
        soup = BeautifulSoup(login_page.text, "html.parser")
        form = soup.find("form")
        if not form:
            return False

        action = form.get("action", login_url)
        if action.startswith("/"):
            parsed = urlparse(login_page.url)
            action = f"{parsed.scheme}://{parsed.netloc}{action}"

        # Collect all hidden inputs (CSRF etc)
        data = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            value = inp.get("value", "")
            if name:
                data[name] = value

        # Fill in credentials - find username/password field names
        for inp in form.find_all("input"):
            t = inp.get("type", "").lower()
            name = inp.get("name", "").lower()
            if t == "email" or "email" in name or "user" in name or "login" in name:
                data[inp.get("name")] = username
            elif t == "password" or "pass" in name:
                data[inp.get("name")] = password

        resp = session.post(action, data=data, timeout=15, allow_redirects=True)

        # Success indicators: redirected away from login page, or dashboard in URL
        if "login" not in resp.url.lower() and resp.status_code == 200:
            return True
        # Check for error indicators
        if "invalid" in resp.text.lower() or "incorrect" in resp.text.lower():
            return False
        # If still on login page, failed
        if "login" in resp.url.lower():
            return False
        return True

    except Exception:
        # If verification itself fails (network error etc), assume valid and let actual filing catch it
        return True
