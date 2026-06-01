"""Kaizen Portfolio Index v1 — read-only evidence storage.

Stores normalised evidence read from the user's Kaizen portfolio plus a
per-run audit row. Read-only by design: nothing in this module writes back
to Kaizen, navigates a browser, or reads credentials. The implementing
sprint slice
(``docs/roadmap/kaizen-mapping-sprint-2026-06.md`` -> "First build slice")
will drive the read-only sync against an already-authenticated CDP session
and call the helpers below.

The tables live in the same SQLite database used by ``usage.py``
(``USAGE_DB_PATH`` / ``usage.db``) so ``/settings`` can surface sync status
alongside tier/usage without a second database path.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Iterable, Literal, Optional

import aiosqlite

from health_models import EvidenceItem, HealthDomain

_DEFAULT_DB = os.path.expanduser("~/.openclaw/data/portfolio-guru/usage.db")
DB_PATH = os.environ.get("USAGE_DB_PATH", _DEFAULT_DB)


EvidenceSurface = Literal["event", "event_section", "draft", "file"]
IndexRunStatus = Literal[
    "running",
    "ok",
    "partial",
    "drift",
    "auth_required",
    "failed",
]


@dataclass
class EvidenceItemRow:
    """One indexed Kaizen evidence item (event, section, draft, or file)."""

    id: str
    user_id: str
    surface: str
    event_type: Optional[str] = None
    category: Optional[str] = None
    state: Optional[str] = None
    date_occurred_on: Optional[str] = None
    end_date: Optional[str] = None
    description: Optional[str] = None
    linked_kc_tags: list[str] = field(default_factory=list)
    filled_in_by: Optional[str] = None
    filled_in_on: Optional[str] = None
    parent_event_id: Optional[str] = None
    detail_url: Optional[str] = None
    last_seen_at: Optional[str] = None
    first_seen_at: Optional[str] = None


@dataclass
class IndexRunRow:
    """One read-only sync run audit row."""

    id: int
    user_id: str
    started_at: str
    finished_at: Optional[str] = None
    status: str = "running"
    rows_seen: int = 0
    rows_written: int = 0
    rows_drifted: int = 0
    notes: Optional[str] = None


@dataclass
class KaizenSyncStatus:
    """Snapshot of the latest sync run, for the /settings status row."""

    last_run: Optional[IndexRunRow]
    items_indexed: int

    @property
    def is_unset(self) -> bool:
        return self.last_run is None


_EVIDENCE_COLUMNS = (
    "id, user_id, surface, event_type, category, state, "
    "date_occurred_on, end_date, description, linked_kc_tags, "
    "filled_in_by, filled_in_on, parent_event_id, detail_url, "
    "last_seen_at, first_seen_at"
)


def _current_db_path() -> str:
    """Resolve the active SQLite path at call time so tests can monkeypatch it."""
    return os.environ.get("USAGE_DB_PATH", _DEFAULT_DB)


async def _ensure_db() -> None:
    path = _current_db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence_items (
                id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                surface TEXT NOT NULL,
                event_type TEXT,
                category TEXT,
                state TEXT,
                date_occurred_on TEXT,
                end_date TEXT,
                description TEXT,
                linked_kc_tags TEXT,
                filled_in_by TEXT,
                filled_in_on TEXT,
                parent_event_id TEXT,
                detail_url TEXT,
                last_seen_at TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                PRIMARY KEY (user_id, id)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_evidence_user_surface "
            "ON evidence_items(user_id, surface)"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS index_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                rows_seen INTEGER DEFAULT 0,
                rows_written INTEGER DEFAULT 0,
                rows_drifted INTEGER DEFAULT 0,
                notes TEXT
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_index_runs_user "
            "ON index_runs(user_id, started_at DESC)"
        )
        await db.commit()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ── Evidence storage ─────────────────────────────────────────────────────────


async def upsert_evidence_item(item: EvidenceItemRow) -> None:
    """Insert or update an evidence row keyed on ``(user_id, id)``.

    ``first_seen_at`` is preserved across upserts; ``last_seen_at`` advances
    to the current timestamp so Portfolio Health can reason about ageing
    without hard-deleting rows that briefly fall off a sync.
    """
    await _ensure_db()
    now = _now_iso()
    first_seen = item.first_seen_at or now
    last_seen = item.last_seen_at or now
    kc_json = json.dumps(item.linked_kc_tags or [])
    async with aiosqlite.connect(_current_db_path()) as db:
        await db.execute(
            f"""
            INSERT INTO evidence_items ({_EVIDENCE_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, id) DO UPDATE SET
                surface = excluded.surface,
                event_type = excluded.event_type,
                category = excluded.category,
                state = excluded.state,
                date_occurred_on = excluded.date_occurred_on,
                end_date = excluded.end_date,
                description = excluded.description,
                linked_kc_tags = excluded.linked_kc_tags,
                filled_in_by = excluded.filled_in_by,
                filled_in_on = excluded.filled_in_on,
                parent_event_id = excluded.parent_event_id,
                detail_url = excluded.detail_url,
                last_seen_at = excluded.last_seen_at
            """,
            (
                item.id,
                item.user_id,
                item.surface,
                item.event_type,
                item.category,
                item.state,
                item.date_occurred_on,
                item.end_date,
                item.description,
                kc_json,
                item.filled_in_by,
                item.filled_in_on,
                item.parent_event_id,
                item.detail_url,
                last_seen,
                first_seen,
            ),
        )
        await db.commit()


def _row_to_evidence_item(row) -> EvidenceItemRow:
    try:
        tags = json.loads(row["linked_kc_tags"]) if row["linked_kc_tags"] else []
        if not isinstance(tags, list):
            tags = []
    except (ValueError, TypeError):
        tags = []
    return EvidenceItemRow(
        id=row["id"],
        user_id=row["user_id"],
        surface=row["surface"],
        event_type=row["event_type"],
        category=row["category"],
        state=row["state"],
        date_occurred_on=row["date_occurred_on"],
        end_date=row["end_date"],
        description=row["description"],
        linked_kc_tags=tags,
        filled_in_by=row["filled_in_by"],
        filled_in_on=row["filled_in_on"],
        parent_event_id=row["parent_event_id"],
        detail_url=row["detail_url"],
        last_seen_at=row["last_seen_at"],
        first_seen_at=row["first_seen_at"],
    )


async def list_evidence_items(user_id: str | int) -> list[EvidenceItemRow]:
    await _ensure_db()
    async with aiosqlite.connect(_current_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT {_EVIDENCE_COLUMNS} FROM evidence_items "
            "WHERE user_id = ? "
            "ORDER BY COALESCE(date_occurred_on, '') DESC, first_seen_at DESC",
            (str(user_id),),
        ) as cursor:
            rows = await cursor.fetchall()
            return [_row_to_evidence_item(row) for row in rows]


async def count_evidence_items(user_id: str | int) -> int:
    await _ensure_db()
    async with aiosqlite.connect(_current_db_path()) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM evidence_items WHERE user_id = ?",
            (str(user_id),),
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0


# ── Index run lifecycle ─────────────────────────────────────────────────────


async def start_index_run(user_id: str | int) -> int:
    await _ensure_db()
    async with aiosqlite.connect(_current_db_path()) as db:
        cursor = await db.execute(
            "INSERT INTO index_runs (user_id, started_at, status) VALUES (?, ?, ?)",
            (str(user_id), _now_iso(), "running"),
        )
        await db.commit()
        run_id = cursor.lastrowid
    if run_id is None:
        raise RuntimeError("Failed to create index_runs row")
    return int(run_id)


async def finish_index_run(
    run_id: int,
    status: IndexRunStatus,
    *,
    rows_seen: int = 0,
    rows_written: int = 0,
    rows_drifted: int = 0,
    notes: Optional[str] = None,
) -> None:
    await _ensure_db()
    async with aiosqlite.connect(_current_db_path()) as db:
        await db.execute(
            """
            UPDATE index_runs
               SET finished_at = ?,
                   status = ?,
                   rows_seen = ?,
                   rows_written = ?,
                   rows_drifted = ?,
                   notes = ?
             WHERE id = ?
            """,
            (_now_iso(), status, rows_seen, rows_written, rows_drifted, notes, run_id),
        )
        await db.commit()


def _row_to_index_run(row) -> IndexRunRow:
    return IndexRunRow(
        id=row["id"],
        user_id=row["user_id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        rows_seen=row["rows_seen"] or 0,
        rows_written=row["rows_written"] or 0,
        rows_drifted=row["rows_drifted"] or 0,
        notes=row["notes"],
    )


async def latest_index_run(user_id: str | int) -> Optional[IndexRunRow]:
    await _ensure_db()
    async with aiosqlite.connect(_current_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, user_id, started_at, finished_at, status,
                   rows_seen, rows_written, rows_drifted, notes
              FROM index_runs
             WHERE user_id = ?
          ORDER BY started_at DESC, id DESC
             LIMIT 1
            """,
            (str(user_id),),
        ) as cursor:
            row = await cursor.fetchone()
            return _row_to_index_run(row) if row else None


