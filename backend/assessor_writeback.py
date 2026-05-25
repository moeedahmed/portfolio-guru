"""Guarded assessor write-back planning.

This module maps a reviewed local assessor draft onto the Kaizen assessor
completion surface, but it does not execute browser writes. It is deliberately
separate from ``supervisor_bot`` so ordinary Open / review / recapture / cancel
flows cannot reach a Kaizen write path by importing Telegram handlers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from assessor_drafter import AssessorDraft
from form_schemas import ASSESSOR_FORM_SCHEMAS


class AssessorWriteAction(str, Enum):
    """Reviewed actions the write-back adapter can reason about."""

    FILL_FIELDS = "fill_fields"
    SAVE_DRAFT = "save_draft"
    SUBMIT = "submit"
    SIGN = "sign"
    APPROVE = "approve"
    CANCEL = "cancel"


FINAL_ACTIONS = {
    AssessorWriteAction.SUBMIT,
    AssessorWriteAction.SIGN,
    AssessorWriteAction.APPROVE,
}

LIVE_WRITE_ACTIONS = {
    AssessorWriteAction.FILL_FIELDS,
    AssessorWriteAction.SAVE_DRAFT,
    *FINAL_ACTIONS,
}

SUPPORTED_WRITEBACK_FORMS = {"CBD"}

CBD_ASSESSOR_FIELD_BINDINGS: dict[str, str] = {
    "assessor_registration_number": "Assessor Registration Number",
    "assessor_job_title": "Job title",
    "assessor_other_specify": "If other, please specify",
    "entrustment_scale": "Entrustment Scale",
    "feedback": "Feedback",
    "recommendation": "Recommendation for further learning or development",
}


@dataclass(frozen=True)
class AssessorWriteRequest:
    """A supervisor's explicit reviewed write-back choice."""

    action: AssessorWriteAction
    ticket_uuid: str | None
    form_type: str | None
    reviewed_draft_hash: str | None


@dataclass(frozen=True)
class AssessorFieldWrite:
    """One local draft value mapped to a Kaizen assessor field label."""

    key: str
    label: str
    value: str


@dataclass(frozen=True)
class AssessorBrowserStep:
    """A browser operation descriptor, not an executable Playwright call."""

    kind: str
    target_label: str | None = None
    field_key: str | None = None
    value_present: bool = False


@dataclass(frozen=True)
class AssessorWritePlan:
    """A non-executing write-back plan for one ticket and one action."""

    request: AssessorWriteRequest
    field_writes: list[AssessorFieldWrite] = field(default_factory=list)
    browser_steps: list[AssessorBrowserStep] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)
    live_execution_available: bool = False

    @property
    def touches_kaizen(self) -> bool:
        return self.request.action in LIVE_WRITE_ACTIONS

    @property
    def is_final_action(self) -> bool:
        return self.request.action in FINAL_ACTIONS

    @property
    def is_executable(self) -> bool:
        return not self.blocked_reasons and self.live_execution_available


class AssessorWriteBackUnavailable(RuntimeError):
    """Raised when a caller attempts live Kaizen write execution in this slice."""


