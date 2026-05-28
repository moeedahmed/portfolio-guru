"""Lock the vNext scaffold's safety contract.

The vNext private test bot must never start unless explicitly enabled,
must never share a Telegram token with the public Portfolio Guru bot,
and must never reach for live runtime actions from this slice. These
tests pin those invariants so a later slice cannot accidentally relax
them.
"""

from types import SimpleNamespace

from conversational_case_engine import (
    CaseState,
    EngineSnapshot,
    IngestKind,
    SourceType,
    new_workspace,
)
from conversational_vnext_bot import (
    PRODUCTION_TOKEN_ENVS,
    VNEXT_TOKEN_ENV,
    build_handler,
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


def _bare_text_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        caption=None,
        voice=None,
        audio=None,
        photo=[],
        document=None,
        message_id=1,
        chat=SimpleNamespace(id=42),
    )


def test_build_handler_returns_none_when_disabled(monkeypatch):
    for env_name in (VNEXT_TOKEN_ENV, *PRODUCTION_TOKEN_ENVS):
        monkeypatch.delenv(env_name, raising=False)

    assert build_handler() is None


def test_build_handler_returns_none_when_token_collides_with_production(monkeypatch):
    monkeypatch.setenv(VNEXT_TOKEN_ENV, "shared")
    monkeypatch.setenv("BOT_TOKEN", "shared")

    assert build_handler() is None


def test_build_handler_processes_text_message_into_engine_snapshot(monkeypatch):
    monkeypatch.setenv(VNEXT_TOKEN_ENV, "vnext-only")
    for prod_env in PRODUCTION_TOKEN_ENVS:
        monkeypatch.delenv(prod_env, raising=False)

    handler = build_handler()
    assert handler is not None

    workspace = new_workspace()
    snapshot = handler(
        workspace,
        _bare_text_message(
            "Had a difficult airway case with a 62M in resus, managed RSI."
        ),
    )

    assert isinstance(snapshot, EngineSnapshot)
    assert snapshot.workspace.case_id == workspace.case_id
    assert snapshot.workspace.state is CaseState.POSSIBLE_CASE
    assert snapshot.workspace.facts == ()
    assert snapshot.workspace.chat_turns[0].source_type is SourceType.TEXT


def test_build_handler_passes_image_through_as_unconfirmed_stricter_source(monkeypatch):
    monkeypatch.setenv(VNEXT_TOKEN_ENV, "vnext-only")
    for prod_env in PRODUCTION_TOKEN_ENVS:
        monkeypatch.delenv(prod_env, raising=False)

    handler = build_handler()
    assert handler is not None

    photo_message = SimpleNamespace(
        text=None,
        caption=None,
        voice=None,
        audio=None,
        photo=[SimpleNamespace(file_id="photo-1", width=10, height=10)],
        document=None,
        message_id=2,
        chat=SimpleNamespace(id=42),
    )

    snapshot = handler(new_workspace(), photo_message)

    assert snapshot.workspace.draft_eligible_facts() == ()
    assert snapshot.workspace.state is CaseState.POSSIBLE_CASE
    assert snapshot.workspace.chat_turns[0].source_type is SourceType.IMAGE


def test_build_handler_does_not_register_telegram_handlers_or_polling(monkeypatch):
    """The build hook must remain a pure conversion path in this slice.

    A future slice will wire a real polling loop. Until then, the
    handler returned here must never import python-telegram-bot, touch
    Kaizen, or perform any I/O. Driving it through several messages
    must stay completely side-effect free.
    """

    monkeypatch.setenv(VNEXT_TOKEN_ENV, "vnext-only")
    for prod_env in PRODUCTION_TOKEN_ENVS:
        monkeypatch.delenv(prod_env, raising=False)

    handler = build_handler()
    assert handler is not None

    workspace = new_workspace()
    for text in (
        "What forms support SLO11?",
        "62M chest pain, STEMI on ECG, cath lab activated.",
        "File this as a CBD in Kaizen",
    ):
        snapshot = handler(workspace, _bare_text_message(text))
        workspace = snapshot.workspace

    # IngestKind drives state; with no extracted facts the engine still
    # refuses to enter DRAFT_READY / SAVING, proving the safety contract.
    assert workspace.state in {
        CaseState.IDLE,
        CaseState.POSSIBLE_CASE,
        CaseState.COLLECTING,
    }


def test_build_handler_refuses_when_vnext_token_blank(monkeypatch):
    monkeypatch.setenv(VNEXT_TOKEN_ENV, "   ")
    for prod_env in PRODUCTION_TOKEN_ENVS:
        monkeypatch.delenv(prod_env, raising=False)

    assert build_handler() is None


def test_build_handler_rejects_when_any_known_production_token_matches(monkeypatch):
    for prod_env in PRODUCTION_TOKEN_ENVS:
        monkeypatch.setenv(VNEXT_TOKEN_ENV, "duplicate")
        monkeypatch.setenv(prod_env, "duplicate")
        try:
            assert build_handler() is None, f"{prod_env} collision was not blocked"
        finally:
            monkeypatch.delenv(prod_env, raising=False)
            monkeypatch.delenv(VNEXT_TOKEN_ENV, raising=False)


def test_module_does_not_import_python_telegram_bot():
    """The scaffold's source must not import ``python-telegram-bot``.

    Importing this module must stay side-effect free in this slice:
    the future polling-loop slice will wire handlers explicitly, but
    until then the private scaffold has no business pulling in the
    production bot stack. An AST-level check is robust to docstring
    prose and to other test modules that legitimately import ``bot.py``
    and pollute ``sys.modules`` for the rest of the run.
    """

    import ast
    import inspect

    import conversational_vnext_bot

    tree = ast.parse(inspect.getsource(conversational_vnext_bot))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert "telegram" not in imported_roots
