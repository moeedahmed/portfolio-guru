"""vNext conversational case workspace engine.

This module models the case workspace state machine for the planned
private vNext test bot. It is intentionally pure: no Telegram, LLM,
Kaizen, billing, credential, or filesystem side effects. Inputs are
already-classified :class:`IngestEvent` values; outputs are an
immutable :class:`EngineSnapshot` describing the new workspace plus
the next actions an orchestrator should take.

The driving idea is "conversational outside, deterministic inside":

* Chat turns are kept separate from source-backed case facts so side
  conversation cannot pollute the draft.
* Every case fact carries the source type (text / voice / image /
  document / user_confirmation / system) and the turn id that produced
  it, so provenance is auditable.
* Facts derived from images or documents are treated as stricter and
  remain unconfirmed until the user explicitly confirms them; only
  source-backed facts that pass the eligibility policy are exposed to
  the draft.

Nothing in here decides intent classification — that is the
orchestrator's job (see ``conversational_router``). Keeping the engine
deterministic and stateless across calls makes it cheap to test and
safe to embed inside the existing bot when the vNext slice graduates
beyond the private test bot.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class CaseState(str, Enum):
    IDLE = "idle"
    POSSIBLE_CASE = "possible_case"
    COLLECTING = "collecting"
    CLARIFYING = "clarifying"
    DRAFT_READY = "draft_ready"
    SAVING = "saving"
    ABANDONED = "abandoned"


class SourceType(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    DOCUMENT = "document"
    USER_CONFIRMATION = "user_confirmation"
    SYSTEM = "system"


STRICTER_SOURCES: frozenset[SourceType] = frozenset(
    {SourceType.IMAGE, SourceType.DOCUMENT}
)


class IngestKind(str, Enum):
    CASE_DETAIL = "case_detail"
    POSSIBLE_CASE_DETAIL = "possible_case_detail"
    SIDE_QUESTION = "side_question"
    CORRECTION = "correction"
    CONFIRMATION = "confirmation"
    NEW_CASE = "new_case"
    ABANDON = "abandon"
    REQUEST_DRAFT = "request_draft"
    REQUEST_SAVE = "request_save"


class ActionKind(str, Enum):
    ANSWER_CHAT = "answer_chat"
    ACK_CASE_DETAILS = "ack_case_details"
    REQUEST_CASE_CONFIRMATION = "request_case_confirmation"
    REQUEST_CLARIFICATION = "request_clarification"
    REQUEST_FACT_CONFIRMATION = "request_fact_confirmation"
    OFFER_DRAFT = "offer_draft"
    DRAFT_NOT_READY = "draft_not_ready"
    SAVE_DRAFT = "save_draft"
    START_NEW_CASE = "start_new_case"
    ABANDON_CASE = "abandon_case"
    NOOP = "noop"


@dataclass(frozen=True)
class CaseFact:
    """A single source-backed case detail.

    ``confirmed`` is reserved for facts the user has explicitly accepted
    (typed back, tapped a confirm action, or supplied as a correction).
    Stricter sources require ``confirmed=True`` before becoming
    draft-eligible.
    """

    key: str
    value: str
    source_type: SourceType
    source_turn_id: str
    confirmed: bool = False

    @property
    def is_stricter(self) -> bool:
        return self.source_type in STRICTER_SOURCES

    @property
    def draft_eligible(self) -> bool:
        if self.is_stricter:
            return self.confirmed
        return True


@dataclass(frozen=True)
class ChatTurn:
    """A conversational turn captured as chat context, not case data."""

    turn_id: str
    role: str
    text: str
    source_type: SourceType = SourceType.TEXT


@dataclass(frozen=True)
class IngestEvent:
    """A classified input the orchestrator hands to the engine.

    ``extracted_facts`` is a tuple of ``(key, value)`` pairs that the
    upstream classifier already attributed to this turn. ``corrections``
    is a parallel tuple used when ``kind`` is :attr:`IngestKind.CORRECTION`:
    each pair overrides any existing fact with the same key and is stored
    as a user-confirmed fact regardless of the inbound ``source_type``.
    """

    turn_id: str
    text: str
    source_type: SourceType
    kind: IngestKind
    extracted_facts: tuple[tuple[str, str], ...] = ()
    corrections: tuple[tuple[str, str], ...] = ()
    clarification_target: str | None = None

    def __post_init__(self) -> None:
        if not self.turn_id:
            raise ValueError("turn_id is required")
        if not isinstance(self.source_type, SourceType):
            raise TypeError("source_type must be a SourceType")
        if not isinstance(self.kind, IngestKind):
            raise TypeError("kind must be an IngestKind")


@dataclass(frozen=True)
class NextAction:
    kind: ActionKind
    payload: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CaseWorkspace:
    """Immutable snapshot of the current case workspace."""

    case_id: str
    state: CaseState = CaseState.IDLE
    facts: tuple[CaseFact, ...] = ()
    chat_turns: tuple[ChatTurn, ...] = ()
    pending_clarification: str | None = None

    def fact_for(self, key: str) -> CaseFact | None:
        for fact in self.facts:
            if fact.key == key:
                return fact
        return None

    def draft_eligible_facts(self) -> tuple[CaseFact, ...]:
        return tuple(fact for fact in self.facts if fact.draft_eligible)

    def has_unconfirmed_stricter_facts(self) -> bool:
        return any(fact.is_stricter and not fact.confirmed for fact in self.facts)


@dataclass(frozen=True)
class EngineSnapshot:
    workspace: CaseWorkspace
    actions: tuple[NextAction, ...]


def new_workspace(case_id: str | None = None) -> CaseWorkspace:
    return CaseWorkspace(case_id=case_id or _fresh_case_id())


def apply_event(workspace: CaseWorkspace, event: IngestEvent) -> EngineSnapshot:
    """Apply ``event`` to ``workspace`` and return the new snapshot.

    The function is total: every (state, kind) combination produces a
    valid snapshot. Unhandled combinations fall through to a NOOP action
    so the orchestrator can decide how to recover without the engine
    raising.
    """

    if event.kind is IngestKind.ABANDON:
        return _abandon(workspace, event)
    if event.kind is IngestKind.NEW_CASE:
        return _start_new_case(workspace, event)
    if event.kind is IngestKind.SIDE_QUESTION:
        return _record_chat_turn(workspace, event, ActionKind.ANSWER_CHAT)
    if event.kind is IngestKind.CONFIRMATION:
        return _confirm_pending(workspace, event)
    if event.kind is IngestKind.CORRECTION:
        return _apply_correction(workspace, event)
    if event.kind is IngestKind.REQUEST_DRAFT:
        return _offer_draft(workspace, event)
    if event.kind is IngestKind.REQUEST_SAVE:
        return _request_save(workspace, event)
    if event.kind is IngestKind.POSSIBLE_CASE_DETAIL:
        return _ingest_case_facts(workspace, event, provisional=True)
    if event.kind is IngestKind.CASE_DETAIL:
        return _ingest_case_facts(workspace, event, provisional=False)
    return EngineSnapshot(
        workspace=_with_chat_turn(workspace, event),
        actions=(NextAction(kind=ActionKind.NOOP),),
    )


def _ingest_case_facts(
    workspace: CaseWorkspace,
    event: IngestEvent,
    *,
    provisional: bool,
) -> EngineSnapshot:
    new_facts = _build_facts(event)
    merged_facts = _merge_facts(workspace.facts, new_facts)
    chat_turns = _append_chat_turn(workspace.chat_turns, event)

    if provisional and not workspace.facts:
        next_state = CaseState.POSSIBLE_CASE
        actions: tuple[NextAction, ...] = (
            NextAction(
                kind=ActionKind.REQUEST_CASE_CONFIRMATION,
                payload={"reason": "ambiguous_case_signal"},
            ),
        )
    else:
        next_state = CaseState.COLLECTING
        actions = (
            NextAction(
                kind=ActionKind.ACK_CASE_DETAILS,
                payload={"new_facts": str(len(new_facts))},
            ),
        )

    if any(fact.is_stricter and not fact.confirmed for fact in new_facts):
        actions = actions + (
            NextAction(
                kind=ActionKind.REQUEST_FACT_CONFIRMATION,
                payload={"reason": "stricter_source"},
            ),
        )

    return EngineSnapshot(
        workspace=CaseWorkspace(
            case_id=workspace.case_id,
            state=next_state,
            facts=merged_facts,
            chat_turns=chat_turns,
            pending_clarification=workspace.pending_clarification,
        ),
        actions=actions,
    )


def _apply_correction(
    workspace: CaseWorkspace, event: IngestEvent
) -> EngineSnapshot:
    corrections = event.corrections or event.extracted_facts
    if not corrections:
        return _record_chat_turn(workspace, event, ActionKind.NOOP)

    corrected_facts = tuple(
        CaseFact(
            key=key,
            value=value,
            source_type=SourceType.USER_CONFIRMATION,
            source_turn_id=event.turn_id,
            confirmed=True,
        )
        for key, value in corrections
    )
    merged = _merge_facts(workspace.facts, corrected_facts)
    chat_turns = _append_chat_turn(workspace.chat_turns, event)

    return EngineSnapshot(
        workspace=CaseWorkspace(
            case_id=workspace.case_id,
            state=CaseState.COLLECTING if merged else workspace.state,
            facts=merged,
            chat_turns=chat_turns,
            pending_clarification=workspace.pending_clarification,
        ),
        actions=(
            NextAction(
                kind=ActionKind.ACK_CASE_DETAILS,
                payload={"corrected_facts": str(len(corrected_facts))},
            ),
        ),
    )


def _confirm_pending(workspace: CaseWorkspace, event: IngestEvent) -> EngineSnapshot:
    if not workspace.facts:
        return _record_chat_turn(workspace, event, ActionKind.NOOP)

    confirmed_facts = tuple(
        CaseFact(
            key=fact.key,
            value=fact.value,
            source_type=fact.source_type,
            source_turn_id=fact.source_turn_id,
            confirmed=True,
        )
        for fact in workspace.facts
    )
    next_state = (
        CaseState.COLLECTING
        if workspace.state in {CaseState.POSSIBLE_CASE, CaseState.CLARIFYING}
        else workspace.state
    )
    chat_turns = _append_chat_turn(workspace.chat_turns, event)

    return EngineSnapshot(
        workspace=CaseWorkspace(
            case_id=workspace.case_id,
            state=next_state,
            facts=confirmed_facts,
            chat_turns=chat_turns,
            pending_clarification=None,
        ),
        actions=(NextAction(kind=ActionKind.ACK_CASE_DETAILS),),
    )


def _start_new_case(workspace: CaseWorkspace, event: IngestEvent) -> EngineSnapshot:
    fresh = new_workspace()
    snapshot = _ingest_case_facts(fresh, event, provisional=False)
    actions = (NextAction(kind=ActionKind.START_NEW_CASE),) + snapshot.actions
    return EngineSnapshot(workspace=snapshot.workspace, actions=actions)


def _abandon(workspace: CaseWorkspace, event: IngestEvent) -> EngineSnapshot:
    chat_turns = _append_chat_turn(workspace.chat_turns, event)
    return EngineSnapshot(
        workspace=CaseWorkspace(
            case_id=workspace.case_id,
            state=CaseState.ABANDONED,
            facts=workspace.facts,
            chat_turns=chat_turns,
            pending_clarification=None,
        ),
        actions=(NextAction(kind=ActionKind.ABANDON_CASE),),
    )


def _offer_draft(workspace: CaseWorkspace, event: IngestEvent) -> EngineSnapshot:
    eligible = workspace.draft_eligible_facts()
    chat_turns = _append_chat_turn(workspace.chat_turns, event)
    if not eligible:
        return EngineSnapshot(
            workspace=CaseWorkspace(
                case_id=workspace.case_id,
                state=workspace.state,
                facts=workspace.facts,
                chat_turns=chat_turns,
                pending_clarification=workspace.pending_clarification,
            ),
            actions=(
                NextAction(
                    kind=ActionKind.DRAFT_NOT_READY,
                    payload={"reason": "no_source_backed_facts"},
                ),
            ),
        )

    actions: tuple[NextAction, ...] = (
        NextAction(
            kind=ActionKind.OFFER_DRAFT,
            payload={"eligible_facts": str(len(eligible))},
        ),
    )
    if workspace.has_unconfirmed_stricter_facts():
        actions = actions + (
            NextAction(
                kind=ActionKind.REQUEST_FACT_CONFIRMATION,
                payload={"reason": "stricter_unconfirmed_excluded"},
            ),
        )

    return EngineSnapshot(
        workspace=CaseWorkspace(
            case_id=workspace.case_id,
            state=CaseState.DRAFT_READY,
            facts=workspace.facts,
            chat_turns=chat_turns,
            pending_clarification=workspace.pending_clarification,
        ),
        actions=actions,
    )


def _request_save(workspace: CaseWorkspace, event: IngestEvent) -> EngineSnapshot:
    eligible = workspace.draft_eligible_facts()
    chat_turns = _append_chat_turn(workspace.chat_turns, event)
    if workspace.state is not CaseState.DRAFT_READY or not eligible:
        return EngineSnapshot(
            workspace=CaseWorkspace(
                case_id=workspace.case_id,
                state=workspace.state,
                facts=workspace.facts,
                chat_turns=chat_turns,
                pending_clarification=workspace.pending_clarification,
            ),
            actions=(
                NextAction(
                    kind=ActionKind.DRAFT_NOT_READY,
                    payload={"reason": "save_requested_before_draft"},
                ),
            ),
        )
    return EngineSnapshot(
        workspace=CaseWorkspace(
            case_id=workspace.case_id,
            state=CaseState.SAVING,
            facts=workspace.facts,
            chat_turns=chat_turns,
            pending_clarification=workspace.pending_clarification,
        ),
        actions=(
            NextAction(
                kind=ActionKind.SAVE_DRAFT,
                payload={"eligible_facts": str(len(eligible))},
            ),
        ),
    )


def _record_chat_turn(
    workspace: CaseWorkspace, event: IngestEvent, action_kind: ActionKind
) -> EngineSnapshot:
    chat_turns = _append_chat_turn(workspace.chat_turns, event)
    return EngineSnapshot(
        workspace=CaseWorkspace(
            case_id=workspace.case_id,
            state=workspace.state,
            facts=workspace.facts,
            chat_turns=chat_turns,
            pending_clarification=workspace.pending_clarification,
        ),
        actions=(NextAction(kind=action_kind),),
    )


def _with_chat_turn(workspace: CaseWorkspace, event: IngestEvent) -> CaseWorkspace:
    return CaseWorkspace(
        case_id=workspace.case_id,
        state=workspace.state,
        facts=workspace.facts,
        chat_turns=_append_chat_turn(workspace.chat_turns, event),
        pending_clarification=workspace.pending_clarification,
    )


def _append_chat_turn(
    chat_turns: tuple[ChatTurn, ...], event: IngestEvent
) -> tuple[ChatTurn, ...]:
    return chat_turns + (
        ChatTurn(
            turn_id=event.turn_id,
            role="user",
            text=event.text,
            source_type=event.source_type,
        ),
    )


def _build_facts(event: IngestEvent) -> tuple[CaseFact, ...]:
    return tuple(
        CaseFact(
            key=key,
            value=value,
            source_type=event.source_type,
            source_turn_id=event.turn_id,
            confirmed=False,
        )
        for key, value in event.extracted_facts
    )


def _merge_facts(
    existing: tuple[CaseFact, ...], incoming: tuple[CaseFact, ...]
) -> tuple[CaseFact, ...]:
    if not incoming:
        return existing
    by_key: dict[str, CaseFact] = {fact.key: fact for fact in existing}
    for fact in incoming:
        by_key[fact.key] = fact
    return tuple(by_key.values())


def _fresh_case_id() -> str:
    return uuid.uuid4().hex
