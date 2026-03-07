"""
Credential store for Portfolio Guru.
Stores Kaizen username/password encrypted with Fernet, keyed by telegram_user_id.
"""
import os
from typing import Optional
from datetime import datetime
from cryptography.fernet import Fernet
from sqlmodel import Field, Session, SQLModel, create_engine, select


_DEFAULT_DB = os.path.expanduser("~/.openclaw/data/portfolio-guru/portfolio_guru.db")
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{_DEFAULT_DB}")
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
    import pathlib
    db_path = DATABASE_URL.replace("sqlite:///", "")
    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
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
