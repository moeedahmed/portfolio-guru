"""Lock the vNext scaffold's safety contract.

The vNext private test bot must never start unless explicitly enabled,
must never share a Telegram token with the public Portfolio Guru bot,
and must never reach for live runtime actions from this slice. These
tests pin those invariants so a later slice cannot accidentally relax
them.
"""

from conversational_vnext_bot import (
    PRODUCTION_TOKEN_ENVS,
    VNEXT_TOKEN_ENV,
    guard_token_separation,
    is_enabled,
    main,
)


def test_is_enabled_requires_token_env():
    assert is_enabled({}) is False
    assert is_enabled({VNEXT_TOKEN_ENV: ""}) is False
    assert is_enabled({VNEXT_TOKEN_ENV: "   "}) is False
    assert is_enabled({VNEXT_TOKEN_ENV: "abc"}) is True


def test_guard_token_separation_blocks_matching_production_token():
    for prod_env in PRODUCTION_TOKEN_ENVS:
        env = {VNEXT_TOKEN_ENV: "shared", prod_env: "shared"}
        message = guard_token_separation(env)
        assert message is not None, f"{prod_env} collision not blocked"
        assert prod_env in message


def test_guard_token_separation_passes_for_distinct_tokens():
    env = {VNEXT_TOKEN_ENV: "vnext-only", "BOT_TOKEN": "public-only"}
    assert guard_token_separation(env) is None


def test_main_exits_clean_when_disabled(monkeypatch, capsys):
    for env_name in (VNEXT_TOKEN_ENV, *PRODUCTION_TOKEN_ENVS):
        monkeypatch.delenv(env_name, raising=False)

    assert main([]) == 0
    captured = capsys.readouterr()
    assert "disabled" in captured.err.lower()


def test_main_refuses_when_token_collides_with_production(monkeypatch, capsys):
    monkeypatch.setenv(VNEXT_TOKEN_ENV, "shared")
    monkeypatch.setenv("BOT_TOKEN", "shared")

    assert main([]) == 2
    captured = capsys.readouterr()
    assert "refused" in captured.err.lower()


def test_main_runs_noop_when_enabled_with_distinct_token(monkeypatch, capsys):
    monkeypatch.setenv(VNEXT_TOKEN_ENV, "vnext-only")
    monkeypatch.setenv("BOT_TOKEN", "public-only")

    assert main([]) == 0
    captured = capsys.readouterr()
    assert "no-op" in captured.err.lower()
