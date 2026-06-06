"""Read-only Kaizen Portfolio Index sync driver.

This module turns visible Kaizen timeline/activity/detail pages into
``kaizen_index.EvidenceItemRow`` records. It is deliberately read-only: callers
must provide an already-authenticated page/session, and this module never
types credentials, clicks write controls, saves, submits, signs, approves, or
deletes anything.

The high-level helper :func:`sync_kaizen_portfolio_index_for_user` opens an
isolated CDP page via the existing trusted login/session bootstrap in
``backend/kaizen_form_filer`` (cached session first, saved credentials second)
and then hands the resulting page to :func:`sync_kaizen_portfolio_index`. The
login bootstrap is reused, never reimplemented here, so the write-side
Playwright actions stay confined to the form filer and this driver remains
purely read-only.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from urllib.parse import quote, urljoin

from kaizen_index import (
    EvidenceItemRow,
    finish_index_run,
    start_index_run,
    upsert_evidence_item,
)

KAIZEN_BASE_URL = "https://kaizenep.com"

PORTFOLIO_HEALTH_TIMELINE_CATEGORIES: tuple[str, ...] = (
    "Assessments",
    "Procedural Logs",
    "Reflection",
    "Educational Review & Meetings",
    "MSF",
    "Teaching & Education",
    "Research, Audit & QI",
    "Manage, Administer & Lead",
    "e-Learning",
    "Exams",
    "Documents",
)

WRITE_ACTION_LABELS: tuple[str, ...] = (
    "approve",
    "delete",
    "fill in",
    "save",
    "send",
    "sign",
    "submit",
)


class KaizenAuthRequired(RuntimeError):
    """The page redirected to Kaizen/RCEM authentication."""


class KaizenSyncDrift(RuntimeError):
    """A Kaizen surface did not match the expected read-only shape."""


@dataclass
class KaizenTimelineRow:
    """Visible row from a Kaizen timeline-like listing."""

    title: Optional[str]
    href: Optional[str]
    uuid: Optional[str]
    state: Optional[str] = None
    date_text: Optional[str] = None
    surface: str = "event"
    category: Optional[str] = None


@dataclass
class KaizenSyncResult:
    """Foreground-friendly summary of one read-only index run."""

    run_id: int
    status: str
    rows_seen: int = 0
    rows_written: int = 0
    rows_drifted: int = 0
    notes: list[str] = field(default_factory=list)


def _normalise_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _is_auth_url(url: str | None) -> bool:
    value = (url or "").lower()
    return "auth.kaizenep.com" in value or "eportfolio.rcem.ac.uk" in value


def _event_uuid_from_href(href: str | None) -> tuple[Optional[str], str]:
    if not href:
        return None, "event"
    match = re.search(r"/events/(view|view-section)/([0-9a-f-]+)", href, re.I)
    if not match:
        return None, "event"
    route_kind = match.group(1).lower()
    surface = "event_section" if route_kind == "view-section" else "event"
    return match.group(2), surface


def _absolute_url(href: str | None) -> Optional[str]:
    if not href:
        return None
    return urljoin(KAIZEN_BASE_URL, href)


def _category_url(category: str) -> str:
    return f"{KAIZEN_BASE_URL}/events/list/{quote(category, safe='')}"


def _looks_like_write_control(label: str | None) -> bool:
    text = (label or "").strip().lower()
    return any(candidate in text for candidate in WRITE_ACTION_LABELS)


def _coerce_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (_normalise_text(item) for item in value) if text]


def _field_value(fields: Iterable[dict[str, Any]], *label_fragments: str) -> Optional[str]:
    fragments = [fragment.lower() for fragment in label_fragments]
    for field in fields:
        label = (field.get("label") or "").lower()
        if any(fragment in label for fragment in fragments):
            return _normalise_text(field.get("value"))
    return None


def _description_from_detail(
    fields: Iterable[dict[str, Any]],
    detail_description: Optional[str],
    row_title: Optional[str],
) -> Optional[str]:
    if detail_description:
        return _normalise_text(detail_description)
    preferred = _field_value(
        fields,
        "case to be discussed",
        "reflective comments",
        "reflection of event",
        "description",
        "title of reflection",
        "comment",
    )
    return preferred or _normalise_text(row_title)


def _row_from_payload(payload: dict[str, Any], *, category: str, surface: str = "event") -> KaizenTimelineRow:
    href = _absolute_url(payload.get("href"))
    uuid, href_surface = _event_uuid_from_href(href)
    return KaizenTimelineRow(
        title=_normalise_text(payload.get("title")),
        href=href,
        uuid=uuid or _normalise_text(payload.get("uuid")),
        state=_normalise_text(payload.get("state")),
        date_text=_normalise_text(payload.get("date_text") or payload.get("date")),
        surface=surface if surface != "event" else href_surface,
        category=category,
    )


def _evidence_from_detail(
    *,
    user_id: str | int,
    row: KaizenTimelineRow,
    detail: dict[str, Any],
) -> EvidenceItemRow:
    fields = detail.get("fields") if isinstance(detail.get("fields"), list) else []
    detail_url = _absolute_url(detail.get("url")) or row.href
    event_type = _normalise_text(detail.get("event_type")) or row.title
    state = _normalise_text(detail.get("state")) or row.state
    date_occurred = (
        _field_value(fields, "date occurred", "date of activity", "date of event", "date of esle")
        or row.date_text
    )
    end_date = _field_value(fields, "end date")
    filled_in_by = _normalise_text(detail.get("filled_in_by"))
    filled_in_on = _normalise_text(detail.get("filled_in_on")) or _field_value(fields, "filled in on")
    description = _description_from_detail(fields, _normalise_text(detail.get("description")), row.title)
    tags = _coerce_list(detail.get("tags"))

    return EvidenceItemRow(
        id=row.uuid or detail_url or f"{row.category}:{event_type}:{date_occurred}",
        user_id=str(user_id),
        surface=row.surface,
        event_type=event_type,
        category=row.category,
        state=state,
        date_occurred_on=date_occurred,
        end_date=end_date,
        description=description,
        linked_kc_tags=tags,
        filled_in_by=filled_in_by,
        filled_in_on=filled_in_on,
        parent_event_id=None,
        detail_url=detail_url,
    )


async def _wait_for_readonly_render(page: Any) -> None:
    wait_for_load_state = getattr(page, "wait_for_load_state", None)
    if wait_for_load_state:
        for state, timeout in (("domcontentloaded", 30000), ("networkidle", 15000)):
            try:
                await wait_for_load_state(state, timeout=timeout)
            except Exception:
                # Angular pages can keep network activity open. Treat networkidle as
                # best-effort; the subsequent DOM read still validates shape.
                if state == "domcontentloaded":
                    raise
    await asyncio.sleep(0)


async def _goto_readonly(page: Any, url: str) -> None:
    goto = getattr(page, "goto", None)
    if not goto:
        raise KaizenSyncDrift("page object does not expose goto()")
    await goto(url, wait_until="domcontentloaded", timeout=40000)
    await _wait_for_readonly_render(page)
    await _raise_if_auth(page)


async def _raise_if_auth(page: Any) -> None:
    url = getattr(page, "url", "")
    if _is_auth_url(url):
        raise KaizenAuthRequired("Kaizen authentication required")


async def _expand_readonly_listing(page: Any) -> None:
    """Scroll a read-only listing so lazy-loaded rows enter the DOM."""
    evaluate = getattr(page, "evaluate", None)
    if not evaluate:
        return
    try:
        await evaluate(
            """async () => {
              const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
              let previous = -1;
              let stable = 0;
              for (let i = 0; i < 16; i += 1) {
                const rows = document.querySelectorAll('.row.event-inner, .activity, li').length;
                window.scrollTo(0, document.body.scrollHeight);
                await sleep(250);
                const nextRows = document.querySelectorAll('.row.event-inner, .activity, li').length;
                if (nextRows <= previous || nextRows === rows) {
                  stable += 1;
                } else {
                  stable = 0;
                }
                previous = nextRows;
                if (stable >= 3) break;
              }
              window.scrollTo(0, 0);
            }"""
        )
    except Exception:
        # Scrolling is a best-effort expansion step. The following DOM read still
        # determines whether the page shape is usable.
        return


async def extract_timeline_rows(
    page: Any,
    category: str,
    *,
    limit: int | None = None,
) -> list[KaizenTimelineRow]:
    """Read visible timeline rows for one category without opening details."""
    await _raise_if_auth(page)
    await _expand_readonly_listing(page)
    payload = await page.evaluate(
        """(limit) => {
          const text = el => (el && el.textContent ? el.textContent.trim().replace(/\\s+/g, ' ') : null);
          const rows = Array.from(document.querySelectorAll('.row.event-inner'));
          return rows.slice(0, limit || rows.length).map(row => {
            const link = row.querySelector('a[href*="/events/view"], a[router-link]');
            const titleEl = row.querySelector('h2.entry-title, .entry-title');
            const stateEl = row.querySelector('.event-section-progress-state');
            const rightText = text(row.querySelector('.col-right')) || text(row);
            const dateMatch = rightText && rightText.match(/\\b\\d{1,2}\\s+[A-Za-z]{3,9},?\\s+\\d{4}\\b|\\b\\d{1,2}\\/\\d{1,2}\\/\\d{4}\\b/);
            return {
              title: text(titleEl || link),
              href: link ? link.getAttribute('href') : null,
              state: text(stateEl),
              date_text: dateMatch ? dateMatch[0] : null
            };
          }).filter(row => row.title || row.href);
        }""",
        limit,
    )
    if not isinstance(payload, list):
        raise KaizenSyncDrift(f"Timeline category {category} returned non-list payload")
    return [_row_from_payload(row, category=category) for row in payload if isinstance(row, dict)]


async def extract_activity_drafts(page: Any, *, limit: int | None = None) -> list[KaizenTimelineRow]:
    """Read visible saved-draft/activity rows as draft evidence candidates."""
    await _raise_if_auth(page)
    await _expand_readonly_listing(page)
    payload = await page.evaluate(
        """(limit) => {
          const text = el => (el && el.textContent ? el.textContent.trim().replace(/\\s+/g, ' ') : null);
          const candidates = Array.from(document.querySelectorAll('.row.event-inner, .activity, li'));
          return candidates.slice(0, limit || candidates.length).map(row => {
            const link = row.querySelector('a[href*="/events/view-section/"], a[href*="/events/view/"]');
            const titleEl = row.querySelector('h2.entry-title, .entry-title, h3, strong');
            const rowText = text(row);
            const dateMatch = rowText && rowText.match(/\\b\\d{1,2}\\s+[A-Za-z]{3,9},?\\s+\\d{4}\\b|\\b\\d{1,2}\\/\\d{1,2}\\/\\d{4}\\b/);
            const draftish = /draft|saved|pending/i.test(rowText || '');
            return {
              title: text(titleEl || link) || rowText,
              href: link ? link.getAttribute('href') : null,
              state: draftish ? 'draft' : null,
              date_text: dateMatch ? dateMatch[0] : null,
              draftish
            };
          }).filter(row => row.draftish && (row.title || row.href));
        }""",
        limit,
    )
    if not isinstance(payload, list):
        raise KaizenSyncDrift("Activities returned non-list payload")
    return [
        _row_from_payload(row, category="Activities", surface="draft")
        for row in payload
        if isinstance(row, dict)
    ]


async def extract_event_detail(page: Any, row: KaizenTimelineRow, *, user_id: str | int) -> EvidenceItemRow:
    """Open and read one event/detail page without clicking write controls."""
    if not row.href:
        raise KaizenSyncDrift(f"Timeline row has no detail href: {row.title}")
    await _goto_readonly(page, row.href)
    payload = await page.evaluate(
        """() => {
          const text = el => (el && el.textContent ? el.textContent.trim().replace(/\\s+/g, ' ') : null);
          const fields = Array.from(document.querySelectorAll('.form-text__form-group, .form-readonly__form-group')).map(group => ({
            label: text(group.querySelector('.form-text__control-label, .control-label')),
            value: text(group.querySelector('.form-text__field-value, .field-value, dd'))
          })).filter(field => field.label || field.value);
          const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], a.btn'))
            .map(el => (el.textContent || el.value || '').trim().replace(/\\s+/g, ' '))
            .filter(Boolean);
          return {
            event_type: text(document.querySelector('h1')),
            state: text(document.querySelector('.event-section-progress-state')),
            description: text(document.querySelector('.form-text__description')),
            fields,
            tags: Array.from(document.querySelectorAll('.event-tag')).map(text).filter(Boolean),
            filled_in_by: text(document.querySelector('.event-users')),
            filled_in_on: text(document.querySelector('.event-date, .filled-in-on')),
            buttons,
            url: location.href
          };
        }"""
    )
    if not isinstance(payload, dict):
        raise KaizenSyncDrift(f"Detail page returned non-object payload for {row.href}")
    write_controls = [label for label in _coerce_list(payload.get("buttons")) if _looks_like_write_control(label)]
    # Write controls can exist on read-only pages; the driver never clicks them.
    if "submit" in {label.lower() for label in write_controls}:
        payload["write_controls_present"] = True
    return _evidence_from_detail(user_id=user_id, row=row, detail=payload)


async def sync_kaizen_portfolio_index(
    user_id: str | int,
    page: Any,
    *,
    categories: Iterable[str] = PORTFOLIO_HEALTH_TIMELINE_CATEGORIES,
    include_activities: bool = True,
    row_limit_per_category: int | None = None,
) -> KaizenSyncResult:
    """Run one read-only sync into ``evidence_items`` using an existing page.

    The page must already be authenticated. This function only navigates to
    Kaizen read surfaces and reads DOM state.
    """
    run_id = await start_index_run(user_id)
    result = KaizenSyncResult(run_id=run_id, status="running")
    seen_ids: set[str] = set()

    try:
        for category in categories:
            try:
                await _goto_readonly(page, _category_url(category))
                rows = await extract_timeline_rows(
                    page, category, limit=row_limit_per_category
                )
                result.rows_seen += len(rows)
                for row in rows:
                    row_key = row.uuid or row.href
                    if not row_key or row_key in seen_ids:
                        continue
                    seen_ids.add(row_key)
                    try:
                        evidence = await extract_event_detail(page, row, user_id=user_id)
                        await upsert_evidence_item(evidence)
                        result.rows_written += 1
                    except KaizenAuthRequired:
                        raise
                    except Exception as exc:
                        result.rows_drifted += 1
                        result.notes.append(f"{category}: detail drift for {row_key}: {exc}")
            except KaizenAuthRequired:
                raise
            except Exception as exc:
                result.rows_drifted += 1
                result.notes.append(f"{category}: list drift: {exc}")

        if include_activities:
            try:
                await _goto_readonly(page, f"{KAIZEN_BASE_URL}/activities")
                drafts = await extract_activity_drafts(page, limit=row_limit_per_category)
                result.rows_seen += len(drafts)
                for row in drafts:
                    row_key = row.uuid or row.href
                    if not row_key or row_key in seen_ids:
                        continue
                    seen_ids.add(row_key)
                    try:
                        evidence = await extract_event_detail(page, row, user_id=user_id)
                        evidence.surface = "draft"
                        evidence.state = evidence.state or "draft"
                        await upsert_evidence_item(evidence)
                        result.rows_written += 1
                    except KaizenAuthRequired:
                        raise
                    except Exception as exc:
                        result.rows_drifted += 1
                        result.notes.append(f"Activities: draft drift for {row_key}: {exc}")
            except KaizenAuthRequired:
                raise
            except Exception as exc:
                result.rows_drifted += 1
                result.notes.append(f"Activities: list drift: {exc}")

        if result.rows_drifted and result.rows_written:
            result.status = "partial"
        elif result.rows_drifted:
            result.status = "drift"
        else:
            result.status = "ok"
        await finish_index_run(
            run_id,
            result.status,  # type: ignore[arg-type]
            rows_seen=result.rows_seen,
            rows_written=result.rows_written,
            rows_drifted=result.rows_drifted,
            notes="; ".join(result.notes)[:2000] if result.notes else None,
        )
        return result
    except KaizenAuthRequired as exc:
        result.status = "auth_required"
        result.notes.append(str(exc))
        await finish_index_run(
            run_id,
            "auth_required",
            rows_seen=result.rows_seen,
            rows_written=result.rows_written,
            rows_drifted=result.rows_drifted,
            notes="; ".join(result.notes)[:2000],
        )
        return result
    except Exception as exc:
        result.status = "failed"
        result.notes.append(str(exc))
        await finish_index_run(
            run_id,
            "failed",
            rows_seen=result.rows_seen,
            rows_written=result.rows_written,
            rows_drifted=result.rows_drifted,
            notes="; ".join(result.notes)[:2000],
        )
        return result


# ── Trusted session bootstrap (delegates to backend.kaizen_form_filer) ──────
#
# The CDP connect / cached-session / login / persist-session helpers below are
# intentionally thin wrappers around the existing, deterministic Kaizen login
# code in ``backend/kaizen_form_filer.py``. Wrapping them as module-level
# functions keeps this read-only driver free of write-side Playwright actions
# (clicking, filling, typing) and gives tests an obvious monkeypatch surface
# without having to substitute the heavyweight form-filer module.


async def _open_kaizen_session_page() -> tuple[Any, Any]:
    """Open an isolated CDP page using the form filer's CDP helper."""
    from kaizen_form_filer import connect_cdp_browser

    return await connect_cdp_browser()


