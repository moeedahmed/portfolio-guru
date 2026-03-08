"""
User profile store — training level and preferences.
Separate from credentials.py (which is frozen).
"""
import os
from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field, Session, create_engine, select

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{os.path.expanduser('~/.openclaw/data/portfolio-guru/portfolio_guru.db')}"
)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


class UserProfile(SQLModel, table=True):
    __tablename__ = "userprofile"
    id: Optional[int] = Field(default=None, primary_key=True)
    telegram_user_id: int = Field(unique=True, index=True)
    training_level: str = Field(default="ST5")   # ST3|ST4|ST5|ST6|SAS
    voice_profile: Optional[str] = Field(default=None)  # JSON style profile from user examples
    voice_examples_count: int = Field(default=0)  # how many examples were used to build profile
    updated_at: datetime = Field(default_factory=datetime.utcnow)


def init_profile_db():
    import pathlib
    db_path = DATABASE_URL.replace("sqlite:///", "")
    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)


def store_training_level(telegram_user_id: int, training_level: str) -> None:
    with Session(engine) as session:
        existing = session.exec(
            select(UserProfile).where(UserProfile.telegram_user_id == telegram_user_id)
        ).first()
        if existing:
            existing.training_level = training_level
            existing.updated_at = datetime.utcnow()
            session.add(existing)
        else:
            session.add(UserProfile(
                telegram_user_id=telegram_user_id,
                training_level=training_level
            ))
        session.commit()


def get_training_level(telegram_user_id: int) -> Optional[str]:
    with Session(engine) as session:
        profile = session.exec(
            select(UserProfile).where(UserProfile.telegram_user_id == telegram_user_id)
        ).first()
        return profile.training_level if profile else None


def store_voice_profile(telegram_user_id: int, profile_json: str, examples_count: int) -> None:
    """Store a generated voice/writing style profile for a user."""
    with Session(engine) as session:
        existing = session.exec(
            select(UserProfile).where(UserProfile.telegram_user_id == telegram_user_id)
        ).first()
        if existing:
            existing.voice_profile = profile_json
            existing.voice_examples_count = examples_count
            existing.updated_at = datetime.utcnow()
            session.add(existing)
        else:
            session.add(UserProfile(
                telegram_user_id=telegram_user_id,
                voice_profile=profile_json,
                voice_examples_count=examples_count,
            ))
        session.commit()


def get_voice_profile(telegram_user_id: int) -> Optional[str]:
    """Get the stored voice/writing style profile JSON. Returns None if not set."""
    with Session(engine) as session:
        profile = session.exec(
            select(UserProfile).where(UserProfile.telegram_user_id == telegram_user_id)
        ).first()
        return profile.voice_profile if profile else None


def clear_voice_profile(telegram_user_id: int) -> None:
    """Remove the user's voice profile."""
    with Session(engine) as session:
        existing = session.exec(
            select(UserProfile).where(UserProfile.telegram_user_id == telegram_user_id)
        ).first()
        if existing:
            existing.voice_profile = None
            existing.voice_examples_count = 0
            existing.updated_at = datetime.utcnow()
            session.add(existing)
            session.commit()
