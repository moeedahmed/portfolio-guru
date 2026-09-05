#!/usr/bin/env python3
"""Local release-card and rollback-state store for scripts/release_loop.sh.

One prepared card per full release SHA, kept under a gitignored `.release/`
directory. The card is what a single operator approval actually covers: exact
SHA, surface, risk, one-line effect, how the named live proof will be obtained,
the exact live target and the frozen singleton allowlist of recipients that
proof may touch, the pre-deploy known-good SHA to roll back to, the rollback
parent (the release itself), that rollback is operator-triggered rather than
silent, the exclusions the approval never extends to, and the absolute paths of
the git/python/bash binaries the printed bootstrap command will run.
ship/resume/attest/rollback refuse anything that drifted from it.

Schema 3 binds the approval to the *content* of the card, not just its SHA.
`canonical_bytes` is the one serialisation of a card — every JSON field, keys
sorted, compact separators, ASCII-only escapes, UTF-8, one trailing newline —
and `card_digest` is the SHA-256 of those bytes. An approval token is
`<sha>:<digest>`; a card whose canonical digest is not the approved digest is
refused even when every field still validates. A bare SHA approval (schema 2)
is a legacy form and fails closed with a message that says so.

A prepared card is immutable for its SHA. `compare` answers the only question
prepare needs before writing: is this exactly the card that already exists? A
re-prepare of the same commit reuses it; anything that would change what the
approval covers is refused rather than silently rewritten under an approval that
was given for different content.

Alongside the card, `rollback-write`/`rollback-export` keep one small state file
per released SHA. Rollback no longer touches the checkout or any local ref, and
its commit is deterministic (fixed identity, the released commit's own date), so
this journal is a progress record — `committed`, `pushed`, `proved` — rather
than something recovery depends on. Recovery reads Git itself: a commit already
on `main` with exactly the released parent and the known-good tree is reused,
never duplicated, whether or not the journal got written before an
interruption.

Stdlib only. No network, no git, no credentials. Content is written by the
release loop from operator-supplied text and is validated here so a token,
patient detail, or multi-line paste cannot land in it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

SCHEMA_VERSION = 3
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
APPROVAL_TOKEN = re.compile(r"^(?P<sha>[0-9a-f]{40}):(?P<digest>[0-9a-f]{64})$")
SURFACES = ("telegram",)
RISKS = ("internal", "telegram", "broad")
PROOF_MODES = ("automated", "manual")
ROLLBACK_MODES = ("operator-triggered",)
ROLLBACK_STATUSES = ("committed", "pushed", "proved")
RESULTS = ("pass", "fail")
MAX_TEXT = 200
MAX_PATH = 1024
TARGET = re.compile(r"^[A-Za-z0-9_]{3,64}$")

# Cheap, deliberately narrow shapes. This is a guard against an accidental
# paste, not a secret scanner: the real rule is that cards never carry secrets.
SECRET_SHAPES = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{10,}"),
    re.compile(r"\d{6,}:[A-Za-z0-9_-]{20,}"),
    re.compile(r"\S{40,}"),
)

CARD_ORDER = (
    "schema_version",
    "sha",
    "surface",
    "risk",
    "effect",
    "proof_mode",
    "live_target",
    "live_allowlist",
    "known_good_sha",
    "rollback_parent_sha",
    "rollback_mode",
    "exclusions",
    "bootstrap_git",
    "bootstrap_python",
    "bootstrap_bash",
    "created_at",
)

# Everything on the card except when it was written. Two prepares of the same
# commit differ in timestamp and in nothing else; a difference anywhere in this
# tuple changes what approving that SHA would mean. (The digest, by contrast,
# covers every field including created_at: it names one file's exact content.)
COMPARED_FIELDS = tuple(field for field in CARD_ORDER if field != "created_at")

ROLLBACK_ORDER = (
    "schema_version",
    "released_sha",
    "known_good_sha",
    "rollback_sha",
    "surface",
    "risk",
    "status",
    "created_at",
    "updated_at",
)


class CardError(Exception):
    """Any refusal that must stop the release loop before it mutates anything."""


def fail(message: str) -> NoReturn:
    raise CardError(message)


def check_sha(value: str, label: str) -> str:
    lowered = (value or "").strip().lower()
    if not FULL_SHA.fullmatch(lowered):
        fail(f"{label} must be a full 40-character lowercase hexadecimal SHA")
    return lowered


def check_text(value: str, label: str) -> str:
    text = (value or "").strip()
    if not text:
        fail(f"{label} must be a non-empty single line")
    if "\n" in text or "\r" in text:
        fail(f"{label} must be a single line")
    if len(text) > MAX_TEXT:
        fail(f"{label} must be at most {MAX_TEXT} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        fail(f"{label} must not contain control characters")
    for shape in SECRET_SHAPES:
        if shape.search(text):
            fail(f"{label} looks like it contains a credential or opaque blob; rewrite it in plain words")
    return text


def check_path(value: str, label: str) -> str:
    """An absolute executable path frozen at prepare time.

    Not `check_text`: a legitimate interpreter path is easily longer than the
    opaque-blob threshold. It still may not be relative, empty, multi-line or
    carry control characters, because it is printed into a command the operator
    is asked to run verbatim.
    """
    path = value or ""
    if not path or path != path.strip():
        fail(f"{label} must be a non-empty path without surrounding whitespace")
    if "\n" in path or "\r" in path or any(ord(char) < 32 or ord(char) == 127 for char in path):
        fail(f"{label} must be a single line without control characters")
    if len(path) > MAX_PATH:
        fail(f"{label} must be at most {MAX_PATH} characters")
    if not os.path.isabs(path):
        fail(f"{label} must be an absolute path")
    return path


def check_choice(value: str, label: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        fail(f"{label} must be one of: {', '.join(allowed)}")
    return value


def check_target(value: str) -> str:
    target = (value or "").strip().lstrip("@")
    if not TARGET.fullmatch(target):
        fail("live target must be an exact bot username (letters, digits, underscore)")
    return target


def canonical_bytes(card: dict) -> bytes:
    """The one serialisation an approval digest is computed over.

    Sorted keys, compact separators, ASCII-only escapes, UTF-8, one trailing
    newline. Whitespace or key order in the file on disk never changes the
    digest; any change to any field's value does.
    """
    return (json.dumps(card, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def card_digest(card: dict) -> str:
    return hashlib.sha256(canonical_bytes(card)).hexdigest()


def approval_token(card: dict) -> str:
    return f"{card['sha']}:{card_digest(card)}"


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".card-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _check_allowlist(value, live_target: str | None) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        fail("live allowlist must be a list of bot usernames")
    names = [check_target(item) for item in value]
    if live_target is None:
        if names:
            fail("only telegram-risk cards may name allowed live recipients")
        return []
    if names != [live_target]:
        fail("live allowlist must be exactly the one approved live target")
    return names


def load_card(path: Path) -> dict:
    if not path.exists():
        fail(f"no release card at {path}; run --mode prepare first")
    try:
        card = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"release card is unreadable: {exc}")
    if not isinstance(card, dict):
        fail("release card is not an object")
    if set(card) != set(CARD_ORDER):
        fail("release card fields do not exactly match the supported schema")
    if card.get("schema_version") != SCHEMA_VERSION:
        fail(f"release card schema {card.get('schema_version')!r} != supported {SCHEMA_VERSION}")
    sha = check_sha(card.get("sha", ""), "card sha")
    check_choice(card.get("surface", ""), "card surface", SURFACES)
    check_choice(card.get("risk", ""), "card risk", RISKS)
    proof_mode = check_choice(card.get("proof_mode", ""), "card proof mode", PROOF_MODES)
    check_text(card.get("effect", ""), "card effect")
    live_target = check_target(card["live_target"]) if card.get("live_target") else None
    _check_allowlist(card.get("live_allowlist"), live_target)
    check_sha(card.get("known_good_sha", ""), "card known-good sha")
    parent = check_sha(card.get("rollback_parent_sha", ""), "card rollback parent sha")
    if parent != sha:
        fail("card rollback parent must be the released SHA itself")
    check_choice(card.get("rollback_mode", ""), "card rollback mode", ROLLBACK_MODES)
    for key in ("bootstrap_git", "bootstrap_python", "bootstrap_bash"):
        check_path(card.get(key, ""), f"card {key.replace('_', ' ')}")
    check_text(card.get("created_at", ""), "card created timestamp")
    if not isinstance(card.get("exclusions"), list) or not card["exclusions"]:
        fail("release card is missing its exclusions")
    for exclusion in card["exclusions"]:
        check_text(exclusion, "card exclusion")
    risk = card["risk"]
    if risk == "telegram" and live_target is None:
        fail("telegram card is missing its exact live target")
    if risk != "telegram" and live_target is not None:
        fail("only telegram-risk cards may name a live target")
    if risk == "internal" and proof_mode != "automated":
        fail("internal-risk cards must use automated proof")
    if risk == "broad" and proof_mode != "manual":
        fail("broad-risk cards must use manual proof")
    return card


def build_card(args: argparse.Namespace) -> dict:
    live_target = check_target(args.live_target) if args.live_target else None
    risk = check_choice(args.risk, "risk", RISKS)
    proof_mode = check_choice(args.proof_mode, "proof mode", PROOF_MODES)
    if risk == "telegram" and live_target is None:
        fail("telegram risk requires an exact --live-target")
    if risk != "telegram" and live_target is not None:
        fail("only telegram risk may name a live target")
    if risk == "internal" and proof_mode != "automated":
        fail("internal risk requires automated proof")
    if risk == "broad" and proof_mode != "manual":
        fail("broad risk requires manual proof")
    exclusions = [item.strip() for item in args.exclusions.split(",") if item.strip()]
    if not exclusions:
        fail("--exclusions must list at least one excluded action")
    for exclusion in exclusions:
        check_text(exclusion, "exclusion")
    sha = check_sha(args.sha, "sha")
    return {
        "schema_version": SCHEMA_VERSION,
        "sha": sha,
        "surface": check_choice(args.surface, "surface", SURFACES),
        "risk": risk,
        "effect": check_text(args.effect, "effect"),
        "proof_mode": proof_mode,
        "live_target": live_target,
        # Frozen singleton: the one recipient a live proof of this card may
        # ever touch. Derived, not supplied, so it cannot disagree with the target.
        "live_allowlist": [live_target] if live_target else [],
        "known_good_sha": check_sha(args.known_good_sha, "known-good sha"),
        "rollback_parent_sha": sha,
        "rollback_mode": check_choice(args.rollback_mode, "rollback mode", ROLLBACK_MODES),
        "exclusions": exclusions,
        "bootstrap_git": check_path(args.bootstrap_git, "bootstrap git"),
        "bootstrap_python": check_path(args.bootstrap_python, "bootstrap python"),
        "bootstrap_bash": check_path(args.bootstrap_bash, "bootstrap bash"),
        "created_at": check_text(args.created_at, "created timestamp"),
    }


def load_rollback(path: Path) -> dict:
    """Validate the small per-release rollback progress record.

    A rollback that reconciles itself must not be able to reconcile towards a
    SHA nobody approved, so every field is re-checked on read and the three SHAs
    must stay distinct.
    """
    if not path.exists():
        fail(f"no rollback state at {path}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"rollback state is unreadable: {exc}")
    if not isinstance(state, dict):
        fail("rollback state is not an object")
    if set(state) != set(ROLLBACK_ORDER):
        fail("rollback state fields do not exactly match the supported schema")
    if state.get("schema_version") != SCHEMA_VERSION:
        fail(f"rollback state schema {state.get('schema_version')!r} != supported {SCHEMA_VERSION}")
    released = check_sha(state.get("released_sha", ""), "rollback released sha")
    known_good = check_sha(state.get("known_good_sha", ""), "rollback known-good sha")
    rollback = check_sha(state.get("rollback_sha", ""), "rollback commit sha")
    if len({released, known_good, rollback}) != 3:
        fail("rollback state must name three distinct SHAs: released, known-good and rollback commit")
    check_choice(state.get("surface", ""), "rollback surface", SURFACES)
    check_choice(state.get("risk", ""), "rollback risk", RISKS)
    check_choice(state.get("status", ""), "rollback status", ROLLBACK_STATUSES)
    check_text(state.get("created_at", ""), "rollback created timestamp")
    check_text(state.get("updated_at", ""), "rollback updated timestamp")
    return state


def build_rollback(args: argparse.Namespace) -> dict:
    released = check_sha(args.released_sha, "released sha")
    known_good = check_sha(args.known_good_sha, "known-good sha")
    rollback = check_sha(args.rollback_sha, "rollback commit sha")
    if len({released, known_good, rollback}) != 3:
        fail("released, known-good and rollback commit SHAs must all differ")
    return {
        "schema_version": SCHEMA_VERSION,
        "released_sha": released,
        "known_good_sha": known_good,
        "rollback_sha": rollback,
        "surface": check_choice(args.surface, "surface", SURFACES),
        "risk": check_choice(args.risk, "risk", RISKS),
        "status": check_choice(args.status, "status", ROLLBACK_STATUSES),
        "created_at": check_text(args.created_at, "created timestamp"),
        "updated_at": check_text(args.updated_at, "updated timestamp"),
    }


def render(card: dict, *, ship_command: str = "", rollback_command: str = "") -> str:
    proof = card["proof_mode"]
    if card.get("live_target"):
        proof = f"{proof} (live target @{card['live_target']})"
    allowlist = ", ".join(f"@{name}" for name in card["live_allowlist"]) or "none (no live recipient)"
    lines = [
        "RELEASE CARD — one card, one approval",
        f"  schema        {card['schema_version']}",
        f"  sha           {card['sha']}",
        f"  digest        {card_digest(card)}",
        f"  surface       {card['surface']}",
        f"  risk          {card['risk']}",
        f"  effect        {card['effect']}",
        f"  proof mode    {proof}",
        f"  recipients    {allowlist}",
        f"  known good    {card['known_good_sha']} (verified live before approval)",
        f"  exclusions    {'; '.join(card['exclusions'])}",
        f"  bootstrap     git={card['bootstrap_git']} python={card['bootstrap_python']} bash={card['bootstrap_bash']}",
        f"  created       {card['created_at']}",
        "  covers        push of this exact SHA to main, CI Tests, Mac Mini deploy, runtime identity,",
        "                the named proof above, unchanged proof resume, and bounded targeted rollback",
        "                to the known-good SHA if that proof fails.",
        f"  rollback      {card['rollback_mode']}; never silent. Rolling {card['sha'][:12]} back to the",
        f"                known-good tree {card['known_good_sha'][:12]} needs no second approval, and",
        "                runs only when the operator runs the exact command below. The rollback",
        f"                commit has exactly one parent, {card['rollback_parent_sha'][:12]}; HEAD stays there.",
        "  not covered   anything in exclusions, and any change of SHA, target, recipient, risk,",
        "                surface, effect, proof mode, rollback target or bootstrap path — each needs",
        "                a new card and a new approval. The approval token below names this exact",
        "                file's content; a hand-edited card, however valid, is not approved.",
        f"  approve       {approval_token(card)}",
    ]
    if ship_command:
        lines.append(f"  ship          {ship_command}")
    if rollback_command:
        lines.append(f"  roll back     {rollback_command}")
    return "\n".join(lines)


def cmd_write(args: argparse.Namespace) -> int:
    card = build_card(args)
    atomic_write(Path(args.path), card)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Emit the whole card as shell-quoted CARD_* assignments for one `eval`.

    Field-at-a-time reads meant the loop could act on a card it had only
    partially validated. Exporting once forces the full schema check first.
    CARD_DIGEST and CARD_APPROVAL are computed here, from the validated card,
    so the loop never re-implements the canonical form.
    """
    card = load_card(Path(args.path))
    for key in CARD_ORDER:
        value = card.get(key)
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        print(f"CARD_{key.upper()}={shlex.quote('' if value is None else str(value))}")
    print(f"CARD_DIGEST={card_digest(card)}")
    print(f"CARD_APPROVAL={approval_token(card)}")
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    print(card_digest(load_card(Path(args.path))))
    return 0


