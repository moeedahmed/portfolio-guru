"""Read-only reader that turns a raw Kaizen assessor ticket into structured
data ready for the Telegram supervisor mode.

The reader does three things, all read-only:

1. Detects the form type from the ticket title (CBD / DOPS / Mini-CEX / QIAT / ESLE).
2. Loads the matching assessor field schema from ``ASSESSOR_FORM_SCHEMAS``.
3. Returns an :class:`AssessorTicketData` payload combining the trainee-side
   section the supervisor needs to read with the assessor fields they will
   later fill in.

It never clicks ``Fill in``, ``Save``, ``Submit``, ``Sign``, ``Delete``,
``Approve`` or ``Send``. The browser-side fetch is delegated to
``assessor_mapper.extract_assessment_detail`` which already enforces that
contract.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Page

from assessor_mapper import (
    AssessorTicketDetail,
    AssessorTicketSummary,
    classify_controls,
    extract_assessment_detail,
)
from form_schemas import ASSESSOR_FORM_SCHEMAS

logger = logging.getLogger(__name__)


_FORM_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Order matters: Mini-CEX must be tried before MSF-style abbreviations and
    # before the generic "CEX" pattern.
    ("MINI_CEX", re.compile(r"\bmini[\s\-]?cex\b", re.I)),
    ("CBD",      re.compile(r"\bcbd\b|case[\s\-]based\s+discussion", re.I)),
    ("DOPS",     re.compile(r"\bdops\b|direct\s+observation\s+of\s+procedural", re.I)),
    ("QIAT",     re.compile(r"\bqiat\b|quality\s+improvement\s+assessment", re.I)),
    ("ESLE",     re.compile(r"\besle\b|extended\s+supervised\s+learning", re.I)),
)


@dataclass
class AssessorTicketData:
    """Structured assessor ticket payload, ready for Telegram rendering."""

    form_type: str | None
    ticket_uuid: str | None
    ticket_url: str | None
    title: str | None
    state: str | None
    trainee_section: list[dict[str, str | None]] = field(default_factory=list)
    pending_assessor_fields: list[dict[str, Any]] = field(default_factory=list)
    needs_write_side_mapping: bool = False


def detect_form_type(title: str | None) -> str | None:
    """Return the canonical form-type key for an assessor ticket title."""
    if not title:
        return None
    for form_type, pattern in _FORM_TYPE_PATTERNS:
        if pattern.search(title):
            return form_type
    return None


def get_assessor_schema(form_type: str | None) -> dict[str, Any] | None:
    if not form_type:
        return None
    return ASSESSOR_FORM_SCHEMAS.get(form_type)


def build_ticket_data(detail: AssessorTicketDetail) -> AssessorTicketData:
    """Combine a raw ticket detail with the matching assessor schema."""
    title = detail.event_type or detail.summary.title
    form_type = detect_form_type(title)
    schema = get_assessor_schema(form_type)

    trainee_section = [
        {"label": row.get("label"), "value": row.get("value")}
        for row in (detail.fields or [])
        if row.get("label")
    ]

    write_controls = detail.write_controls
    if not write_controls:
        write_controls, _ = classify_controls(detail.available_buttons or [])

    return AssessorTicketData(
        form_type=form_type,
        ticket_uuid=detail.summary.uuid,
        ticket_url=detail.url or detail.summary.href,
        title=title,
        state=detail.state or detail.summary.state,
        trainee_section=trainee_section,
        pending_assessor_fields=list(schema["fields"]) if schema else [],
        needs_write_side_mapping=bool(write_controls),
    )


async def open_ticket_readonly(page: Page, summary: AssessorTicketSummary) -> AssessorTicketData:
    """Open a ticket detail page (read-only) and return structured assessor data.

    Delegates to ``assessor_mapper.extract_assessment_detail`` which already
    enforces the read-only contract (no Fill in / Save / Submit clicks).
    """
    detail = await extract_assessment_detail(page, summary)
    return build_ticket_data(detail)


def extract_assessor_section(detail: AssessorTicketDetail) -> list[dict[str, Any]]:
    """Return only the pending assessor schema fields for a detail."""
    return build_ticket_data(detail).pending_assessor_fields
