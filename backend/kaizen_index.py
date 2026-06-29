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
import re
import sqlite3
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


async def delete_user_index(user_id: str | int) -> dict[str, int]:
    """Delete all local Kaizen index rows and run audits for one user."""
    await _ensure_db()
    async with aiosqlite.connect(_current_db_path()) as db:
        evidence_cursor = await db.execute(
            "DELETE FROM evidence_items WHERE user_id = ?",
            (str(user_id),),
        )
        runs_cursor = await db.execute(
            "DELETE FROM index_runs WHERE user_id = ?",
            (str(user_id),),
        )
        await db.commit()
    return {
        "evidence_items": max(evidence_cursor.rowcount or 0, 0),
        "index_runs": max(runs_cursor.rowcount or 0, 0),
    }


def delete_user_index_sync(user_id: str | int) -> None:
    """Synchronous variant used by credential rotation cleanup."""
    path = _current_db_path()
    if not os.path.exists(path):
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with sqlite3.connect(path) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "evidence_items" not in tables and "index_runs" not in tables:
            return
        if "evidence_items" not in tables:
            db.execute("DELETE FROM index_runs WHERE user_id = ?", (str(user_id),))
            db.commit()
            return
        if "index_runs" not in tables:
            db.execute("DELETE FROM evidence_items WHERE user_id = ?", (str(user_id),))
            db.commit()
            return
        db.execute("DELETE FROM evidence_items WHERE user_id = ?", (str(user_id),))
        db.execute("DELETE FROM index_runs WHERE user_id = ?", (str(user_id),))
        db.commit()


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


@dataclass(frozen=True)
class _FormClass:
    """Canonical classification for a recognised Kaizen event label."""

    form_type: str
    domain: HealthDomain
    evidence_type: str


# Recognised Kaizen event labels carry trailing curriculum/version qualifiers
# such as "(2025 update)", "- ST3-ST6", "(ACCS)". Real display names therefore
# never match a bare code like "DOPS" once naively upper/underscore-normalised,
# which is why every unknown previously fell through to clinical/other. We match
# on phrase/token membership of the cleaned label instead, in priority order.
#
# Each rule is (predicate, classification). The FIRST matching rule wins, so the
# order below is significant: e.g. "Educational Supervisor Report" must be caught
# by the supervisor rule before the generic "educational ... attended" CPD rule,
# and reflection must win over the generic "learning" CPD rule.
_FormRule = tuple["_LabelMatch", _FormClass]


class _LabelMatch:
    """Match a cleaned, upper-cased Kaizen label by phrase and/or whole token."""

    def __init__(self, *, phrases: tuple[str, ...] = (), tokens: tuple[str, ...] = ()):
        self._phrases = phrases
        self._tokens = tokens

    def __call__(self, label: str, tokens: frozenset[str]) -> bool:
        if any(phrase in label for phrase in self._phrases):
            return True
        return any(token in tokens for token in self._tokens)


