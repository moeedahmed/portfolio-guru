"""Guarded assessor write-back planning and live save-draft execution.

This module maps a reviewed local assessor draft onto the Kaizen assessor
completion surface and — for one explicitly scoped action only — runs that plan
live via the existing CDP-attached Playwright page.

Live execution is intentionally limited to the ``SAVE_DRAFT`` action on the
CBD completion surface. The runner clicks ``Fill in`` once, fills the mapped
assessor fields, and clicks ``Save as draft``. It never clicks Submit, Sign,
Approve, Send, Delete, or Reject. The Telegram surface is required to gate
the runner behind a separate explicit confirmation step.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from assessor_drafter import AssessorDraft
from form_schemas import ASSESSOR_FORM_SCHEMAS

logger = logging.getLogger(__name__)


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

# Browser step kinds the live runner is allowed to execute. Any other kind in a
# plan blocks live execution. New step kinds must be added here intentionally.
_LIVE_ALLOWED_STEP_KINDS = frozenset({"open_completion_surface", "fill_field", "save_draft"})

# Labels classified as dropdown/select on the CBD assessor completion surface.
# The runner tries select-by-label first for these; everything else fills as text.
_SELECT_FIELD_LABELS = frozenset({"Entrustment Scale"})


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
    """A browser operation descriptor.

    For ``open_completion_surface``, ``fill_field``, and ``save_draft`` kinds the
    descriptor mirrors what the live runner will do. For ``cancel_local`` and
    final-action kinds the descriptor is informational only — the runner never
    executes them.
    """

    kind: str
    target_label: str | None = None
    field_key: str | None = None
    value_present: bool = False


@dataclass(frozen=True)
class AssessorWritePlan:
    """A reviewed write-back plan for one ticket and one action."""

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


@dataclass(frozen=True)
class AssessorWriteResult:
    """Outcome of a live save-draft execution."""

    status: str  # "success" | "failed"
    action: AssessorWriteAction
    filled_fields: list[str] = field(default_factory=list)
    error: str | None = None


class AssessorWriteBackUnavailable(RuntimeError):
    """Raised when a caller attempts a live action the runner refuses to do."""


def draft_review_hash(draft: AssessorDraft) -> str:
    """Return a stable short hash for the reviewed draft payload."""
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
        steps.append(
            AssessorBrowserStep(kind=request.action.value, target_label=request.action.value.title())
        )
        blocked.append(f"{request.action.value} is a final assessor action and is not live-enabled")

    if request.action in {AssessorWriteAction.SAVE_DRAFT, *FINAL_ACTIONS} and missing_required:
        blocked.append("required assessor fields are missing")

    # Live execution is reserved for unblocked CBD save-draft plans with at
    # least one mapped field. Everything else stays plan-only.
    live_executable = (
        not blocked
        and request.action is AssessorWriteAction.SAVE_DRAFT
        and request.form_type in SUPPORTED_WRITEBACK_FORMS
        and bool(field_writes)
    )

    return AssessorWritePlan(
        request=request,
        field_writes=field_writes,
        browser_steps=steps,
        missing_required=missing_required,
        blocked_reasons=blocked,
        live_execution_available=live_executable,
    )


def render_write_plan(plan: AssessorWritePlan) -> str:
    """Render a Telegram-safe summary of a reviewed write plan."""
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
        lines.append("• This plan cannot be saved live.")
        lines.append("• Nothing was opened, filled, saved, submitted, signed, or approved in Kaizen.")
    elif plan.is_executable:
        lines.append("• Live *Save as draft* is available behind explicit confirmation.")
        lines.append(
            "• I will only open the named ticket, fill assessor fields, and click *Save as draft*."
        )
        lines.append("• I will never submit, sign, approve, send, or delete in Kaizen.")
    else:
        lines.append("• Live Kaizen execution is unavailable for this plan.")
        lines.append("• Nothing was opened, filled, saved, submitted, signed, or approved in Kaizen.")
    return "\n".join(lines)


async def execute_write_plan(
    plan: AssessorWritePlan,
    draft: AssessorDraft,
    *,
    page: Any,
    ticket_url: str,
) -> AssessorWriteResult:
    """Run a reviewed CBD save-draft plan against an authenticated Playwright page.

    The runner is intentionally narrow:

    * ``plan.request.action`` must be ``SAVE_DRAFT``. Any other action raises
      :class:`AssessorWriteBackUnavailable` — even cancellation, which has no
      browser side effects and should be handled by callers locally.
    * ``plan.blocked_reasons`` must be empty.
    * ``plan.field_writes`` must be non-empty — saving an empty assessor
      section would replace any in-progress work without intent.
    * The reviewed-draft hash must still match ``draft``.
    * ``ticket_url`` must include ``plan.request.ticket_uuid``.
    * Every browser step kind must be in the live allow-list.

    On success the runner returns a result describing the filled fields. On
    failure modes the runner can recover from (Fill in / field input / Save
    as draft button missing, no confirmation marker, unexpected exception) it
    returns a failed result without raising. Conditions that signal misuse of
    the API raise :class:`AssessorWriteBackUnavailable`.
    """
    if plan.request.action is not AssessorWriteAction.SAVE_DRAFT:
        raise AssessorWriteBackUnavailable(
            "Live execution is restricted to save_draft; "
            f"got {plan.request.action.value!r}."
        )
    if plan.blocked_reasons:
        raise AssessorWriteBackUnavailable(
            "Plan is blocked and cannot be executed live."
        )
    if not plan.field_writes:
        raise AssessorWriteBackUnavailable(
            "Plan has no mapped field writes; refusing to save an empty assessor section."
        )
    if plan.request.reviewed_draft_hash != draft_review_hash(draft):
        raise AssessorWriteBackUnavailable(
            "Reviewed draft hash no longer matches the current draft."
        )
    if not ticket_url or not plan.request.ticket_uuid or plan.request.ticket_uuid not in ticket_url:
        raise AssessorWriteBackUnavailable(
            "ticket_url does not contain the planned ticket UUID."
        )
    for step in plan.browser_steps:
        if step.kind not in _LIVE_ALLOWED_STEP_KINDS:
            raise AssessorWriteBackUnavailable(
                f"Step {step.kind!r} is not permitted in live execution."
            )

    try:
        await page.goto(ticket_url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        if not await _click_exact_label(page, "Fill in"):
            return AssessorWriteResult(
                status="failed",
                action=plan.request.action,
                error="Could not open the assessor completion surface — 'Fill in' control not found.",
            )
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(4)

        filled: list[str] = []
        for write in plan.field_writes:
            ok = await _fill_field_by_label(page, write.label, write.value)
            if not ok:
                return AssessorWriteResult(
                    status="failed",
                    action=plan.request.action,
                    filled_fields=list(filled),
                    error=f"Could not fill the field labelled {write.label!r}.",
                )
            filled.append(write.key)

        if not await _click_exact_label(page, "Save as draft"):
            return AssessorWriteResult(
                status="failed",
                action=plan.request.action,
                filled_fields=list(filled),
                error="'Save as draft' button not found on the assessor completion surface.",
            )
        await asyncio.sleep(5)

        body_text = ""
        try:
            body_text = (await page.inner_text("body") or "").lower()
        except Exception as exc:
            logger.warning("Save-draft confirmation read failed: %s", exc)

        if any(marker in body_text for marker in ("saved as draft", "draft saved", "last saved")):
            return AssessorWriteResult(
                status="success",
                action=plan.request.action,
                filled_fields=list(filled),
            )

        return AssessorWriteResult(
            status="failed",
            action=plan.request.action,
            filled_fields=list(filled),
            error="Save click completed but Kaizen did not confirm the draft.",
        )

    except AssessorWriteBackUnavailable:
        raise
    except Exception as exc:
        logger.warning("Assessor save-draft execution failed: %s", exc)
        return AssessorWriteResult(
            status="failed",
            action=plan.request.action,
            error=f"Execution error: {type(exc).__name__}: {exc}",
        )


async def _click_exact_label(page: Any, label: str) -> bool:
    """Click the first element whose visible text matches ``label`` exactly.

    Returns ``False`` when no such element exists. The runner uses this helper
    only for the two whitelisted controls — ``Fill in`` and ``Save as draft``.
    """
    locator = page.get_by_text(label, exact=True).first
    if not await locator.count():
        return False
    await locator.click()
    return True


async def _fill_field_by_label(page: Any, label: str, value: str) -> bool:
    """Fill the labelled assessor field with ``value``.

    Tries to dispatch by label kind: select-by-label first when the label is in
    ``_SELECT_FIELD_LABELS``, text input otherwise. Returns ``False`` when the
    field is not found or the input can't accept the value.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        return False
    target = page.get_by_label(label, exact=True).first
    if not await target.count():
        return False

    if label in _SELECT_FIELD_LABELS:
        try:
            await target.select_option(label=cleaned)
            return True
        except Exception as exc:
            logger.warning("Select-by-label failed for %r: %s", label, exc)
            return False

    try:
        await target.click()
        await target.fill(cleaned)
        return True
    except Exception as exc:
        logger.warning("Fill failed for %r: %s", label, exc)
        return False