async def get_kaizen_sync_status(user_id: str | int) -> KaizenSyncStatus:
    """Return a read-only sync snapshot for the /settings status row."""
    run = await latest_index_run(user_id)
    count = await count_evidence_items(user_id)
    return KaizenSyncStatus(last_run=run, items_indexed=count)


# ── Pure conversion to health_models.EvidenceItem ───────────────────────────


_FORM_TYPE_DOMAIN_LOOKUP: dict[str, tuple[HealthDomain, str]] = {
    "CBD": (HealthDomain.clinical, "wpba"),
    "CASE_BASED_DISCUSSION": (HealthDomain.clinical, "wpba"),
    "DOPS": (HealthDomain.clinical, "wpba"),
    "MINI_CEX": (HealthDomain.clinical, "wpba"),
    "ACAT": (HealthDomain.clinical, "wpba"),
    "ACAF": (HealthDomain.clinical, "wpba"),
    "LAT": (HealthDomain.clinical, "wpba"),
    "STAT": (HealthDomain.clinical, "wpba"),
    "PROC_LOG": (HealthDomain.clinical, "wpba"),
    "PROCEDURAL_LOG": (HealthDomain.clinical, "wpba"),
    "US_CASE": (HealthDomain.clinical, "wpba"),
    "ESLE": (HealthDomain.clinical, "wpba"),
    "ESLE_ASSESS": (HealthDomain.clinical, "wpba"),
    "ESLE_PART1_2": (HealthDomain.clinical, "wpba"),
    "MSF": (HealthDomain.clinical, "wpba"),
    "QIAT": (HealthDomain.qi, "audit"),
    "AUDIT": (HealthDomain.qi, "audit"),
    "TEACH": (HealthDomain.teaching, "teaching_session"),
    "TEACH_OBS": (HealthDomain.teaching, "teaching_session"),
    "TEACHING_SESSION": (HealthDomain.teaching, "teaching_session"),
    "EDU_ACT": (HealthDomain.teaching, "teaching_session"),
    "FORMAL_COURSE": (HealthDomain.cpd, "course"),
    "REFLECT_LOG": (HealthDomain.reflection, "reflection_log"),
    "REFLECTIVE_PRACTICE_LOG": (HealthDomain.reflection, "reflection_log"),
    "JCF": (HealthDomain.reflection, "reflection_log"),
    "PDP": (HealthDomain.reflection, "reflection_log"),
    "COMPLAINT": (HealthDomain.leadership, "other"),
    "SERIOUS_INCIDENT": (HealthDomain.leadership, "other"),
    "MGMT_REPORT": (HealthDomain.leadership, "other"),
}


