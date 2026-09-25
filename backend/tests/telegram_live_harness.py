"""Shared Telethon harness for live Portfolio Guru workflow checks.

The harness is intentionally small: Telethon drives the bot as a real Telegram
user, captures a transcript, and applies scenario-level assertions. Heavier
visual proof belongs in OpenClaw QA Lab, not in this product repo.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

LIVE_APPROVAL_VALUE = "portfolio-guru-live-qa-approved"
DEFAULT_ALLOWED_BOT_USERNAMES = ("portfolio_guru_bot",)
FORBIDDEN_RESPONSE_MARKERS = (
    "traceback",
    "exception",
    "internal server error",
    "undefined",
    "null",
)


@dataclass(frozen=True)
class TelegramStep:
    name: str
    message: str
    expect_text_any: tuple[str, ...] = ()
    expect_button_any: tuple[str, ...] = ()
    click_button_any: tuple[str, ...] = ()
    expect_after_click_text_any: tuple[str, ...] = ()
    expect_after_click_button_any: tuple[str, ...] = ()
    forbid_text_any: tuple[str, ...] = FORBIDDEN_RESPONSE_MARKERS
    forbid_button_any: tuple[str, ...] = ()
    timeout_seconds: int = 90
    followup_limit: int = 4


@dataclass
class TelegramExchange:
    step: str
    action: str
    received: str
    buttons: list[str] = field(default_factory=list)
    clicked_button: str = ""
    message_id: int = 0
    direction: str = "in"
    controls: list[dict] = field(default_factory=list)


def telethon_env() -> dict[str, str]:
    """Return Telethon credentials, accepting both old and new env names."""
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "portfolio_guru_bot").lstrip("@")
    return {
        "session": os.environ.get("TELETHON_SESSION", ""),
        "api_id": os.environ.get("TELETHON_API_ID") or os.environ.get("TELEGRAM_API_ID", ""),
        "api_hash": os.environ.get("TELETHON_API_HASH") or os.environ.get("TELEGRAM_API_HASH", ""),
        "bot_username": bot_username,
        "approval": os.environ.get("TELEGRAM_LIVE_APPROVED", ""),
    }


def allowed_bot_usernames() -> set[str]:
    configured = os.environ.get("TELEGRAM_LIVE_ALLOWED_BOTS", "")
    names = configured.split(",") if configured else DEFAULT_ALLOWED_BOT_USERNAMES
    return {name.strip().lstrip("@") for name in names if name.strip()}


def normalise_bot_username(bot_username: str) -> str:
    return bot_username.strip().lstrip("@")


def assert_live_telegram_guardrails(bot_username: str | None = None) -> None:
    env = telethon_env()
    if env["approval"] != LIVE_APPROVAL_VALUE:
        raise RuntimeError(
            "Live Telegram QA is blocked until Moeed explicitly approves this run. "
            f"Set TELEGRAM_LIVE_APPROVED={LIVE_APPROVAL_VALUE} only after approval."
        )
    allowed = allowed_bot_usernames()
    if env["bot_username"] not in allowed:
        raise RuntimeError(
            f"Live Telegram QA target @{env['bot_username']} is not allowlisted. "
            f"Allowed targets: {', '.join(sorted(allowed))}"
        )
    if bot_username is not None:
        target = normalise_bot_username(bot_username)
        if target != env["bot_username"]:
            raise RuntimeError(f"Refusing to send live Telegram messages to @{target}")
        if target not in allowed:
            raise RuntimeError(
                f"Live Telegram QA target @{target} is not allowlisted. "
                f"Allowed targets: {', '.join(sorted(allowed))}"
            )


def has_telethon_env() -> bool:
    env = telethon_env()
    if not (env["session"] and env["api_id"] and env["api_hash"]):
        return False
    try:
        assert_live_telegram_guardrails()
    except RuntimeError:
        return False
    return True


def button_texts(message) -> list[str]:
    return [button.text for row in (getattr(message, "buttons", None) or []) for button in row]


def message_fingerprint(message) -> tuple[int, str, tuple[str, ...]]:
    """Identity of a message's visible content: id plus text plus button labels.

    Telegram bots often edit a message in place rather than sending a new one,
    so message-id ordering alone cannot tell a genuinely changed reply from the
    same pre-click message still sitting at the top of history. Comparing the
    full fingerprint catches both a same-id edit (fingerprint changes) and a
    stale unedited repeat (fingerprint matches the known "before" state).
    """
    return (
        getattr(message, "id", 0) or 0,
        (getattr(message, "raw_text", "") or "").strip(),
        tuple(button_texts(message)),
    )


_SLO_HEADER_RE = re.compile(r"SLO\s*(\d+)", re.IGNORECASE)
_KC_CHILD_RE = re.compile(r"^↳\s*KC\s*(\d+)\s*:", re.IGNORECASE)


def parse_visible_kc_selections(text: str) -> list[tuple[int, int]]:
    """Parse the rendered SLO->KC hierarchy into ordered (SLO number, KC number)
    pairs, matching `bot.py`'s `_format_curriculum_hierarchy` output exactly: a
    parent "• *SLOn — ...*" line followed by one or more child "  ↳ KCm: ..."
    lines that belong to the most recently seen parent. A child line with no
    parent yet seen (should not happen in real output) is dropped rather than
    silently attributed to the wrong SLO.

    Returns raw (possibly duplicate) pairs in document order; callers decide
    whether duplicates are acceptable for their assertion.
    """
    pairs: list[tuple[int, int]] = []
    current_slo: int | None = None
    for line in (text or "").splitlines():
        stripped = line.strip()
        child_match = _KC_CHILD_RE.match(stripped)
        if child_match:
            if current_slo is not None:
                pairs.append((current_slo, int(child_match.group(1))))
            continue
        if stripped.startswith("•"):
            header_match = _SLO_HEADER_RE.search(stripped)
            current_slo = int(header_match.group(1)) if header_match else None
    return pairs


READY_DRAFT_BUTTON_TOKEN = "save to kaizen"
MISSING_ESSENTIALS_TEXT_MARKER = "before i draft this, i still need"


def classify_post_click_draft_state(message) -> str:
    """Classify the message the bot shows right after a form-choice click.

    The only two bounded states this journey is allowed to see are the ready
    draft (`_build_approval_keyboard` in bot.py: a Save button + "Cancel") and
    the retired missing-essentials prompt. Since 25 Sep 2026 missing details
    are listed inside the ready draft, so a live run should only ever see
    "ready"; the second state stays recognised so an old prompt fails
    loudly rather than being guessed at. Anything else — including a ready-looking message
    that also carries the missing-essentials marker, or a missing-essentials
    message with extra buttons — fails closed rather than being guessed at.
    This is deliberately not a general flow engine: it recognises exactly
    these two states and nothing further.

    Returns "ready" or "missing_essentials"; raises AssertionError otherwise.
    """
    received_lower = (getattr(message, "raw_text", "") or "").lower()
    buttons = button_texts(message)
    buttons_lower = [b.lower() for b in buttons]

    is_ready = any(READY_DRAFT_BUTTON_TOKEN in b for b in buttons_lower)
    is_missing_essentials = MISSING_ESSENTIALS_TEXT_MARKER in received_lower

    if is_ready and not is_missing_essentials:
        return "ready"

    if is_missing_essentials and not is_ready:
        cancel_only = bool(buttons_lower) and all("cancel" in b for b in buttons_lower)
        if not cancel_only:
            raise AssertionError(
                "the missing-essentials prompt must offer Cancel only; got "
                f"buttons {buttons!r}"
            )
        return "missing_essentials"

    raise AssertionError(
        "unexpected post-click state: neither the ready draft nor the bounded "
        f"missing-essentials prompt; text={getattr(message, 'raw_text', '')!r} buttons={buttons!r}"
    )


def _contains_any(value: str, tokens: tuple[str, ...]) -> bool:
    return any(token.lower() in value.lower() for token in tokens)


def _button_matches(button_text: str, tokens: tuple[str, ...]) -> bool:
    return any(token.lower() in button_text.lower() for token in tokens)


def _find_button(buttons, tokens: tuple[str, ...]):
    for row in buttons or []:
        for button in row:
            if _button_matches(getattr(button, "text", ""), tokens):
                return button
    return None


def _matches_expectation(message, step: TelegramStep) -> bool:
    received = getattr(message, "raw_text", "") or ""
    buttons = button_texts(message)
    text_ok = not step.expect_text_any or _contains_any(received, step.expect_text_any)
    buttons_ok = not step.expect_button_any or any(
        _button_matches(button, step.expect_button_any) for button in buttons
    )
    forbidden_text_ok = not step.forbid_text_any or not _contains_any(received, step.forbid_text_any)
    forbidden_buttons_ok = not step.forbid_button_any or not any(
        _button_matches(button, step.forbid_button_any) for button in buttons
    )
    return bool(received.strip()) and text_ok and buttons_ok and forbidden_text_ok and forbidden_buttons_ok


def _assert_message_matches(message, step: TelegramStep, phase: str) -> None:
    received = getattr(message, "raw_text", "") or ""
    buttons = button_texts(message)
    assert received.strip(), f"{step.name} {phase}: bot returned empty text"
    if step.expect_text_any:
        assert _contains_any(received, step.expect_text_any), (
            f"{step.name} {phase}: expected one of {step.expect_text_any!r}; got {received!r}"
        )
    if step.expect_button_any:
        assert any(_button_matches(button, step.expect_button_any) for button in buttons), (
            f"{step.name} {phase}: expected one of {step.expect_button_any!r}; got buttons {buttons!r}"
        )
    if step.forbid_text_any:
        assert not _contains_any(received, step.forbid_text_any), (
            f"{step.name} {phase}: response leaked forbidden marker; got {received!r}"
        )
    if step.forbid_button_any:
        assert not any(_button_matches(button, step.forbid_button_any) for button in buttons), (
            f"{step.name} {phase}: forbidden button present; got buttons {buttons!r}"
        )


def _click_expectation_step(step: TelegramStep) -> TelegramStep:
    return TelegramStep(
        name=step.name,
        message=step.message,
        expect_text_any=step.expect_after_click_text_any,
        expect_button_any=step.expect_after_click_button_any,
        forbid_text_any=step.forbid_text_any,
        forbid_button_any=step.forbid_button_any,
        timeout_seconds=step.timeout_seconds,
        followup_limit=step.followup_limit,
    )


# Deliberately narrow: real network hiccups a poll loop should just retry,
# not every possible exception. A bug in the matching logic below (a bad
# assertion, an AttributeError from a malformed fake in a unit test, a
# harness programming error) must propagate immediately rather than being
# silently reinterpreted as "the message never arrived".
_TRANSIENT_POLL_ERRORS = (ConnectionError, TimeoutError, OSError)


async def wait_for_matching_message(
    client,
    chat_id: str | int,
    timeout_seconds: int,
    expect_text_any: tuple[str, ...] = (),
    expect_buttons: bool = False,
    expect_button_any: tuple[str, ...] = (),
    forbid_text_any: tuple[str, ...] = FORBIDDEN_RESPONSE_MARKERS,
    min_id: int | None = None,
    reject_fingerprint: tuple[int, str, tuple[str, ...]] | None = None,
    known_fingerprints=None, observe=None, strict=False, poll_interval=0.5,
) -> any:
    """Poll recent chat history until a message matches text/buttons expectations.

    `reject_fingerprint`, when given, excludes a message whose full
    (id, text, buttons) fingerprint equals a known "before" state — the
    pre-click message a caller already read — even if it happens to satisfy
    the text/button expectations. This is a stronger guard than `min_id`
    alone, which cannot tell an edited-in-place reply (same id) from the same
    stale message still being returned by the API.

    Only `_TRANSIENT_POLL_ERRORS` from the `get_messages` call itself are
    swallowed and retried; any other exception — including one raised while
    evaluating a match — propagates immediately instead of being masked as a
    plain timeout.
    """
    import asyncio
    import time
    start_time = time.time()
    previous_candidate = None

    while time.time() - start_time < timeout_seconds:
        try:
            messages = await client.get_messages(chat_id, limit=50 if strict else 5)
        except _TRANSIENT_POLL_ERRORS:
            if strict:
                raise
            await asyncio.sleep(poll_interval)
            continue

        if observe:
            observe(messages)
        if strict and known_fingerprints is not None and len(messages) >= 50:
            assert not all(exploration_fingerprint(m) not in known_fingerprints for m in messages), "Response history ceiling"
        for msg in messages:
            if known_fingerprints is not None:
                ids = {fp[0] for fp in known_fingerprints}
                if (exploration_fingerprint(msg) in known_fingerprints or
                    (msg.id not in ids and msg.id <= max(ids, default=0))):
                    continue
            if msg.out:
                continue
            if min_id is not None and getattr(msg, "id", 0) < min_id:
                if not (strict and known_fingerprints is not None and msg.id in ids):
                    continue
            if reject_fingerprint is not None and message_fingerprint(msg) == reject_fingerprint:
                continue
            received = getattr(msg, "raw_text", "") or ""
            buttons = button_texts(msg)
            if strict and re.search(r"(?:scanning|analysing|reviewing|processing|updating).*(?:…|\.\.\.)", received, re.I):
                continue

            text_ok = not expect_text_any or _contains_any(received, expect_text_any)
            buttons_ok = not expect_buttons or bool(buttons or msg.reply_markup)
            button_text_ok = not expect_button_any or any(
                _button_matches(button, expect_button_any) for button in buttons
            )
            forbidden_text_ok = not forbid_text_any or not _contains_any(received, forbid_text_any)

            if strict and not forbidden_text_ok:
                raise AssertionError("Forbidden response content")
            if received.strip() and text_ok and buttons_ok and button_text_ok and forbidden_text_ok:
                candidate = exploration_fingerprint(msg) if strict else None
                if not strict or candidate == previous_candidate:
                    return msg
                previous_candidate = candidate
                break
        await asyncio.sleep(poll_interval)

    raise TimeoutError(
        f"Timed out waiting for message in chat {chat_id} "
        f"matching text {expect_text_any} and buttons {expect_button_any} "
        f"(buttons expected: {expect_buttons})"
    )


async def _wait_for_matching_response(conv, step: TelegramStep):
    try:
        return await wait_for_matching_message(
            conv.client,
            conv.input_chat,
            step.timeout_seconds,
            expect_text_any=step.expect_text_any,
            expect_buttons=bool(step.expect_button_any),
            expect_button_any=step.expect_button_any,
            forbid_text_any=step.forbid_text_any,
        )
    except TimeoutError:
        return await conv.get_response(timeout=1)


async def run_telegram_workflow(client, bot_username: str, steps: Iterable[TelegramStep], *, strict=False) -> list[TelegramExchange]:
    """Run a scenario against the live bot and return a transcript."""
    bot_username = normalise_bot_username(bot_username)
    assert_live_telegram_guardrails(bot_username)
    transcript: list[TelegramExchange] = []
    async with client.conversation(bot_username, timeout=max(step.timeout_seconds for step in steps)) as conv:
        for step in steps:
            sent = await conv.send_message(step.message)
            reply = await wait_for_matching_message(
                client,
                bot_username,
                step.timeout_seconds,
                expect_text_any=step.expect_text_any,
                expect_buttons=bool(step.expect_button_any),
                expect_button_any=step.expect_button_any,
                forbid_text_any=step.forbid_text_any,
                min_id=getattr(sent, "id", None), strict=strict,
            )
            exchange = TelegramExchange(
                step=step.name,
                action=f"send:{step.message}",
                received=reply.raw_text or "",
                buttons=button_texts(reply),
            )
            _assert_message_matches(reply, step, "after send")
            transcript.append(exchange)
            if step.click_button_any:
                button = _find_button(getattr(reply, "buttons", None), step.click_button_any)
                assert button is not None, (
                    f"{step.name}: expected clickable button matching {step.click_button_any!r}; "
                    f"got buttons {exchange.buttons!r}"
                )
                clicked_text = button.text
                before_click_fingerprint = message_fingerprint(reply)
                await button.click()
                click_step = _click_expectation_step(step)
                followup = await wait_for_matching_message(
                    client,
                    bot_username,
                    click_step.timeout_seconds,
                    expect_text_any=click_step.expect_text_any,
                    expect_buttons=bool(click_step.expect_button_any),
                    expect_button_any=click_step.expect_button_any,
                    forbid_text_any=click_step.forbid_text_any,
                    min_id=getattr(reply, "id", None),
                    reject_fingerprint=before_click_fingerprint, strict=strict,
                )
                _assert_message_matches(followup, click_step, f"after clicking {clicked_text!r}")
                transcript.append(
                    TelegramExchange(
                        step=step.name,
                        action="click_button",
                        received=followup.raw_text or "",
                        buttons=button_texts(followup),
                        clicked_button=clicked_text,
                    )
                )
    write_transcript_artifact(transcript)
    return transcript


def assert_transcript_is_sensible(transcript: list[TelegramExchange]) -> None:
    """Low-cost semantic guard for the live workflow."""
    assert transcript, "No Telegram transcript captured"
    combined = "\n".join(exchange.received for exchange in transcript).lower()
    assert not any(marker in combined for marker in FORBIDDEN_RESPONSE_MARKERS), combined
    clicked = [exchange for exchange in transcript if exchange.action == "click_button"]
    if clicked:
        assert all(exchange.clicked_button for exchange in clicked), "Button click transcript missing button label"


def write_transcript_artifact(transcript: list[TelegramExchange]) -> None:
    from tests.whole_bot_identity import live_transcript
    artifact_dir = os.environ.get("TELEGRAM_E2E_ARTIFACT_DIR")
    if not artifact_dir:
        return
    path = Path(artifact_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / "portfolio-guru-telegram-transcript.json").write_text(
        json.dumps(live_transcript(redact_exploration([asdict(exchange) for exchange in transcript],
                                     (telethon_env()["session"], telethon_env()["api_hash"]))), indent=2),
        encoding="utf-8",
    )


@dataclass(frozen=True)
class ExplorationLimits:
    routes: int = 300
    depth: int = 8
    actions: int = 2000
    seconds: float = 900
    response_seconds: float = 45
    poll_interval: float = 0.25


def message_controls(message):
    controls = []
    for row in getattr(message, "buttons", None) or []:
        for button in row:
            payload = getattr(button, "data", None)
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", errors="strict")
            controls.append({"label": str(button.text), "payload": payload or "",
                             "url": getattr(button, "url", None) or ""})
    return controls


def exploration_fingerprint(message):
    # Preserve the existing id/text/label fingerprint; destinations distinguish
    # same-id edits where only the callback or URL changed.
    return (*message_fingerprint(message), tuple(
        (c["payload"], c["url"]) for c in message_controls(message)))


def semantic_message_state(message):
    return (message_fingerprint(message)[1], tuple(
        (c["label"], c["payload"], c["url"]) for c in message_controls(message)))


def redact_exploration(value, secrets=()):
    """Redact before writing; no credential lookup and no raw exception strings."""
    if isinstance(value, dict):
        return {k: redact_exploration(v, secrets) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact_exploration(v, secrets) for v in value]
    if not isinstance(value, str):
        return value
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        value = value.replace(secret, "[redacted]")
    value = re.sub(r"https?://[^\s]+", "[url]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", value)
    value = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]+\b", "[token]", value)
    value = re.sub(r"(?<![A-Za-z0-9])\+?\d[\d ()-]{7,}\d(?![A-Za-z0-9])", "[number]", value)
    return value


async def explore_whole_bot(client, bot_username, catalogue, artifact_dir, *,
                           expected_user_id, limits=ExplorationLimits(), secrets=()):
    """Bounded replay exploration using the existing client, poller and transcript.

    /cancel resets transient state; /reset and /delete are never sent. A pass
    proves the reachable safe graph for this account, not the still-open offline
    semantic catalogue or arbitrary language/model behaviour.
    """
    import asyncio
    import hashlib
    import time
    from collections import deque
    from tests.telegram_live_policy import command_roots, control_policy, COMMAND_POLICY

    path = Path(artifact_dir)
    transcript = []
    receipt = {"schema": 1, "layer": "live", "status": "pending", "overall_status": "pending",
               "registration_digest": catalogue.get("digest"), "commands": {}, "routes": [],
               "protected": [], "loops": [], "failures": [], "actions": 0,
               "scope": "Safe registered commands and their reachable controls for the bound synthetic account"}
    known = {}
    latest = {}
    screens = []
    read_outbound = set()
    sent_any = False
    clean = False
    deadline = time.monotonic() + limits.seconds

    def persist():
        from tests.whole_bot_identity import live_transcript, current_binding
        if os.environ.get("WHOLE_BOT_LIVE_PROOF") == "1":
            receipt.update(current_binding())
        path.mkdir(parents=True, exist_ok=True)
        (path / "whole-bot-transcript.json").write_text(json.dumps(
            live_transcript(redact_exploration([asdict(t) for t in transcript], secrets)), sort_keys=True, indent=2) + "\n")
        (path / "whole-bot-coverage.json").write_text(json.dumps(
            redact_exploration(receipt, secrets), sort_keys=True, indent=2) + "\n")

    def state_id(message):
        return hashlib.sha256(json.dumps(semantic_message_state(message)).encode()).hexdigest()

    def observe(messages):
        for msg in reversed(messages):
            fp = exploration_fingerprint(msg)
            if known.get(msg.id) == fp or (msg.id not in known and msg.id <= max(known, default=0)):
                continue
            known[msg.id] = fp
            latest[msg.id] = msg
            direction = "out" if msg.out else "in"
            if msg.out:
                read_outbound.add(msg.id)
            transcript.append(TelegramExchange("whole-bot", "observe", msg.raw_text or "",
                button_texts(msg), message_id=msg.id, direction=direction, controls=message_controls(msg)))
            persist()  # Retain the last observed failure, not just happy-path output.

    async def action(conv, message=None, payload=None, source=None):
        nonlocal sent_any, clean, screens
        if receipt["actions"] >= limits.actions:
            raise AssertionError("Action ceiling")
        if time.monotonic() >= deadline:
            raise TimeoutError("Exploration deadline exhausted")
        assert_live_telegram_guardrails(bot_username)
        before = set(known.values())
        receipt["actions"] += 1
        clean = False
        if message is not None:
            sent_any = True
            transcript.append(TelegramExchange("whole-bot", "attempt_send:" + message, "", direction="out"))
            persist()
            sent = await conv.send_message(message)
            minimum = sent.id
            transcript.append(TelegramExchange("whole-bot", "send:" + message, "", direction="out", message_id=sent.id))
        else:
            assert control_policy(payload, "", catalogue) == "safe", "Protected click refused"
            matches = [b for row in (source.buttons or []) for b in row
                       if (b.data.decode() if isinstance(b.data, bytes) else b.data) == payload]
            assert len(matches) == 1, "Ambiguous or missing control"
            transcript.append(TelegramExchange("whole-bot", "click_button", "",
                clicked_button=matches[0].text, controls=[{"payload": payload}]))
            minimum = None  # A reply may edit any already-known message in place.
            answer = await matches[0].click()
            if payload == "FORM|disabled":
                text = getattr(answer, "message", "") or ""
                assert "coming soon" in text.lower(), "Disabled control acknowledgement missing"
                transcript.append(TelegramExchange("whole-bot", "callback_answer", text, clicked_button=matches[0].text))
                screens = [source]
                persist()
                return source
        persist()
        from tests.telegram_live_policy import command_expectation
        reply = await wait_for_matching_message(client, bot_username, min(limits.response_seconds, max(0, deadline - time.monotonic())),
            expect_text_any=command_expectation(message[1:]) if message else (), min_id=minimum, known_fingerprints=before, observe=observe, strict=True,
            poll_interval=limits.poll_interval)
        if message is not None:
            assert sent.id in read_outbound, "Outbound message was not read back"
        screens = [m for _, m in sorted(latest.items()) if not m.out
                   and exploration_fingerprint(m) not in before and (m.id == reply.id or message_controls(m))]
        for screen in screens:
            controls = message_controls(screen)
            for control in controls:
                control_policy(control["payload"], control["url"], catalogue)
            if MISSING_ESSENTIALS_TEXT_MARKER in (screen.raw_text or "").lower():
                assert not any(c["payload"].startswith("APPROVE|") for c in controls), "Contradictory draft state"
        return reply

    async def reset(conv):
        nonlocal clean
        reply = await action(conv, message="/cancel")
        assert re.search(r"\bcancelled\b|\bcanceled\b", reply.raw_text, re.I), "Clean cancellation unproved"
        assert not any(c["payload"].startswith("APPROVE|") for c in message_controls(reply)), "Contradictory reset"
        clean = True

    async def traverse():
        nonlocal clean
        roots = command_roots(catalogue["commands"])
        receipt["commands"] = {c: "pending" if COMMAND_POLICY[c] == "safe" else
                               "protected-command-not-invoked" for c in sorted(catalogue["commands"])}
        assert min(limits.routes, limits.depth, limits.actions, limits.seconds, limits.response_seconds) > 0
        assert expected_user_id and (await client.get_me()).id == expected_user_id, "Synthetic account mismatch"
        baseline = await client.get_messages(bot_username, limit=50)
        known.update((m.id, exploration_fingerprint(m)) for m in baseline)  # Do not archive prior chat history.
        queue = deque((root, ()) for root in roots)
        expanded = set()
        async with client.conversation(bot_username, timeout=limits.response_seconds,
                                       max_messages=max(100, limits.actions * 4)) as conv:
            try:
                while queue:
                    assert len(receipt["routes"]) < limits.routes, "Route ceiling"
                    root, route = queue.popleft()
                    assert len(route) <= limits.depth, "Depth ceiling"
                    await reset(conv)
                    reply = await action(conv, message="/" + root)
                    receipt["commands"][root] = "observed"
                    for payload, expected_source in route:
                        sources = [m for m in screens if state_id(m) == expected_source]
                        assert len(sources) == 1, "Replay state changed or ambiguous"
                        reply = await action(conv, payload=payload, source=sources[0])
                    for screen in screens:
                        assert len(receipt["routes"]) < limits.routes, "Route ceiling"
                        state = state_id(screen)
                        receipt["routes"].append({"root": root, "path": [p for p, _ in route],
                                                  "destination": state, "status": "observed"})
                        key = (root, state)
                        if key in expanded:
                            receipt["loops"].append({"root": root, "state": state})
                            continue
                        expanded.add(key)
                        for control in message_controls(screen):
                            effect = control_policy(control["payload"], control["url"], catalogue)
                            if effect == "protected":
                                receipt["protected"].append({"root": root, "state": state, **control,
                                                             "status": "protected-boundary-reached"})
                            else:
                                assert len(route) < limits.depth, "Depth ceiling before complete traversal"
                                assert len(queue) < limits.routes, "Route ceiling"
                                queue.append((root, (*route, (control["payload"], state))))
                    persist()
            except BaseException as exc:
                receipt["failures"].append({"stage": "traversal", "type": type(exc).__name__})
                raise
            finally:
                if sent_any and not clean:
                    try:
                        await reset(conv)
                    except BaseException as exc:
                        receipt["failures"].append({"stage": "cancellation", "type": type(exc).__name__})
                        raise
        assert receipt["routes"] and transcript and clean, "Incomplete traversal or transcript"

    try:
        persist()  # Fail before sending if evidence cannot be retained.
        assert_live_telegram_guardrails(bot_username)
        async with asyncio.timeout(limits.seconds):
            await traverse()
        receipt["status"] = "passed"
    except BaseException as exc:
        safe_reasons = {"Action ceiling", "Depth ceiling", "Route ceiling", "Depth ceiling before complete traversal",
            "Unknown callback control requires policy review", "Unknown URL control", "Clean cancellation unproved",
            "Replay state changed or ambiguous", "Outbound message was not read back", "Synthetic account mismatch",
            "Contradictory draft state", "Contradictory reset", "Forbidden response content", "Response history ceiling"}
        receipt["failures"].append({"type": type(exc).__name__,
            "reason": str(exc) if str(exc) in safe_reasons else "Guard, transport, deadline or evidence failure"})
        persist()
        raise
    finally:
        persist()
    return receipt
