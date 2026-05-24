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
    training_level: str = Field(default="UNKNOWN")   # UNKNOWN|ACCS|INTERMEDIATE|HIGHER|SAS, or legacy ST3/ST4/ST5/ST6
    curriculum: Optional[str] = Field(default=None)  # "2025" or "2021"; None treated as "2025"
    voice_profile: Optional[str] = Field(default=None)  # JSON style profile from user examples
    voice_examples_count: int = Field(default=0)  # how many examples were used to build profile
    # Detected Kaizen account role — "assessor" (pure Clinical Supervisor),
    # "trainee" (own portfolio), or "unknown" (detection failed / no probe yet).
    # Separate from training_level because an assessor has no personal portfolio
    # and the current bot maps that to HIGHER as a default. Read-only here;
    # mutated only through the demotion-safe `store_kaizen_role` helper.
    kaizen_role: Optional[str] = Field(default=None)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


def init_profile_db():
    import pathlib
    db_path = DATABASE_URL.replace("sqlite:///", "")
    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    # Migrate: add columns that create_all won't alter on existing tables.
    _migrate_add_column("curriculum", "TEXT")
    _migrate_add_column("kaizen_role", "TEXT")


def _migrate_add_column(column_name: str, column_type: str) -> None:
    """Add a column to userprofile if it doesn't exist (SQLite migration)."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(userprofile)")
        columns = {row[1] for row in cursor.fetchall()}
        if column_name not in columns:
            conn.execute(f"ALTER TABLE userprofile ADD COLUMN {column_name} {column_type}")
            conn.commit()
        conn.close()
    except Exception:
        pass  # table may not exist yet — create_all will handle it


# Auto-apply additive UserProfile migrations on import.
# Newer columns added to the SQLModel above show up in every default SELECT,
# so a legacy on-disk DB missing the column raises OperationalError at the
# first read — including from code paths (e.g. `get_voice_profile`) that
# don't logically depend on the new column. The migration is idempotent and
# additive (no data loss); running it once per process load is cheap.
# init_profile_db() also calls these for the startup path; the duplicate is
# intentional and safe.
def _autoapply_userprofile_migrations() -> None:
    try:
        _migrate_add_column("curriculum", "TEXT")
        _migrate_add_column("kaizen_role", "TEXT")
    except Exception:
        pass


_autoapply_userprofile_migrations()


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

    try:
        from supabase_sync import mirror_profile
        mirror_profile(telegram_user_id, training_level=training_level)
    except Exception:
        pass


def get_training_level(telegram_user_id: int) -> Optional[str]:
    with Session(engine) as session:
        profile = session.exec(
            select(UserProfile).where(UserProfile.telegram_user_id == telegram_user_id)
        ).first()
        
        if not profile or profile.training_level == "UNKNOWN":
            return None
        return profile.training_level


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

    try:
        from supabase_sync import mirror_profile
        mirror_profile(
            telegram_user_id,
            voice_profile_json=profile_json,
            voice_examples_count=examples_count,
        )
    except Exception:
        pass


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

    try:
        from supabase_sync import mirror_profile
        mirror_profile(telegram_user_id, voice_profile_json={}, voice_examples_count=0)
    except Exception:
        pass


def store_curriculum(telegram_user_id: int, curriculum: str) -> None:
    """Store curriculum preference ("2025" or "2021")."""
    with Session(engine) as session:
        existing = session.exec(
            select(UserProfile).where(UserProfile.telegram_user_id == telegram_user_id)
        ).first()
        if existing:
            existing.curriculum = curriculum
            existing.updated_at = datetime.utcnow()
            session.add(existing)
        else:
            session.add(UserProfile(
                telegram_user_id=telegram_user_id,
                curriculum=curriculum
            ))
        session.commit()

    try:
        from supabase_sync import mirror_profile
        mirror_profile(telegram_user_id, curriculum=curriculum)
    except Exception:
        pass


def get_curriculum(telegram_user_id: int) -> str:
    """Get curriculum preference. Returns "2025" if not set."""
    with Session(engine) as session:
        profile = session.exec(
            select(UserProfile).where(UserProfile.telegram_user_id == telegram_user_id)
        ).first()
        return profile.curriculum if profile and profile.curriculum else "2025"


def _select_profile(session, telegram_user_id: int) -> Optional[UserProfile]:
    """Return the UserProfile row for ``telegram_user_id`` or ``None``."""
    stmt = select(UserProfile).where(UserProfile.telegram_user_id == telegram_user_id)
    rows = session.scalars(stmt)
    return rows.first()


def store_kaizen_role(telegram_user_id: int, role: Optional[str]) -> None:
    """Persist the user's Kaizen account role (`assessor` / `trainee` / `unknown`).

    Stored verbatim — the demotion-safe semantics live in
    ``supervisor_workflow.set_role_if_better``. Callers that want
    "don't downgrade a known-good role" must go through that helper.
    """
    with Session(engine) as session:
        existing = _select_profile(session, telegram_user_id)
        if existing:
            existing.kaizen_role = role
            existing.updated_at = datetime.utcnow()
            session.add(existing)
        else:
            session.add(UserProfile(
                telegram_user_id=telegram_user_id,
                kaizen_role=role,
            ))
        session.commit()


def get_kaizen_role(telegram_user_id: int) -> Optional[str]:
    """Return the cached Kaizen role for a user or ``None`` if never set."""
    with Session(engine) as session:
        profile = _select_profile(session, telegram_user_id)
        return profile.kaizen_role if profile else None


def list_users_by_kaizen_role(role: str) -> list[int]:
    """Return Telegram user IDs whose cached Kaizen role matches ``role``.

    Used by the supervisor scheduler to find assessor accounts without
    iterating every profile row. Users whose ``kaizen_role`` is ``NULL``
    are excluded.
    """
    with Session(engine) as session:
        stmt = select(UserProfile).where(UserProfile.kaizen_role == role)
        rows = session.scalars(stmt).all()
        return [row.telegram_user_id for row in rows]
