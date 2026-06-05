#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/backend"

PY="${PYTHON:-venv/bin/python3}"
OUT_DIR="${WEIRD_PROMPT_QA_DIR:-$ROOT/.artifacts/weird-prompt-qa/latest}"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

set +e
WEIRD_PROMPT_QA_DIR="$OUT_DIR" "$PY" -m pytest tests/test_weird_prompt_qa_offline.py -q "$@"
PYTEST_RC=$?
set -e

printf '\nReport written:\n  %s/weird-prompt-qa.md\n  %s/weird-prompt-qa.json\n' "$OUT_DIR" "$OUT_DIR"

FIX_QUEUE="$OUT_DIR/fix-queue.json"
if [ -f "$FIX_QUEUE" ]; then
    FAILURE_COUNT=$("$PY" -c "import json; d=json.load(open('$FIX_QUEUE')); print(d['failure_count'])" 2>/dev/null || echo "?")
    printf '  %s  (%s failure(s))\n' "$FIX_QUEUE" "$FAILURE_COUNT"
    printf '\nNext action: read %s, fix the routing/reply gaps listed there, then re-run this script.\n' "$FIX_QUEUE"
fi

exit $PYTEST_RC
