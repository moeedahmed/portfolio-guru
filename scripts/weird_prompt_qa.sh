#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/backend"

PY="${PYTHON:-venv/bin/python3}"
OUT_DIR="${WEIRD_PROMPT_QA_DIR:-$ROOT/.artifacts/weird-prompt-qa/latest}"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

WEIRD_PROMPT_QA_DIR="$OUT_DIR" "$PY" -m pytest tests/test_weird_prompt_qa_offline.py -q "$@"

printf '\nReport written:\n  %s/weird-prompt-qa.md\n  %s/weird-prompt-qa.json\n' "$OUT_DIR" "$OUT_DIR"
