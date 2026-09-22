"""Live entrypoint only; all effectful clinical paths remain offline protected."""
import os
from pathlib import Path

import pytest
from tests.test_e2e import telethon_client  # sole shared client fixture
from tests.telegram_live_harness import explore_whole_bot, has_telethon_env, telethon_env
from tests.telegram_live_policy import registered_catalogue
from tests.whole_bot_identity import current_binding, verify_candidate
from tests.whole_bot_aggregate import write_layer

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def whole_bot_ready():
    # Autouse runs before the shared client fixture, including connect/get_me.
    root = Path(os.environ["WHOLE_BOT_ARTIFACT_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    result = verify_candidate(Path(__file__).resolve().parents[2], root,
        os.environ.get("WHOLE_BOT_RUN_ID", ""),
        os.environ.get("TELEGRAM_BOT_USERNAME", "").lstrip("@"),
        os.environ.get("PORTFOLIO_GURU_EXPECTED_SHA", ""))
    assert result["status"] == "passed", "Candidate/runtime identity not verified"
    assert os.environ.get("WHOLE_BOT_LIVE_PROOF") == "1"
    assert has_telethon_env(), "Whole-bot live approval/credentials/allowlist incomplete"
    assert os.environ.get("TELEGRAM_QA_USER_ID", "").isdigit(), "Explicit synthetic account required"


async def cancel_cleanup(client):
    from unittest.mock import patch
    from tests.telegram_live_harness import run_telegram_workflow, TelegramStep
    binding = current_binding()
    root = Path(os.environ["WHOLE_BOT_ARTIFACT_DIR"])
    run_id = binding.pop("run_id")
    # Account mismatch must never cancel the wrong account's conversation.
    assert (await client.get_me()).id == int(os.environ["TELEGRAM_QA_USER_ID"]), "Synthetic account mismatch"
    try:
        with patch.dict(os.environ, {"TELEGRAM_E2E_ARTIFACT_DIR": str(root / "cleanup")}):
            await run_telegram_workflow(client, binding["target"], [
                TelegramStep("cleanup", "/cancel", expect_text_any=("cancelled",))], strict=True)
        (root / "cleanup-transcript.json").write_bytes((root / "cleanup/portfolio-guru-telegram-transcript.json").read_bytes())
        write_layer(root, "cleanup", run_id, "passed", **binding)
    except BaseException as exc:
        write_layer(root, "cleanup", run_id, "failed", reason=type(exc).__name__, **binding)
        raise


async def test_live_cancel_cleanup(telethon_client):
    await cancel_cleanup(telethon_client)


async def test_live_whole_bot(telethon_client):
    binding = current_binding()
    root = Path(os.environ["WHOLE_BOT_ARTIFACT_DIR"])
    run_id = binding.pop("run_id")
    expected = int(os.environ["TELEGRAM_QA_USER_ID"])
    assert (await telethon_client.get_me()).id == expected, "Synthetic account mismatch"
    try:
        env = telethon_env()
        graph = await explore_whole_bot(telethon_client, binding["target"], registered_catalogue(),
            root, expected_user_id=expected, secrets=(env["session"], env["api_hash"]))
        (root / "live_graph-transcript.json").write_bytes((root / "whole-bot-transcript.json").read_bytes())
        write_layer(root, "live_graph", run_id, graph["status"], **binding,
            routes=graph["routes"], commands=graph["commands"], failures=graph["failures"],
            protected=graph["protected"], registration_digest=graph["registration_digest"])
        write_layer(root, "clinical", run_id, "pending", **binding,
            reason="Generation and portfolio effects require a future synthetic-isolation envelope; offline only")
    except BaseException as exc:
        write_layer(root, "live_graph", run_id, "failed", reason=type(exc).__name__, **binding)
        raise
    finally:
        await cancel_cleanup(telethon_client)
