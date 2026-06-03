#!/usr/bin/env bash
# Offline Telegram QA transcript — no live Telegram, no network.
#
# Drives the real PTB handler stack with OfflineRequest blocking outbound
# calls, runs the six Haris/Sana golden cases, and writes JSON + Markdown
# transcripts under .artifacts/telegram-qa-transcript/<utc-stamp>/.
#
# Usage:
#   bash scripts/telegram_qa_offline.sh
#   TELEGRAM_QA_TRANSCRIPT_DIR=/tmp/pg-qa bash scripts/telegram_qa_offline.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="${ROOT}/backend"

cd "$BACKEND"

if [[ -x "venv/bin/python3" ]]; then
  PY="venv/bin/python3"
elif [[ -x ".venv/bin/python3" ]]; then
  PY=".venv/bin/python3"
else
  PY="python3"
fi

exec "$PY" -m pytest tests/test_telegram_qa_offline_transcript.py -v "$@"
