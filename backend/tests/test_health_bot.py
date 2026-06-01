from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest
from telegram.ext import ConversationHandler

from health_models import HealthProfile, Pathway
from tests.bot_simulator import BotSimulator


def test_pathway_is_registered_in_telegram_command_menu():
    import bot

    assert ("pathway", "Switch Portfolio Health between ARCP and CESR") in bot.BOT_COMMANDS


@pytest.mark.asyncio
async def test_safe_edit_text_retries_plain_text_when_markdown_is_invalid():
    import bot

    target = SimpleNamespace(
        edit_text=AsyncMock(
            side_effect=[
                BadRequest("Can't parse entities: can't find end of the entity"),
                "ok",
            ]
        )
    )

    result = await bot._safe_edit_text(target, "Bad _markdown", parse_mode="Markdown")

    assert result == "ok"
    assert target.edit_text.await_count == 2
    assert target.edit_text.await_args_list[1].kwargs == {}


@pytest.fixture
def isolated_health_store(tmp_path, monkeypatch):
    """Point the flat-file health store at a per-test path."""
    monkeypatch.setenv(
        "PORTFOLIO_GURU_HEALTH_PROFILE_PATH",
        str(tmp_path / "health_profiles.json"),
    )
    import health_profile_store
    return health_profile_store


