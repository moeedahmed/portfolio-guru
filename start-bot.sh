#!/bin/zsh
# Portfolio Guru bot launcher
# Loads env vars from .env and starts the bot

cd "$(dirname "$0")/backend"
set -a
source .env
set +a

exec venv/bin/python3 bot.py