def cmd_approval(args: argparse.Namespace) -> int:
    print(approval_token(load_card(Path(args.path))))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Say whether an existing card is exactly the card this prepare would write.

    Exit 0 means the same semantic card and it may be reused unchanged. Exit 3
    means the content the approval would cover has changed, and the existing card
    must not be overwritten. Exit 4 means the card on disk did not validate, and
    exit 2 means these values would not have made a valid card either way. All
    three are refusals; they differ only in what the operator is told.
    """
    try:
        existing = load_card(Path(args.path))
    except CardError as exc:
        print(f"RELEASE_CARD_ERROR: existing card did not validate: {exc}", file=sys.stderr)
        return 4
    candidate = build_card(args)
    differences = [
        f"{field}: existing card has {existing.get(field)!r}, this prepare would write {candidate.get(field)!r}"
        for field in COMPARED_FIELDS
        if existing.get(field) != candidate.get(field)
    ]
    if differences:
        for difference in differences:
            print(f"RELEASE_CARD_DIFF: {difference}", file=sys.stderr)
        return 3
    return 0


def cmd_rollback_write(args: argparse.Namespace) -> int:
    atomic_write(Path(args.path), build_rollback(args))
    return 0


def cmd_rollback_export(args: argparse.Namespace) -> int:
    state = load_rollback(Path(args.path))
    for key in ROLLBACK_ORDER:
        print(f"ROLLBACK_{key.upper()}={shlex.quote(str(state.get(key)))}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    print(
        render(
            load_card(Path(args.path)),
            ship_command=args.ship_command,
            rollback_command=args.rollback_command,
        )
    )
    return 0


def cmd_attest(args: argparse.Namespace) -> int:
    card = load_card(Path(args.card))
    sha = check_sha(args.sha, "sha")
    if card["sha"] != sha:
        fail(f"card sha {card['sha']} != attested sha {sha}")
    attestation = {
        "schema_version": SCHEMA_VERSION,
        "sha": sha,
        "card_digest": card_digest(card),
        "surface": card["surface"],
        "risk": card["risk"],
        "live_target": card.get("live_target"),
        "card_proof_mode": card["proof_mode"],
        "proof_kind": "manual-operator-attestation",
        "result": check_choice(args.result, "result", RESULTS),
        "note": check_text(args.note, "note"),
        "attested_at": check_text(args.attested_at, "attested timestamp"),
    }
    atomic_write(Path(args.path), attestation)
    return 0


def _add_card_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--surface", required=True)
    parser.add_argument("--risk", required=True)
    parser.add_argument("--effect", required=True)
    parser.add_argument("--proof-mode", required=True)
    parser.add_argument("--live-target", default="")
    parser.add_argument("--known-good-sha", required=True)
    parser.add_argument("--rollback-mode", required=True)
    parser.add_argument("--exclusions", required=True)
    parser.add_argument("--bootstrap-git", required=True)
    parser.add_argument("--bootstrap-python", required=True)
    parser.add_argument("--bootstrap-bash", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    write = subparsers.add_parser("write", help="write a prepared release card atomically")
    _add_card_fields(write)
    write.add_argument("--created-at", required=True)
    write.set_defaults(handler=cmd_write)

    compare = subparsers.add_parser(
        "compare", help="exit 0 if an existing card is exactly the card these values would write"
    )
    _add_card_fields(compare)
    compare.add_argument("--created-at", default="comparison-only")
    compare.set_defaults(handler=cmd_compare)

    export = subparsers.add_parser("export", help="print the validated card as CARD_* shell assignments")
    export.add_argument("--path", required=True)
    export.set_defaults(handler=cmd_export)

    digest = subparsers.add_parser("digest", help="print the canonical SHA-256 digest of a validated card")
    digest.add_argument("--path", required=True)
    digest.set_defaults(handler=cmd_digest)

    approval = subparsers.add_parser("approval", help="print the <sha>:<digest> approval token of a validated card")
    approval.add_argument("--path", required=True)
    approval.set_defaults(handler=cmd_approval)

    rollback_write = subparsers.add_parser("rollback-write", help="write rollback state atomically")
    rollback_write.add_argument("--path", required=True)
    rollback_write.add_argument("--released-sha", required=True)
    rollback_write.add_argument("--known-good-sha", required=True)
    rollback_write.add_argument("--rollback-sha", required=True)
    rollback_write.add_argument("--surface", required=True)
    rollback_write.add_argument("--risk", required=True)
    rollback_write.add_argument("--status", required=True)
    rollback_write.add_argument("--created-at", required=True)
    rollback_write.add_argument("--updated-at", required=True)
    rollback_write.set_defaults(handler=cmd_rollback_write)

    rollback_export = subparsers.add_parser(
        "rollback-export", help="print validated rollback state as ROLLBACK_* shell assignments"
    )
    rollback_export.add_argument("--path", required=True)
    rollback_export.set_defaults(handler=cmd_rollback_export)

    show = subparsers.add_parser("render", help="print the compact human card")
    show.add_argument("--path", required=True)
    show.add_argument("--ship-command", default="")
    show.add_argument("--rollback-command", default="")
    show.set_defaults(handler=cmd_render)

    attest = subparsers.add_parser("attest", help="write a manual proof attestation atomically")
    attest.add_argument("--card", required=True)
    attest.add_argument("--path", required=True)
    attest.add_argument("--sha", required=True)
    attest.add_argument("--result", required=True)
    attest.add_argument("--note", required=True)
    attest.add_argument("--attested-at", required=True)
    attest.set_defaults(handler=cmd_attest)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except CardError as exc:
        print(f"RELEASE_CARD_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