_FORM_RULES: tuple[_FormRule, ...] = (
    # ── Direct WPBA clinical encounters ──────────────────────────────────────
    (
        _LabelMatch(phrases=("MINI CEX", "MINICEX")),
        _FormClass("MINI_CEX", HealthDomain.clinical, "wpba"),
    ),
    (
        _LabelMatch(phrases=("CASE BASED DISCUSSION",), tokens=("CBD",)),
        _FormClass("CBD", HealthDomain.clinical, "wpba"),
    ),
    (
        _LabelMatch(tokens=("DOPS",)),
        _FormClass("DOPS", HealthDomain.clinical, "wpba"),
    ),
    (
        _LabelMatch(phrases=("PROCEDURAL LOG", "PROC LOG")),
        _FormClass("PROC_LOG", HealthDomain.clinical, "wpba"),
    ),
    (
        _LabelMatch(tokens=("ACAF",)),
        _FormClass("ACAF", HealthDomain.clinical, "wpba"),
    ),
    (
        _LabelMatch(tokens=("ACAT",)),
        _FormClass("ACAT", HealthDomain.clinical, "wpba"),
    ),
    (
        _LabelMatch(tokens=("LAT",)),
        _FormClass("LAT", HealthDomain.clinical, "wpba"),
    ),
    (
        _LabelMatch(phrases=("EXTENDED SUPERVISED",), tokens=("ESLE",)),
        _FormClass("ESLE", HealthDomain.clinical, "wpba"),
    ),
    (
        _LabelMatch(phrases=("ULTRASOUND",), tokens=("US",)),
        _FormClass("US_CASE", HealthDomain.clinical, "wpba"),
    ),
    (
        _LabelMatch(phrases=("MULTI-SOURCE FEEDBACK", "MULTI SOURCE FEEDBACK"), tokens=("MSF",)),
        _FormClass("MSF", HealthDomain.clinical, "wpba"),
    ),
    (
        _LabelMatch(
            phrases=(
                "MULTIPLE CONSULTANT REPORT",
                "MULTI-CONSULTANT",
                "MULTIPLE TRAINER REPORT",
                "MULTI-TRAINER",
            ),
            tokens=("MCR", "MTR"),
        ),
        _FormClass("MCR", HealthDomain.clinical, "wpba"),
    ),
    # ── Reflection (must precede generic CPD "learning") ─────────────────────
    (
        _LabelMatch(phrases=("REFLECT", "REFLECTION")),
        _FormClass("REFLECT_LOG", HealthDomain.reflection, "reflection_log"),
    ),
    (
        _LabelMatch(phrases=("PERSONAL DEVELOPMENT PLAN",), tokens=("PDP",)),
        _FormClass("PDP", HealthDomain.reflection, "reflection_log"),
    ),
    (
        _LabelMatch(tokens=("JCF",)),
        _FormClass("JCF", HealthDomain.reflection, "reflection_log"),
    ),
    # ── Teaching delivered / observed (incl. STAT) ───────────────────────────
    (
        _LabelMatch(tokens=("STAT",)),
        _FormClass("STAT", HealthDomain.teaching, "teaching_session"),
    ),
    (
        _LabelMatch(phrases=("TEACHING OBSERVATION", "TEACH OBS")),
        _FormClass("TEACH_OBS", HealthDomain.teaching, "teaching_session"),
    ),
    (
        _LabelMatch(phrases=("TEACH CONFID", "CONFIDENTIALITY")),
        _FormClass("TEACH_CONFID", HealthDomain.teaching, "teaching_session"),
    ),
    (
        _LabelMatch(
            phrases=(
                "TEACHING DELIVERED",
                "DELIVERED BY TRAINEE",
            ),
            tokens=("TEACH", "TEACHING"),
        ),
        _FormClass("TEACH", HealthDomain.teaching, "teaching_session"),
    ),
    # ── QI / audit / research ────────────────────────────────────────────────
    (
        _LabelMatch(phrases=("QUALITY IMPROVEMENT",), tokens=("QIAT", "QIP")),
        _FormClass("QIAT", HealthDomain.qi, "audit"),
    ),
    (
        _LabelMatch(tokens=("AUDIT",)),
        _FormClass("AUDIT", HealthDomain.qi, "audit"),
    ),
    (
        _LabelMatch(phrases=("RESEARCH",)),
        _FormClass("RESEARCH", HealthDomain.qi, "project"),
    ),
    # ── Supervisor / ARCP / governance reports (before generic CPD) ──────────
    # Named summative reports. Explicitly classified so they are recognised
    # rather than defaulting to clinical/other. Mapped to leadership (training
    # oversight/governance) so they never inflate the clinical WPBA count.
    (
        _LabelMatch(
            phrases=("EDUCATIONAL SUPERVISOR", "EDUCATIONAL SUPERVISION"),
            tokens=("ESR", "ESLR"),
        ),
        _FormClass("ESR", HealthDomain.leadership, "other"),
    ),
    (
        _LabelMatch(phrases=("END OF PLACEMENT",)),
        _FormClass("END_OF_PLACEMENT", HealthDomain.leadership, "other"),
    ),
    (
        _LabelMatch(tokens=("ARCP",)),
        _FormClass("ARCP", HealthDomain.leadership, "other"),
    ),
    (
        _LabelMatch(tokens=("FEGS", "STR")),
        _FormClass("STR", HealthDomain.leadership, "other"),
    ),
    (
        _LabelMatch(tokens=("COMPLAINT",)),
        _FormClass("COMPLAINT", HealthDomain.leadership, "other"),
    ),
    (
        _LabelMatch(
            phrases=("SERIOUS INCIDENT", "SERIOUS INC", "SERIOUS UNTOWARD"),
            tokens=("SUI", "DATIX"),
        ),
        _FormClass("SERIOUS_INCIDENT", HealthDomain.leadership, "other"),
    ),
    (
        _LabelMatch(
            phrases=("MANAGEMENT", "GOVERNANCE", "LEADERSHIP"),
            tokens=("APPRAISAL", "GOV", "MGMT"),
        ),
        _FormClass("MGMT_REPORT", HealthDomain.leadership, "other"),
    ),
    # ── CPD: educational activity consumed / attended ────────────────────────
    (
        _LabelMatch(phrases=("FORMAL COURSE",)),
        _FormClass("FORMAL_COURSE", HealthDomain.cpd, "course"),
    ),
    (
        _LabelMatch(
            phrases=(
                "RCEM LEARNING",
                "EDUCATIONAL ACTIVITY",
                "EDUCATIONAL MEETING",
                "CONFERENCE",
            ),
            tokens=("ATTENDED", "EDU", "EDU_ACT"),
        ),
        _FormClass("EDU_ACT", HealthDomain.cpd, "course"),
    ),
)


