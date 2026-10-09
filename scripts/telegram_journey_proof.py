#!/usr/bin/env python3
"""Local, content-bound proof for the sixteen guarded staging journeys."""
from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

TARGET = 'portfolio_guru_test_bot'
PREFIX = 'tests/test_e2e.py::'
FORMS = ('LAT', 'TEACH', 'QIAT', 'MGMT_ROTA', 'SERIOUS_INC', 'PROC_LOG',
         'US_CASE', 'FORMAL_COURSE', 'REFLECT_LOG')
JOURNEYS = {code: PREFIX + f'test_e2e_form_variety_ready_draft_to_cancel_journey[{code}]' for code in FORMS}
JOURNEYS.update({'teaching-pdf': PREFIX + 'test_e2e_form_variety_pdf_ready_draft_to_cancel_journey'})
JOURNEYS.update({kind: PREFIX + f'test_e2e_{kind}_ready_draft_to_cancel_journey'
                 for kind in ('text', 'photo', 'voice', 'document')})
JOURNEYS.update({'settings': PREFIX + 'test_e2e_settings_read_only_journey',
                 'form-switching': PREFIX + 'test_e2e_form_switching_to_cancel_journey'})


def snapshot(root, sha=None):
    """Read source only; never load dotenv, credentials, providers or transport."""
    def source(name):
        return (name.startswith('backend/') and name.endswith(('.py', '.json', '.yaml', '.yml', '.txt', '.ini'))
                or name in ('scripts/telegram_bot_qa.sh', 'scripts/telegram_journey_proof.py',
                            'scripts/stage.sh', 'scripts/run_staging.sh', 'scripts/verify_live_runtime.py', 'start-bot.sh'))
    if sha:
        archive = subprocess.run([os.environ.get('RELEASE_LOOP_GIT', 'git'), '-C', str(root), 'archive', sha], check=True, capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            return {item.name: tar.extractfile(item).read() for item in tar.getmembers()
                    if item.isfile() and source(item.name)}
    names = subprocess.run([os.environ.get('RELEASE_LOOP_GIT', 'git'), '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                           check=True, capture_output=True).stdout.decode().split('\0')
    return {name: (root / name).read_bytes() for name in names if source(name) and (root / name).is_file()}


def fingerprints(root, sha=None):
    files = snapshot(Path(root), sha)
    # The shared bot module is deliberately conservative: every local import,
    # including lazy imports, is watched. A bot.py change invalidates all paths.
    seeds = {'backend/bot.py', 'backend/tests/test_e2e.py', 'backend/tests/conftest.py',
             'scripts/telegram_bot_qa.sh', 'scripts/telegram_journey_proof.py', 'scripts/verify_live_runtime.py'}
    if not seeds <= files.keys():
        raise ValueError('Journey dependency source missing')
    seeds |= {name for name in files if name.startswith('backend/') and '/tests/' not in name
              and '/_archived/' not in name and not name.endswith('.py')}
    seeds |= {name for name in ('scripts/stage.sh', 'scripts/run_staging.sh', 'start-bot.sh', 'backend/pytest.ini') if name in files}
    pending, watched = list(seeds), set()
    while pending:
        name = pending.pop()
        if name in watched:
            continue
        watched.add(name)
        if not name.endswith('.py'):
            continue
        tree = ast.parse(files[name], filename=name)
        package = name.removeprefix('backend/').rsplit('/', 1)[0].replace('/', '.') if '/' in name.removeprefix('backend/') else ''
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ''
                if node.level:
                    parts = package.split('.')
                    base = '.'.join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
                modules.append(base)
                modules.extend(base + '.' + alias.name for alias in node.names)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module':
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    modules.append(node.args[0].value)
        for module in modules:
            parts = module.split('.')
            for size in range(1, len(parts) + 1):
                base = 'backend/' + '/'.join(parts[:size])
                for candidate in (base + '.py', base + '/__init__.py'):
                    if candidate in files and candidate not in watched:
                        pending.append(candidate)
    digest = hashlib.sha256()
    for name in sorted(watched):
        digest.update(name.encode() + b'\0' + hashlib.sha256(files[name]).digest())
    # The node id binds the parametrised synthetic input and exact test code;
    # test_e2e.py and its local harness/fixture dependencies are watched above.
    return {key: hashlib.sha256(digest.digest() + node.encode()).hexdigest() for key, node in JOURNEYS.items()}


def matching(entry, fingerprint):
    return (isinstance(entry, dict) and entry.get('fingerprint') == fingerprint
            and entry.get('target') == TARGET and isinstance(entry.get('sha'), str) and re.fullmatch('[0-9a-f]{40}', entry['sha']))


def select(current, ledger, *, only=None, full=False):
    selected = list(current) if only is None else list(dict.fromkeys(only))
    unknown = set(selected) - current.keys()
    if unknown or not selected:
        raise ValueError('Unknown journey ids or empty selection: ' + ', '.join(sorted(unknown)))
    return [key for key in selected if full or not matching(ledger.get(key), current[key])]


def validate_coverage(report, root, sha, *, committed=False):
    if not isinstance(report, dict) or report.get('schema') != 1 or report.get('sha') != sha or report.get('target') != TARGET:
        raise ValueError('Journey coverage does not name this exact SHA and test bot')
    current = fingerprints(root, sha if committed else None)
    entries = report.get('journeys')
    if not isinstance(entries, dict) or set(entries) != set(JOURNEYS):
        raise ValueError('Full sixteen-journey coverage required')
    for key, fingerprint in current.items():
        entry = entries[key]
        if not matching(entry, fingerprint) or entry.get('status') not in ('passed', 'reused'):
            raise ValueError(f'Full journey coverage missing or stale: {key}')


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.journey-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run(args):
    root = args.root.resolve()
    sha = subprocess.run([os.environ.get('RELEASE_LOOP_GIT', 'git'), '-C', str(root), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
    if args.sha and args.sha != sha:
        raise ValueError('Journey runtime SHA differs from checkout')
    current = fingerprints(root)
    if current != fingerprints(root, sha):
        raise ValueError('Journey dependencies differ from committed runtime SHA')
    def verify_runtime():
        subprocess.run([args.python, str(root / 'scripts/verify_live_runtime.py'),
                        '--service-label', 'com.portfolioguru.staging-bot', '--root', str(root),
                        '--identity', '/tmp/portfolio-guru-staging-runtime.json', '--expected-sha', sha], check=True)
    verify_runtime()
    ledger_path = root / '.artifacts/telegram-bot-qa/journey-passes.json'
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    # One owner while sends are in progress; an overlapping run fails promptly.
    with ledger_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        records = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
        if not isinstance(records, dict) or any(not isinstance(value, dict) for value in records.values()):
            raise ValueError('Invalid journey ledger')
        ledger = {key: records.get(key, {}).get(value) for key, value in current.items()}
        ledger = {key: value for key, value in ledger.items() if matching(value, current[key])}
        def save_ledger():
            for key, fingerprint in current.items():
                history = records.setdefault(key, {})
                if key in ledger: history[fingerprint] = ledger[key]
                else: history.pop(fingerprint, None)
            write_json(ledger_path, records)
        allowed = list(JOURNEYS)
        if args.mode == 'form-variety': allowed = list(FORMS) + ['teaching-pdf']
        if args.mode == 'form-switching': allowed = ['form-switching']
        requested = args.only.split(',') if args.only is not None else allowed
        if set(requested) - set(allowed):
            raise ValueError('Unknown journey ids for this mode')
        selected = select({key: current[key] for key in allowed}, ledger, only=requested, full=args.full)
        for key in requested:
            if key not in selected:
                print(f'{key}: already passed at {ledger[key]["sha"]} (reused; no fresh pass)', flush=True)
        # Revoke selected proof before running: interruption/failure cannot leave
        # old successes reusable. Completed passes are salvaged from JUnit even
        # when another journey fails later in the batch.
        for key in selected:
            ledger.pop(key, None)
        save_ledger()
        result, passed = 0, set()
        if selected:
            xml = args.report.with_suffix('.xml')
            xml.parent.mkdir(parents=True, exist_ok=True)
            if xml.exists(): xml.unlink()
            result = subprocess.run([args.python, '-m', 'pytest', *(JOURNEYS[key] for key in selected),
                                     '-q', '-rs', '-m', 'e2e', '--junitxml=' + str(xml)], cwd=root / 'backend').returncode
            cases = ET.parse(xml).findall('.//testcase') if xml.exists() else []
            for key in selected:
                found = [case for case in cases if case.get('name') == JOURNEYS[key].split('::')[1]
                         and case.get('classname') in ('tests.test_e2e', 'test_e2e')]
                if len(found) == 1 and not any(found[0].find(tag) is not None for tag in ('skipped', 'failure', 'error')):
                    passed.add(key)
            expected = {JOURNEYS[key].split('::')[1] for key in selected}
            if len(cases) != len(expected) or {case.get('name') for case in cases} != expected or len(passed) != len(selected):
                result = 1
            if current != fingerprints(root) or current != fingerprints(root, sha):
                raise ValueError('Journey source changed while tests ran; no proof recorded')
            verify_runtime()
            for key in sorted(passed):
                ledger[key] = {'fingerprint': current[key], 'sha': sha, 'target': TARGET,
                               'passed_at': datetime.now(timezone.utc).isoformat()}
                print(f'{key}: fresh PASS at {sha}', flush=True)
            save_ledger()
        if not selected:
            verify_runtime()
        entries = {key: {**ledger[key], 'status': 'passed' if key in passed else 'reused'}
                   for key in JOURNEYS if matching(ledger.get(key), current[key])}
        report = {'schema': 1, 'sha': sha, 'target': TARGET, 'journeys': entries,
                  'selected': selected, 'full_run': args.full, 'complete': set(entries) == set(JOURNEYS)}
        write_json(args.report, report)
        status = 'FAIL' if result else ('PARTIAL' if args.mode == 'wider' and not report['complete'] else 'PASS')
        print(f'{args.mode}-completeness: ' + status
              + f' ({len(entries)}/16 covered; {len(passed)} fresh)', flush=True)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--mode', choices=('wider', 'form-variety', 'form-switching'), default='wider')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--python', required=True)
    parser.add_argument('--sha')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--only')
    selection.add_argument('--changed', action='store_true')
    selection.add_argument('--full', action='store_true')
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, ValueError, subprocess.CalledProcessError, ET.ParseError, SyntaxError) as exc:
        print(f'Journey proof blocked: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
