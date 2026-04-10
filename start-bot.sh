#!/bin/zsh
# Portfolio Guru bot launcher (local)
# Canonical path: load secrets via BWS (backend/run_local.sh)

set -e
cd "$(dirname "$0")"
exec bash backend/run_local.sh
