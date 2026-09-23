"""The offline suite must give CI's result whatever backend/.env holds.

Worktrees link the shared real backend/.env; on 2026-09-23 its Supabase settings
leaked in through bot.py's load_dotenv() and 55 offline tests tried the real
mirror. conftest.py now disables dotenv for the test process. These tests pin
that isolation and pin that the socket guard itself is still fail-closed.
"""
import os
import socket

import pytest
from dotenv import load_dotenv


def test_dotenv_files_are_not_loaded_into_offline_tests(tmp_path, monkeypatch):
    monkeypatch.delenv("PG_OFFLINE_ENV_LEAK_PROBE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("PG_OFFLINE_ENV_LEAK_PROBE=leaked\n")

    load_dotenv(env_file)

    assert "PG_OFFLINE_ENV_LEAK_PROBE" not in os.environ


def test_supabase_mirror_is_unconfigured_like_ci():
    assert "SUPABASE_URL" not in os.environ
    assert "SUPABASE_SERVICE_ROLE_KEY" not in os.environ


def test_socket_guard_still_fails_a_real_network_call():
    with pytest.raises(pytest.fail.Exception, match="attempted a socket connection"):
        socket.create_connection(("example.com", 443), timeout=1)