async def _restore_cached_session(
    page: Any,
    user_id: str | int,
    username: str | None = None,
) -> bool:
    """Try to replay a previously saved Kaizen session into ``page``."""
    from kaizen_form_filer import use_cached_session

    return await use_cached_session(page, int(user_id), username)


async def _login_kaizen_page(page: Any, username: str, password: str) -> bool:
    """Run the existing RCEM/Kaizen two-step login on ``page``."""
    from kaizen_form_filer import _login as _kaizen_login

    return await _kaizen_login(page, username, password)


async def _persist_session_state(
    context: Any,
    user_id: str | int,
    username: str | None = None,
) -> None:
    """Best-effort save of the freshly-authenticated session cookies."""
    from kaizen_form_filer import save_session_state

    await save_session_state(context, int(user_id), username)


def _load_user_credentials(user_id: str | int) -> Optional[tuple[str, str]]:
    """Return (username, password) for ``user_id`` if stored, else None."""
    from store import get_credentials

    return get_credentials(int(user_id))


async def _record_bootstrap_failure(
    user_id: str | int, status: str, note: str
) -> KaizenSyncResult:
    """Open + close an ``index_runs`` row when the sync never reaches Kaizen.

    Used for credential-missing, login-failure, or CDP-unavailable paths so
    /settings can still surface why the most recent sync attempt did not run.
    """
    run_id = await start_index_run(user_id)
    result = KaizenSyncResult(run_id=run_id, status=status)
    result.notes.append(note)
    await finish_index_run(
        run_id,
        status,  # type: ignore[arg-type]
        rows_seen=result.rows_seen,
        rows_written=result.rows_written,
        rows_drifted=result.rows_drifted,
        notes=note[:2000],
    )
    return result