def _normalise_kaizen_form_type(event_type: Optional[str]) -> str:
    return (event_type or "UNKNOWN").strip().upper().replace("-", "_").replace(" ", "_")


def _kaizen_status_to_evidence_status(state: Optional[str], surface: str) -> str:
    if surface == "draft":
        return "drafted"
    value = (state or "").strip().lower()
    if value in {"complete", "completed", "accepted"}:
        return "filed"
    if value in {"submitted", "reviewed", "sign-off", "sign_off", "signed-off", "signed_off"}:
        return "reviewed"
    if value in {"returned", "returned for amendment", "needs_amendment", "amend"}:
        return "needs_work"
    return "drafted"


def _kaizen_source_for_surface(surface: str) -> str:
    return "pg_draft" if surface == "draft" else "kaizen_filed"


def _parse_kaizen_date(value: Optional[str]) -> date:
    if not value:
        return date.today()
    raw = value.strip()
    if not raw:
        return date.today()
    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d %b, %Y",
        "%d %b %Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            snippet = raw[:19] if ("T" in raw or " " in raw) and "%H" in fmt else raw
            return datetime.strptime(snippet, fmt).date()
        except ValueError:
            continue
    return date.today()


def evidence_row_to_health_item(row: EvidenceItemRow) -> EvidenceItem:
    """Convert a stored evidence row to the pathway-agnostic ``EvidenceItem``.

    Pure: no I/O, no time queries beyond a single ``datetime.now`` stamp for
    ``created_at``/``updated_at``.
    """
    form_type = _normalise_kaizen_form_type(row.event_type)
    domain, evidence_type = _FORM_TYPE_DOMAIN_LOOKUP.get(
        form_type, (HealthDomain.clinical, "other")
    )
    status = _kaizen_status_to_evidence_status(row.state, row.surface)
    source = _kaizen_source_for_surface(row.surface)
    now = datetime.now(UTC)
    summary = (row.description or row.event_type or "Kaizen portfolio evidence").strip()
    title = (row.event_type or row.description or "Kaizen evidence").strip()
    return EvidenceItem(
        id=row.id,
        user_id=row.user_id,
        domain=domain,
        evidence_type=evidence_type,  # type: ignore[arg-type]
        form_type=form_type if form_type != "UNKNOWN" else None,
        title=title[:200],
        summary=summary[:1000],
        event_date=_parse_kaizen_date(row.date_occurred_on),
        source=source,  # type: ignore[arg-type]
        source_ref=row.detail_url,
        status=status,  # type: ignore[arg-type]
        created_at=now,
        updated_at=now,
    )


def evidence_rows_to_health_items(rows: Iterable[EvidenceItemRow]) -> list[EvidenceItem]:
    return [evidence_row_to_health_item(row) for row in rows]
