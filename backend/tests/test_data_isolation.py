"""The offline suite must never read or write the live bot's data.

Every runtime store resolves through data_paths; conftest points that at a
throwaway directory. These tests fail if a module hard-codes the live path
again or if a store escapes the test data dir.
"""
import ast
import os
from pathlib import Path

import data_paths

BACKEND = Path(__file__).resolve().parent.parent
LIVE_DATA_DIR = Path.home() / ".openclaw" / "data" / "portfolio-guru"


def _string_constants(source: str):
    """String literals in code, ignoring docstrings and comments."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.value


def test_no_backend_module_hard_codes_the_live_data_dir():
    offenders = []
    for path in sorted(BACKEND.glob("*.py")):
        if path.name == "data_paths.py":
            continue
        for value in _string_constants(path.read_text()):
            # Both spellings: "~/.openclaw/data/..." and Path.home() / ".openclaw" / "data".
            if ".openclaw/data" in value or value == ".openclaw":
                offenders.append(f"{path.name}: {value!r}")
    assert offenders == [], "use data_paths.data_path() instead:\n" + "\n".join(offenders)


def test_suite_data_dir_is_not_the_live_one():
    assert os.environ.get(data_paths.DATA_DIR_ENV)
    assert data_paths.data_dir().resolve() != LIVE_DATA_DIR.resolve()


def test_default_stores_resolve_inside_the_test_data_dir():
    import credentials
    import draft_backup
    import dogfood_audit
    import filing_attempt_log
    import funnel_metrics
    import kaizen_index
    import profile_store
    import usage

    root = data_paths.data_dir().resolve()
    paths = {
        "usage": usage.DB_PATH,
        "kaizen_index": kaizen_index.DB_PATH,
        "credentials": credentials.DATABASE_URL.removeprefix("sqlite:///"),
        "profile_store": profile_store.DATABASE_URL.removeprefix("sqlite:///"),
        "funnel": funnel_metrics.default_log_path(),
        "filing": filing_attempt_log.default_log_path(),
        "audit": dogfood_audit.default_log_path(),
        "drafts": draft_backup.backup_dir(),
    }
    escaped = {name: str(p) for name, p in paths.items() if root not in Path(p).resolve().parents}
    assert escaped == {}