def _clean_kaizen_label(event_type: Optional[str]) -> tuple[str, frozenset[str]]:
    """Return a phrase-matchable label and its whole-token set.

    Hyphens, underscores and slashes are flattened to spaces so a single phrase
    (e.g. "MINI CEX") matches both raw display names ("Mini-CEX (2025 Update)")
    and canonical codes ("MINI_CEX"). Tokens are alphanumeric runs for matching
    short codes like DOPS, CBD or STAT regardless of surrounding punctuation.
    """
    raw = (event_type or "").strip().upper()
    label = " ".join(re.sub(r"[-_/]+", " ", raw).split())
    tokens = frozenset(part for part in re.split(r"[^A-Z0-9]+", raw) if part)
    return label, tokens


def _normalise_kaizen_form_type(event_type: Optional[str]) -> str:
    """Preserve an unrecognised label as a stable upper/underscore token.

    Used only for audit visibility on unclassified rows; recognised labels are
    mapped to canonical codes via ``_classify_kaizen_event``.
    """
    cleaned = (event_type or "UNKNOWN").strip().upper()
    token = "_".join(part for part in re.split(r"[^A-Z0-9]+", cleaned) if part)
    return token or "UNKNOWN"


def _classify_kaizen_event(
    event_type: Optional[str], surface: str
) -> tuple[Optional[str], HealthDomain, str]:
    """Map a raw Kaizen event label to (form_type, domain, evidence_type).

    Unknown labels and raw file uploads land in the ``unclassified`` domain and
    are never treated as clinical evidence. Unknown form codes are preserved for
    audit/debug; file uploads carry no form code.
    """
    if surface == "file":
        return None, HealthDomain.unclassified, "other"

    label, tokens = _clean_kaizen_label(event_type)
    if "UPLOAD" in tokens or "DOCUMENT" in tokens:
        return None, HealthDomain.unclassified, "other"

    for match, classification in _FORM_RULES:
        if match(label, tokens):
            return classification.form_type, classification.domain, classification.evidence_type

    # Unrecognised: keep the raw token for audit, but never default to clinical.
    return _normalise_kaizen_form_type(event_type), HealthDomain.unclassified, "other"


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
    if surface == "draft":
        return "pg_draft"
    if surface == "file":
        return "file_upload"
    return "kaizen_filed"


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
    form_type, domain, evidence_type = _classify_kaizen_event(row.event_type, row.surface)
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
        form_type=form_type if form_type and form_type != "UNKNOWN" else None,
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
