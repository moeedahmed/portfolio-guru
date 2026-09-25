#!/usr/bin/env python3
"""Pinned bootstrap for scripts/release_loop.sh — the only approved execution path.

Invoked exactly as the prepared card printed it:

    <git> -C <root> show <sha>:scripts/release_bootstrap.py | <python> - \
        --root <root> --git <git> --bash <bash> --python <python> --sha <sha> -- <release_loop args>

Nothing mutable in the checkout is executed. This file is read out of the local
Git object database by `git show`, and it in turn reads `scripts/release_loop.sh`
and `scripts/release_card.py` out of the same objects at the approved SHA,
verifies each blob hashes to the id Git records for it, stages them in a
private, unpredictable temporary directory (mode 0700), and runs the loop from
there with the absolute git/python/bash paths that were resolved and frozen on
the card at prepare time. The staging directory is removed afterwards; no
runner copy is retained anywhere.

Tamper-resistance claim, bounded verbatim: this trusts the local Git object
database and the original printed command; it does not defend against a
same-user process altering PATH/interpreter/shell rc or the command invocation.

Stdlib only. No network, no credentials, no writes outside the temporary
staging directory.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
PINNED_FILES = ("scripts/release_loop.sh", "scripts/release_card.py")


def fail(message: str) -> int:
    print(f"RELEASE_BOOTSTRAP_ERROR: {message}", file=sys.stderr)
    return 3


def executable(path: str, label: str) -> str:
    if not os.path.isabs(path):
        raise ValueError(f"{label} must be an absolute path, got {path!r}")
    if not (os.path.isfile(path) and os.access(path, os.X_OK)):
        raise ValueError(f"{label} is not an executable file: {path}")
    return os.path.realpath(path)


def git_blob_id(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def read_blob(git: str, root: str, sha: str, path: str) -> bytes:
    recorded = subprocess.run(
        [git, "-C", root, "rev-parse", "--verify", "--quiet", f"{sha}:{path}"],
        capture_output=True, text=True, check=False,
    )
    if recorded.returncode != 0 or not FULL_SHA.fullmatch(recorded.stdout.strip()):
        raise ValueError(f"{path} is not present in commit {sha}")
    content = subprocess.run(
        [git, "-C", root, "cat-file", "blob", f"{sha}:{path}"],
        capture_output=True, check=False,
    )
    if content.returncode != 0:
        raise ValueError(f"could not read {path} from commit {sha}")
    if git_blob_id(content.stdout) != recorded.stdout.strip():
        raise ValueError(f"{path} read from {sha} does not hash to the blob Git records for it")
    return content.stdout


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True)
    parser.add_argument("--git", required=True)
    parser.add_argument("--bash", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("loop_args", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    loop_args = list(args.loop_args)
    if loop_args and loop_args[0] == "--":
        loop_args = loop_args[1:]
    sha = args.sha.strip().lower()
    if not FULL_SHA.fullmatch(sha):
        return fail("--sha must be a full 40-character hexadecimal SHA")
    try:
        git = executable(args.git, "--git")
        bash = executable(args.bash, "--bash")
        python = executable(args.python, "--python")
    except ValueError as exc:
        return fail(str(exc))
    if not os.path.isabs(args.root) or not os.path.isdir(args.root):
        return fail(f"--root must be an absolute existing directory, got {args.root!r}")
    root = os.path.realpath(args.root)
    running = os.path.realpath(sys.executable)
    if running != python:
        return fail(f"this bootstrap is running under {running}, not the frozen interpreter {python}")
    verify = subprocess.run(
        [git, "-C", root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
        capture_output=True, text=True, check=False,
    )
    if verify.returncode != 0 or verify.stdout.strip() != sha:
        return fail(f"commit {sha} is not readable in {root}")

    stage = tempfile.mkdtemp(prefix="pg-release-")
    try:
        os.chmod(stage, 0o700)
        mode = stat.S_IMODE(os.stat(stage).st_mode)
        if mode != 0o700:
            return fail(f"staging directory {stage} is mode {mode:o}, not 0700")
        staged: dict[str, str] = {}
        for path in PINNED_FILES:
            try:
                content = read_blob(git, root, sha, path)
            except ValueError as exc:
                return fail(str(exc))
            target = os.path.join(stage, os.path.basename(path))
            with open(target, "wb") as handle:
                handle.write(content)
            os.chmod(target, 0o500)
            staged[path] = target
        print(
            f"RELEASE_BOOTSTRAP sha={sha} stage_mode=0700 git={git} python={python} bash={bash} "
            f"pinned={','.join(PINNED_FILES)}",
            flush=True,
        )
        env = dict(os.environ)
        env.update(
            RELEASE_LOOP_BOOTSTRAP="1",
            RELEASE_LOOP_PINNED_SHA=sha,
            RELEASE_LOOP_ROOT=root,
            RELEASE_LOOP_GIT=git,
            RELEASE_LOOP_PYTHON=python,
            RELEASE_LOOP_BASH=bash,
            RELEASE_LOOP_CARD_TOOL=staged["scripts/release_card.py"],
        )
        # The script arrived on stdin, so hand the loop a terminal if there is
        # one (the broad-risk checklist is interactive) and nothing otherwise.
        stdin = subprocess.DEVNULL
        tty = None
        try:
            tty = open("/dev/tty", "rb")
            stdin = tty
        except OSError:
            tty = None
        try:
            result = subprocess.run(
                [bash, staged["scripts/release_loop.sh"], *loop_args],
                cwd=root, env=env, stdin=stdin, check=False,
            )
        finally:
            if tty is not None:
                tty.close()
        return result.returncode
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
