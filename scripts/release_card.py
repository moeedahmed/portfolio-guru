#!/usr/bin/env python3
"""Local release-card store for scripts/release_loop.sh.

One prepared card per full release SHA, kept under a gitignored `.release/`
directory. The card is what a single operator approval actually covers: exact
SHA, surface, risk, one-line effect, how the named live proof will be obtained,
the pre-deploy known-good SHA to roll back to, and the exclusions the approval
never extends to. ship/resume/attest refuse anything that drifted from it.

Stdlib only. No network, no git, no credentials. Card content is written by the
release loop from operator-supplied text and is validated here so a token,
patient detail, or multi-line paste cannot land in it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SURFACES = ("telegram",)
RISKS = ("internal", "telegram", "broad")
PROOF_MODES = ("automated", "manual")
RESULTS = ("pass", "fail")
MAX_TEXT = 200
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
    "known_good_sha",
    "exclusions",
    "created_at",
)


class CardError(Exception):
    """Any refusal that must stop the release loop before it mutates anything."""


def fail(message: str) -> "None":
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


def check_choice(value: str, label: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        fail(f"{label} must be one of: {', '.join(allowed)}")
    return value


def check_target(value: str) -> str:
    target = (value or "").strip().lstrip("@")
    if not TARGET.fullmatch(target):
        fail("live target must be an exact bot username (letters, digits, underscore)")
    return target


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


def load_card(path: Path) -> dict:
    if not path.exists():
        fail(f"no release card at {path}; run --mode prepare first")
    try:
        card = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"release card is unreadable: {exc}")
    if not isinstance(card, dict):
        fail("release card is not an object")
    if card.get("schema_version") != SCHEMA_VERSION:
        fail(f"release card schema {card.get('schema_version')!r} != supported {SCHEMA_VERSION}")
    check_sha(card.get("sha", ""), "card sha")
    check_choice(card.get("surface", ""), "card surface", SURFACES)
    check_choice(card.get("risk", ""), "card risk", RISKS)
    check_choice(card.get("proof_mode", ""), "card proof mode", PROOF_MODES)
    check_text(card.get("effect", ""), "card effect")
    if not isinstance(card.get("exclusions"), list) or not card["exclusions"]:
        fail("release card is missing its exclusions")
    return card


def build_card(args: argparse.Namespace) -> dict:
    live_target = check_target(args.live_target) if args.live_target else None
    risk = check_choice(args.risk, "risk", RISKS)
    if risk == "telegram" and live_target is None:
        fail("telegram risk requires an exact --live-target")
    exclusions = [item.strip() for item in args.exclusions.split(",") if item.strip()]
    if not exclusions:
        fail("--exclusions must list at least one excluded action")
    return {
        "schema_version": SCHEMA_VERSION,
        "sha": check_sha(args.sha, "sha"),
        "surface": check_choice(args.surface, "surface", SURFACES),
        "risk": risk,
        "effect": check_text(args.effect, "effect"),
        "proof_mode": check_choice(args.proof_mode, "proof mode", PROOF_MODES),
        "live_target": live_target,
        "known_good_sha": check_sha(args.known_good_sha, "known-good sha") if args.known_good_sha else None,
        "exclusions": exclusions,
        "created_at": check_text(args.created_at, "created timestamp"),
    }


def render(card: dict, *, ship_command: str = "") -> str:
    proof = card["proof_mode"]
    if card.get("live_target"):
        proof = f"{proof} (live target @{card['live_target']})"
    lines = [
        "RELEASE CARD — one card, one approval",
        f"  schema        {card['schema_version']}",
        f"  sha           {card['sha']}",
        f"  surface       {card['surface']}",
        f"  risk          {card['risk']}",
        f"  effect        {card['effect']}",
        f"  proof mode    {proof}",
        f"  known good    {card.get('known_good_sha') or 'captured and verified at ship, before main is moved'}",
        f"  exclusions    {'; '.join(card['exclusions'])}",
        f"  created       {card['created_at']}",
        "  covers        push of this exact SHA to main, CI Tests, Mac Mini deploy, runtime identity,",
        "                the named proof above, unchanged proof resume, and bounded targeted rollback",
        "                to the known-good SHA if that proof fails.",
        "  not covered   anything in exclusions, and any change of SHA, target, risk, surface, effect,",
        "                proof mode or rollback target — each needs a new card and a new approval.",
    ]
    if ship_command:
        lines.append(f"  ship          {ship_command}")
    return "\n".join(lines)


def cmd_write(args: argparse.Namespace) -> int:
    card = build_card(args)
    atomic_write(Path(args.path), card)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Emit the whole card as shell-quoted CARD_* assignments for one `eval`.

    Field-at-a-time reads meant the loop could act on a card it had only
    partially validated. Exporting once forces the full schema check first.
    """
    card = load_card(Path(args.path))
    for key in CARD_ORDER:
        value = card.get(key)
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        print(f"CARD_{key.upper()}={shlex.quote('' if value is None else str(value))}")
    return 0


def cmd_set_known_good(args: argparse.Namespace) -> int:
    path = Path(args.path)
    card = load_card(path)
    card["known_good_sha"] = check_sha(args.known_good_sha, "known-good sha")
    atomic_write(path, {key: card.get(key) for key in CARD_ORDER})
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    print(render(load_card(Path(args.path)), ship_command=args.ship_command))
    return 0


def cmd_attest(args: argparse.Namespace) -> int:
    card = load_card(Path(args.card))
    sha = check_sha(args.sha, "sha")
    if card["sha"] != sha:
        fail(f"card sha {card['sha']} != attested sha {sha}")
    attestation = {
        "schema_version": SCHEMA_VERSION,
        "sha": sha,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    write = subparsers.add_parser("write", help="write a prepared release card atomically")
    write.add_argument("--path", required=True)
    write.add_argument("--sha", required=True)
    write.add_argument("--surface", required=True)
    write.add_argument("--risk", required=True)
    write.add_argument("--effect", required=True)
    write.add_argument("--proof-mode", required=True)
    write.add_argument("--live-target", default="")
    write.add_argument("--known-good-sha", default="")
    write.add_argument("--exclusions", required=True)
    write.add_argument("--created-at", required=True)
    write.set_defaults(handler=cmd_write)

    export = subparsers.add_parser("export", help="print the validated card as CARD_* shell assignments")
    export.add_argument("--path", required=True)
    export.set_defaults(handler=cmd_export)

    known_good = subparsers.add_parser("set-known-good", help="record the verified pre-deploy known-good SHA")
    known_good.add_argument("--path", required=True)
    known_good.add_argument("--known-good-sha", required=True)
    known_good.set_defaults(handler=cmd_set_known_good)

    show = subparsers.add_parser("render", help="print the compact human card")
    show.add_argument("--path", required=True)
    show.add_argument("--ship-command", default="")
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