async def _close_session(context: Any, pw: Any) -> None:
    if context is not None:
        try:
            await context.close()
        except Exception:
            pass
    if pw is not None:
        try:
            await pw.stop()
        except Exception:
            pass


async def sync_kaizen_portfolio_index_for_user(
    user_id: str | int,
    *,
    categories: Iterable[str] = PORTFOLIO_HEALTH_TIMELINE_CATEGORIES,
    include_activities: bool = True,
    row_limit_per_category: int | None = None,
) -> KaizenSyncResult:
    """Open an authenticated Kaizen page and run the read-only index sync.

    Bootstrap order:

    1. Open an isolated CDP context via ``connect_cdp_browser`` (the same
       per-user isolation the form filer uses).
    2. Try to restore a previously saved session with ``use_cached_session``.
    3. If the cache is missing or stale, look up saved credentials through
       ``store.get_credentials`` and run the existing RCEM/Kaizen login.
    4. After a successful fresh login, persist the new session state so the
       next refresh can skip the password step.
    5. Hand the authenticated page to :func:`sync_kaizen_portfolio_index`.

    Any bootstrap-stage failure (CDP unavailable, no saved credentials, login
    refused) still records an ``index_runs`` row so the UI has a status to
    show. The isolated CDP context and the Playwright handle are always
    closed in ``finally`` so this helper does not leak browser state.
    """
    page, pw = await _open_kaizen_session_page()
    if page is None:
        return await _record_bootstrap_failure(
            user_id,
            "failed",
            "Could not open isolated Kaizen CDP context.",
        )
    context = getattr(page, "context", None)
    credentials = _load_user_credentials(user_id)
    username = credentials[0] if credentials else None

    try:
        try:
            authed = await _restore_cached_session(page, user_id, username)
        except Exception:
            authed = False

        if not authed:
            if not credentials:
                return await _record_bootstrap_failure(
                    user_id,
                    "auth_required",
                    "No saved Kaizen credentials; cannot start sync session.",
                )
            username, password = credentials
            try:
                logged_in = await _login_kaizen_page(page, username, password)
            except Exception as exc:
                return await _record_bootstrap_failure(
                    user_id,
                    "failed",
                    f"Kaizen login raised an exception: {exc}",
                )
            if not logged_in:
                return await _record_bootstrap_failure(
                    user_id,
                    "auth_required",
                    "Kaizen login did not land on a portfolio page.",
                )
            if context is not None:
                try:
                    await _persist_session_state(context, user_id, username)
                except Exception:
                    pass

        return await sync_kaizen_portfolio_index(
            user_id,
            page,
            categories=categories,
            include_activities=include_activities,
            row_limit_per_category=row_limit_per_category,
        )
    finally:
        await _close_session(context, pw)