def draft_review_hash(draft: AssessorDraft) -> str:
    """Return a stable short hash for the reviewed draft payload.

    The hash lets code bind an explicit action to the exact draft the supervisor
    reviewed without logging the draft text itself.
    """
    payload = {
        "form_type": draft.form_type,
        "ticket_uuid": draft.ticket_uuid,
        "values": draft.values,
        "missing_required": draft.missing_required,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def coerce_action(raw: str | AssessorWriteAction) -> AssessorWriteAction:
    if isinstance(raw, AssessorWriteAction):
        return raw
    try:
        return AssessorWriteAction(str(raw))
    except ValueError as exc:
        raise ValueError(f"Unsupported assessor write action: {raw!r}") from exc


def build_write_request(
    *,
    action: str | AssessorWriteAction,
    ticket_uuid: str | None,
    form_type: str | None,
    reviewed_draft_hash: str | None,
) -> AssessorWriteRequest:
    """Build a typed write request from an explicit reviewed action."""
    return AssessorWriteRequest(
        action=coerce_action(action),
        ticket_uuid=(ticket_uuid or "").strip() or None,
        form_type=(form_type or "").strip() or None,
        reviewed_draft_hash=(reviewed_draft_hash or "").strip() or None,
    )


def build_write_plan(draft: AssessorDraft, request: AssessorWriteRequest) -> AssessorWritePlan:
    """Map a local assessor draft to a guarded Kaizen write-back plan."""
    blocked: list[str] = []
    field_writes: list[AssessorFieldWrite] = []
    steps: list[AssessorBrowserStep] = []

    if request.action is AssessorWriteAction.CANCEL:
        return AssessorWritePlan(
            request=request,
            browser_steps=[AssessorBrowserStep(kind="cancel_local")],
            blocked_reasons=[],
            live_execution_available=False,
        )

    if not request.ticket_uuid:
        blocked.append("ticket_uuid is required for any Kaizen write action")
    if request.ticket_uuid and draft.ticket_uuid and request.ticket_uuid != draft.ticket_uuid:
        blocked.append("ticket_uuid does not match the reviewed draft")
    if not request.form_type:
        blocked.append("form_type is required for assessor write-back")
    if request.form_type and request.form_type != draft.form_type:
        blocked.append("form_type does not match the reviewed draft")
    if request.form_type not in SUPPORTED_WRITEBACK_FORMS:
        blocked.append(f"assessor write-back is not mapped for form type {request.form_type!r}")
    expected_hash = draft_review_hash(draft)
    if request.reviewed_draft_hash != expected_hash:
        blocked.append("reviewed_draft_hash does not match the current draft")

    schema = ASSESSOR_FORM_SCHEMAS.get(draft.form_type)
    required_keys = {
        field_def["key"]
        for field_def in (schema or {}).get("fields", [])
        if field_def.get("required") and field_def.get("key")
    }
    missing_required = sorted(
        key for key in required_keys if not (draft.values.get(key) or "").strip()
    )

    bindings = CBD_ASSESSOR_FIELD_BINDINGS if request.form_type == "CBD" else {}
    for key, label in bindings.items():
        value = (draft.values.get(key) or "").strip()
        if value:
            field_writes.append(AssessorFieldWrite(key=key, label=label, value=value))

    steps.append(AssessorBrowserStep(kind="open_completion_surface", target_label="Fill in"))
    for item in field_writes:
        steps.append(
            AssessorBrowserStep(
                kind="fill_field",
                target_label=item.label,
                field_key=item.key,
                value_present=True,
            )
        )

    if request.action is AssessorWriteAction.SAVE_DRAFT:
        steps.append(AssessorBrowserStep(kind="save_draft", target_label="Save as draft"))
    elif request.action in FINAL_ACTIONS:
        steps.append(AssessorBrowserStep(kind=request.action.value, target_label=request.action.value.title()))
        blocked.append(f"{request.action.value} is a final assessor action and is not live-enabled")

    if request.action in {AssessorWriteAction.SAVE_DRAFT, *FINAL_ACTIONS} and missing_required:
        blocked.append("required assessor fields are missing")

    return AssessorWritePlan(
        request=request,
        field_writes=field_writes,
        browser_steps=steps,
        missing_required=missing_required,
        blocked_reasons=blocked,
        live_execution_available=False,
    )


def render_write_plan(plan: AssessorWritePlan) -> str:
    """Render a Telegram-safe summary of a reviewed, non-executing write plan."""
    action = plan.request.action.value.replace("_", " ")
    lines = [
        f"🧾 *Reviewed Kaizen action plan — {action}*",
        "",
        f"Ticket: `{plan.request.ticket_uuid or 'missing'}`",
        f"Form: `{plan.request.form_type or 'missing'}`",
        "",
        "*Mapped fields*",
    ]
    if plan.field_writes:
        for item in plan.field_writes:
            lines.append(f"• {item.label}: ready")
    else:
        lines.append("• No draft values are mapped yet.")

    if plan.missing_required:
        lines.append("")
        lines.append("*Missing required before any write*")
        for key in plan.missing_required:
            label = CBD_ASSESSOR_FIELD_BINDINGS.get(key) or key.replace("_", " ")
            lines.append(f"• {label}")

    lines.append("")
    lines.append("*Safety boundary*")
    if plan.blocked_reasons:
        for reason in plan.blocked_reasons:
            lines.append(f"• Blocked: {reason}")
    lines.append("• Live Kaizen execution is unavailable in this bot path.")
    lines.append("• This did not open, fill, save, submit, sign, or approve in Kaizen.")
    return "\n".join(lines)


async def execute_write_plan(*args: Any, **kwargs: Any) -> None:
    """Placeholder for a future foreground-approved live runner."""
    raise AssessorWriteBackUnavailable(
        "Assessor Kaizen write-back execution is not available in this slice."
    )