def _profile(user_id: int, pathway: Pathway) -> HealthProfile:
    now = datetime.now(UTC)
    return HealthProfile(
        user_id=str(user_id),
        pathway=pathway,
        pathway_config={},
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_pathway_command_saves_selected_pathway(isolated_health_store):
    import bot
    health_profile_store = isolated_health_store

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    result = await bot.pathway_command(sim._make_text_update("/pathway"), context)

    assert result == bot.AWAIT_PATHWAY
    assert ("Training (ARCP)", "PATHWAY|training_arcp") in sim.get_last_buttons()
    assert ("CESR / Portfolio Pathway", "PATHWAY|cesr_portfolio") in sim.get_last_buttons()

    result = await bot.handle_pathway_choice(
        sim._make_callback_update("PATHWAY|cesr_portfolio"),
        context,
    )

    assert result == ConversationHandler.END
    stored = health_profile_store.get_health_profile(sim.user_id)
    assert stored is not None
    assert stored.pathway == Pathway.cesr_portfolio
    assert "CESR / Portfolio Pathway" in sim.get_last_text()


@pytest.mark.asyncio
async def test_cesr_health_output_uses_deterministic_engine_without_llm(monkeypatch):
    import sys
    import bot

    user_id = 5151
    history = [
        {"form_type": "CBD", "filed_at": "2026-05-01 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "DOPS", "filed_at": "2026-05-02 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "QIAT", "filed_at": "2026-05-03 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "TEACH_OBS", "filed_at": "2026-05-04 09:00:00", "status": "failed", "telegram_user_id": user_id},
    ]
    analysis = AsyncMock()

    async def generate_health_chart_async(_user_id):
        return None

    monkeypatch.setitem(
        sys.modules,
        "portfolio_chart",
        SimpleNamespace(generate_health_chart_async=generate_health_chart_async),
    )
    monkeypatch.setattr(bot, "get_health_profile", lambda _user_id: _profile(user_id, Pathway.cesr_portfolio))
    monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST6")
    monkeypatch.setattr(bot, "get_case_history", AsyncMock(return_value=history))
    monkeypatch.setattr(bot, "analyse_portfolio_health", analysis)

    sent: dict[str, str] = {}

    await bot._run_health_analysis(
        user_id=user_id,
        chat=SimpleNamespace(send_action=AsyncMock()),
        send_progress=AsyncMock(),
        send_result=AsyncMock(side_effect=lambda text, reply_markup: sent.setdefault("text", text)),
        send_photo_fn=AsyncMock(),
        fail_fn=AsyncMock(),
    )

    text = sent["text"]
    assert "*Portfolio Health — CESR*" in text
    assert "Health score:" in text
    assert "Domain coverage:" in text
    assert "Gap summary:" in text
    assert "Next actions:" in text
    assert "WPBA count: 2" in text
    assert "CESR requires 36 WPBAs minimum" in text
    analysis.assert_not_called()


@pytest.mark.asyncio
async def test_health_sends_verdict_before_chart(monkeypatch, tmp_path):
    import sys
    import bot

    user_id = 5152
    chart_path = tmp_path / "health.png"
    chart_path.write_bytes(b"fake chart")
    history = [
        {"form_type": "CBD", "filed_at": "2026-05-01 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "DOPS", "filed_at": "2026-05-02 09:00:00", "status": "filed", "telegram_user_id": user_id},
    ]

    async def generate_health_chart_async(_user_id):
        return str(chart_path)

    monkeypatch.setitem(
        sys.modules,
        "portfolio_chart",
        SimpleNamespace(generate_health_chart_async=generate_health_chart_async),
    )
    monkeypatch.setattr(bot, "get_health_profile", lambda _user_id: _profile(user_id, Pathway.cesr_portfolio))
    monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST6")
    monkeypatch.setattr(bot, "get_case_history", AsyncMock(return_value=history))
    monkeypatch.setattr(bot, "analyse_portfolio_health", AsyncMock())

    order: list[str] = []

    async def send_result(_text, _reply_markup):
        order.append("verdict")

    async def send_photo(_fh):
        order.append("chart")

    await bot._run_health_analysis(
        user_id=user_id,
        chat=SimpleNamespace(send_action=AsyncMock()),
        send_progress=AsyncMock(),
        send_result=send_result,
        send_photo_fn=send_photo,
        fail_fn=AsyncMock(),
    )

    assert order == ["verdict", "chart"]
    assert not chart_path.exists()


@pytest.mark.asyncio
async def test_arcp_health_falls_back_to_deterministic_output_when_llm_fails(monkeypatch):
    import sys
    import bot

    user_id = 5252
    history = [
        {"form_type": "CBD", "filed_at": "2026-05-01 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "DOPS", "filed_at": "2026-05-02 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "QIAT", "filed_at": "2026-05-03 09:00:00", "status": "filed", "telegram_user_id": user_id},
    ]

    async def generate_health_chart_async(_user_id):
        return None

    async def fail_analysis(*_args, **_kwargs):
        raise RuntimeError("provider 402")

    monkeypatch.setitem(
        sys.modules,
        "portfolio_chart",
        SimpleNamespace(generate_health_chart_async=generate_health_chart_async),
    )
    monkeypatch.setattr(bot, "get_health_profile", lambda _user_id: _profile(user_id, Pathway.training_arcp))
    monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST6")
    monkeypatch.setattr(bot, "get_case_history", AsyncMock(return_value=history))
    monkeypatch.setattr(bot, "analyse_portfolio_health", fail_analysis)

    sent: dict[str, str] = {}
    fail_fn = AsyncMock()

    await bot._run_health_analysis(
        user_id=user_id,
        chat=SimpleNamespace(send_action=AsyncMock()),
        send_progress=AsyncMock(),
        send_result=AsyncMock(side_effect=lambda text, reply_markup: sent.setdefault("text", text)),
        send_photo_fn=AsyncMock(),
        fail_fn=fail_fn,
    )

    text = sent["text"]
    assert "*Portfolio Health — ARCP*" in text
    assert "AI ARCP narrative is temporarily unavailable" in text
    assert "Health score:" in text
    assert "Domain coverage:" in text
    assert "Gap summary:" in text
    assert "Next actions:" in text
    fail_fn.assert_not_called()


# ── _pathway_for_detected_role / _autoset_health_pathway_from_role ───────────


@pytest.mark.parametrize(
    "detected_role,expected",
    [
        ("sas", Pathway.cesr_portfolio),
        ("hst", Pathway.training_arcp),
        ("accs", Pathway.training_arcp),
        ("accs_intermediate", Pathway.training_arcp),
        ("intermediate", Pathway.training_arcp),
    ],
)
def test_pathway_for_detected_role_maps_confident_roles(detected_role, expected):
    import bot
    assert bot._pathway_for_detected_role(detected_role) is expected


@pytest.mark.parametrize("detected_role", ["unknown", "assessor", "", "garbage"])
def test_pathway_for_detected_role_returns_none_for_ambiguous_roles(detected_role):
    import bot
    assert bot._pathway_for_detected_role(detected_role) is None


def test_autoset_health_pathway_saves_cesr_for_sas(isolated_health_store):
    import bot
    pathway = bot._autoset_health_pathway_from_role(7001, "sas")
    assert pathway is Pathway.cesr_portfolio
    stored = isolated_health_store.get_health_profile(7001)
    assert stored is not None
    assert stored.pathway is Pathway.cesr_portfolio


@pytest.mark.parametrize("detected_role", ["hst", "accs", "accs_intermediate", "intermediate"])
def test_autoset_health_pathway_saves_arcp_for_trainee_roles(isolated_health_store, detected_role):
    import bot
    user_id = 7100 + hash(detected_role) % 100
    pathway = bot._autoset_health_pathway_from_role(user_id, detected_role)
    assert pathway is Pathway.training_arcp
    stored = isolated_health_store.get_health_profile(user_id)
    assert stored is not None
    assert stored.pathway is Pathway.training_arcp


@pytest.mark.parametrize("detected_role", ["unknown", "assessor", ""])
def test_autoset_health_pathway_does_not_save_for_ambiguous_roles(isolated_health_store, detected_role):
    import bot
    pathway = bot._autoset_health_pathway_from_role(7200, detected_role)
    assert pathway is None
    assert isolated_health_store.get_health_profile(7200) is None


def test_autoset_health_pathway_preserves_existing_created_at_and_config(isolated_health_store):
    import bot
    user_id = 7300
    seed_created = datetime(2026, 1, 1, tzinfo=UTC)
    seed = HealthProfile(
        user_id=str(user_id),
        pathway=Pathway.training_arcp,
        pathway_config={"custom": "keep"},
        created_at=seed_created,
        updated_at=seed_created,
    )
    isolated_health_store.save_health_profile(seed)

    pathway = bot._autoset_health_pathway_from_role(user_id, "sas")
    assert pathway is Pathway.cesr_portfolio

    stored = isolated_health_store.get_health_profile(user_id)
    assert stored.pathway is Pathway.cesr_portfolio
    assert stored.pathway_config == {"custom": "keep"}
    assert stored.created_at == seed_created


# ── setup_password integration: Kaizen first-link auto-detection ─────────────


def _patch_setup_password_deps(monkeypatch, detected_role: str):
    """Stub out everything setup_password touches besides health-pathway logic."""
    import bot

    async def _fake_login(_u, _p):
        return detected_role

    monkeypatch.setattr(bot, "_test_kaizen_login", _fake_login)
    monkeypatch.setattr(bot, "store_credentials", lambda *a, **k: None)
    monkeypatch.setattr(bot, "store_training_level", lambda *a, **k: None)
    monkeypatch.setattr(bot, "store_curriculum", lambda *a, **k: None)
    monkeypatch.setattr(bot, "get_curriculum", lambda *_a, **_k: "2025")

    async def _fake_flow_edit(*_a, **_k):
        return None

    monkeypatch.setattr(bot, "_flow_edit", _fake_flow_edit)
    monkeypatch.setattr(bot, "_flow_done", lambda *_a, **_k: None)

    # Block any accidental import of supervisor_workflow during the test —
    # set_role_if_better lives there and exceptions are swallowed by setup_password.
    import sys
    monkeypatch.setitem(
        sys.modules,
        "supervisor_workflow",
        SimpleNamespace(set_role_if_better=lambda *_a, **_k: None),
    )


@pytest.mark.asyncio
async def test_setup_password_autosaves_cesr_pathway_for_sas(isolated_health_store, monkeypatch):
    import bot
    _patch_setup_password_deps(monkeypatch, detected_role="sas")

    sim = BotSimulator(user_id=8001)
    sim.user_data["setup_username"] = "doctor@example.com"
    update = sim._make_text_update("super-secret")
    update.message.delete = AsyncMock()
    context = sim._make_context()

    await bot.setup_password(update, context)

    stored = isolated_health_store.get_health_profile(8001)
    assert stored is not None
    assert stored.pathway is Pathway.cesr_portfolio


@pytest.mark.asyncio
@pytest.mark.parametrize("detected_role", ["hst", "accs", "accs_intermediate", "intermediate"])
async def test_setup_password_autosaves_arcp_for_trainee_roles(
    isolated_health_store, monkeypatch, detected_role
):
    import bot
    _patch_setup_password_deps(monkeypatch, detected_role=detected_role)

    user_id = 8100 + abs(hash(detected_role)) % 100
    sim = BotSimulator(user_id=user_id)
    sim.user_data["setup_username"] = "doctor@example.com"
    update = sim._make_text_update("super-secret")
    update.message.delete = AsyncMock()
    context = sim._make_context()

    await bot.setup_password(update, context)

    stored = isolated_health_store.get_health_profile(user_id)
    assert stored is not None
    assert stored.pathway is Pathway.training_arcp


@pytest.mark.asyncio
@pytest.mark.parametrize("detected_role", ["unknown", "assessor"])
async def test_setup_password_does_not_force_pathway_for_ambiguous_roles(
    isolated_health_store, monkeypatch, detected_role
):
    import bot
    _patch_setup_password_deps(monkeypatch, detected_role=detected_role)

    sim = BotSimulator(user_id=8200)
    sim.user_data["setup_username"] = "doctor@example.com"
    update = sim._make_text_update("super-secret")
    update.message.delete = AsyncMock()
    context = sim._make_context()

    await bot.setup_password(update, context)

    # Safest existing behaviour: no health profile is written, so /health
    # falls back to the default ARCP view and the manual /pathway selector
    # stays authoritative for the user to choose.
    assert isolated_health_store.get_health_profile(8200) is None
