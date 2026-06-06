from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest
from telegram.ext import ConversationHandler

from health_models import HealthProfile, Pathway
from tests.bot_simulator import BotSimulator


def test_pathway_is_hidden_from_telegram_command_menu_but_still_typeable():
    import bot

    assert all(command != "pathway" for command, _ in bot.BOT_COMMANDS)
    assert callable(bot.pathway_command)


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
    assert ("Training (CCT)", "PATHWAY|training_arcp") in sim.get_last_buttons()
    assert ("Portfolio (CESR)", "PATHWAY|cesr_portfolio") in sim.get_last_buttons()

    result = await bot.handle_pathway_choice(
        sim._make_callback_update("PATHWAY|cesr_portfolio"),
        context,
    )

    assert result == ConversationHandler.END
    stored = health_profile_store.get_health_profile(sim.user_id)
    assert stored is not None
    assert stored.pathway == Pathway.cesr_portfolio
    assert "Portfolio (CESR)" in sim.get_last_text()


def test_settings_shows_pathway_change_control(isolated_health_store, monkeypatch):
    import bot

    monkeypatch.setattr(bot, "get_curriculum", lambda _user_id: "2025")
    monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST5")
    monkeypatch.setattr(bot, "get_voice_profile", lambda _user_id: None)

    text, keyboard = bot._settings_view_components(
        4242,
        tier="pro_plus",
        used=0,
        connected=True,
    )

    buttons = [(button.text, button.callback_data) for row in keyboard.inline_keyboard for button in row]
    assert "Pathway: Training (CCT)" in text
    assert ("📊 Pathway: Training (CCT)", "ACTION|change_pathway") in buttons


@pytest.mark.asyncio
async def test_settings_pathway_change_saves_and_returns_to_settings(isolated_health_store, monkeypatch):
    import bot
    health_profile_store = isolated_health_store

    monkeypatch.setattr(bot, "get_curriculum", lambda _user_id: "2025")
    monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST5")
    monkeypatch.setattr(bot, "get_voice_profile", lambda _user_id: None)
    monkeypatch.setattr(bot, "has_credentials", lambda _user_id: True)
    monkeypatch.setattr(bot, "get_cases_this_month", AsyncMock(return_value=0))
    monkeypatch.setattr(bot, "get_user_tier", AsyncMock(return_value="pro_plus"))
    monkeypatch.setattr(bot, "is_beta_tester", AsyncMock(return_value=False))

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|change_pathway"),
        context,
    )

    assert ("Training (CCT)", "PATHWAY_SETTINGS|training_arcp") in sim.get_last_buttons()
    assert ("Portfolio (CESR)", "PATHWAY_SETTINGS|cesr_portfolio") in sim.get_last_buttons()
    assert ("🔙 Back to settings", "ACTION|settings") in sim.get_last_buttons()

    result = await bot.handle_pathway_choice(
        sim._make_callback_update("PATHWAY_SETTINGS|cesr_portfolio"),
        context,
    )

    assert result == ConversationHandler.END
    stored = health_profile_store.get_health_profile(sim.user_id)
    assert stored is not None
    assert stored.pathway == Pathway.cesr_portfolio
    assert "Pathway: Portfolio (CESR)" in sim.get_last_text()
    assert ("📊 Pathway: Portfolio (CESR)", "ACTION|change_pathway") in sim.get_last_buttons()


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
    assert "*Portfolio Health — CESR / Portfolio Pathway*" in text
    assert "Long-term CESR readiness:" in text
    assert "WPBA progress toward 36" in text
    assert "2/36" in text
    assert "DOPS 1/12" in text
    assert "Mini-CEX 0/12" in text
    assert "CBD 1/12" in text
    assert "This year's evidence plan" in text
    assert "5-year evidence window" in text
    assert "Evidence window:" in text
    assert "ARCP" not in text
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
    assert "*Portfolio Health — Training (CCT) pathway · ARCP readiness check*" in text
    assert "Training (ARCP)" not in text
    assert "AI ARCP narrative is temporarily unavailable" in text
    assert "ARCP risk:" in text
    assert "Why:" in text
    assert "Next 3 urgent filing actions before ARCP" in text
    assert "Already strong" in text
    assert "Missing domains" in text
    assert "Domain coverage:" not in text
    fail_fn.assert_not_called()


@pytest.mark.asyncio
async def test_arcp_health_output_prioritises_action_plan_when_llm_succeeds(monkeypatch):
    import sys
    import bot

    user_id = 5253
    history = [
        {"form_type": "CBD", "filed_at": "2026-05-01 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "DOPS", "filed_at": "2026-05-02 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "REFLECT_LOG", "filed_at": "2026-05-03 09:00:00", "status": "filed", "telegram_user_id": user_id},
    ]

    async def generate_health_chart_async(_user_id):
        return None

    monkeypatch.setitem(
        sys.modules,
        "portfolio_chart",
        SimpleNamespace(generate_health_chart_async=generate_health_chart_async),
    )
    monkeypatch.setattr(bot, "get_health_profile", lambda _user_id: _profile(user_id, Pathway.training_arcp))
    monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST6")
    monkeypatch.setattr(bot, "get_case_history", AsyncMock(return_value=history))
    monkeypatch.setattr(
        bot,
        "analyse_portfolio_health",
        AsyncMock(return_value={"suggestions": ["Book a supervisor review"]}),
    )

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
    assert "*Portfolio Health — Training (CCT) pathway · ARCP readiness check*" in text
    assert "Training (ARCP)" not in text
    assert "ARCP risk:" in text
    assert "Why:" in text
    assert "Next 3 urgent filing actions before ARCP" in text
    assert "Already strong" in text
    assert "Missing domains" in text
    assert "CPD" in text
    assert "QI" in text
    assert "Form types:" not in text
    assert "Domain coverage:" not in text
    assert "CESR" not in text
    assert "yearly" not in text.lower()


# ── _pathway_for_detected_role / _autoset_health_pathway_from_role ───────────


@pytest.mark.parametrize(
    "detected_role,expected",
    [
        ("sas", Pathway.cesr_portfolio),
        ("non_training_higher", Pathway.cesr_portfolio),
        ("non_training_unknown", Pathway.cesr_portfolio),
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


@pytest.mark.parametrize("detected_role", ["non_training_higher", "non_training_unknown"])
def test_autoset_health_pathway_saves_cesr_for_non_training_roles(
    isolated_health_store, detected_role
):
    import bot
    user_id = 7050 + abs(hash(detected_role)) % 100
    pathway = bot._autoset_health_pathway_from_role(user_id, detected_role)
    assert pathway is Pathway.cesr_portfolio
    stored = isolated_health_store.get_health_profile(user_id)
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
@pytest.mark.parametrize("detected_role", ["non_training_higher", "non_training_unknown"])
async def test_setup_password_autosaves_cesr_for_non_training_roles(
    isolated_health_store, monkeypatch, detected_role
):
    import bot
    _patch_setup_password_deps(monkeypatch, detected_role=detected_role)

    user_id = 8050 + abs(hash(detected_role)) % 100
    sim = BotSimulator(user_id=user_id)
    sim.user_data["setup_username"] = "doctor@example.com"
    update = sim._make_text_update("super-secret")
    update.message.delete = AsyncMock()
    context = sim._make_context()

    await bot.setup_password(update, context)

    stored = isolated_health_store.get_health_profile(user_id)
    assert stored is not None
    assert stored.pathway is Pathway.cesr_portfolio


@pytest.mark.asyncio
async def test_setup_password_clears_local_health_sources_on_kaizen_account_switch(
    isolated_health_store, monkeypatch
):
    import bot
    _patch_setup_password_deps(monkeypatch, detected_role="non_training_higher")
    clear_account_data = AsyncMock(return_value={})
    monkeypatch.setattr(bot, "get_credentials", lambda _uid: ("moeed@example.com", "old"))
    monkeypatch.setattr(bot, "_clear_local_portfolio_account_data", clear_account_data)

    sim = BotSimulator(user_id=8070)
    sim.user_data["setup_username"] = "sana@example.com"
    update = sim._make_text_update("super-secret")
    update.message.delete = AsyncMock()
    context = sim._make_context()

    await bot.setup_password(update, context)

    clear_account_data.assert_awaited_once_with(8070, reason="kaizen_account_switch")
    stored = isolated_health_store.get_health_profile(8070)
    assert stored is not None
    assert stored.pathway is Pathway.cesr_portfolio


@pytest.mark.asyncio
async def test_setup_password_continues_when_health_pathway_save_fails(monkeypatch):
    import bot
    _patch_setup_password_deps(monkeypatch, detected_role="hst")
    monkeypatch.setattr(
        bot,
        "_autoset_health_pathway_from_role",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("blocked")),
    )

    sim = BotSimulator(user_id=8060)
    sim.user_data["setup_username"] = "doctor@example.com"
    update = sim._make_text_update("super-secret")
    update.message.delete = AsyncMock()
    context = sim._make_context()

    result = await bot.setup_password(update, context)

    assert result == bot.ConversationHandler.END


@pytest.mark.asyncio
async def test_setup_password_clears_local_health_context_when_kaizen_username_changes(monkeypatch):
    import bot
    _patch_setup_password_deps(monkeypatch, detected_role="non_training_higher")

    cleared = AsyncMock(return_value={})
    stored = []
    monkeypatch.setattr(bot, "get_credentials", lambda _uid: ("moeed@example.com", "old-secret"))
    monkeypatch.setattr(bot, "_clear_local_portfolio_account_data", cleared)
    monkeypatch.setattr(bot, "store_credentials", lambda *args: stored.append(args))

    sim = BotSimulator(user_id=8065)
    sim.user_data["setup_username"] = "sana@example.com"
    update = sim._make_text_update("super-secret")
    update.message.delete = AsyncMock()
    context = sim._make_context()

    result = await bot.setup_password(update, context)

    assert result == bot.ConversationHandler.END
    cleared.assert_awaited_once_with(8065, reason="kaizen_account_switch")
    assert stored == [(8065, "sana@example.com", "super-secret")]


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


# ── ARCP vs CESR pathway-aware output divergence ─────────────────────────────


def _history_for_user(user_id: int) -> list[dict]:
    return [
        {"form_type": "CBD", "filed_at": "2026-05-01 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "DOPS", "filed_at": "2026-05-02 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "MINI_CEX", "filed_at": "2026-05-03 09:00:00", "status": "filed", "telegram_user_id": user_id},
    ]


async def _run_health_capture(monkeypatch, user_id: int, pathway: Pathway) -> str:
    import sys
    import bot

    history = _history_for_user(user_id)

    async def generate_health_chart_async(_user_id):
        return None

    monkeypatch.setitem(
        sys.modules,
        "portfolio_chart",
        SimpleNamespace(generate_health_chart_async=generate_health_chart_async),
    )
    monkeypatch.setattr(bot, "get_health_profile", lambda _user_id: _profile(user_id, pathway))
    monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST6")
    monkeypatch.setattr(bot, "get_case_history", AsyncMock(return_value=history))
    monkeypatch.setattr(
        bot,
        "analyse_portfolio_health",
        AsyncMock(return_value={"suggestions": ["Book a supervisor review"]}),
    )

    sent: dict[str, str] = {}

    await bot._run_health_analysis(
        user_id=user_id,
        chat=SimpleNamespace(send_action=AsyncMock()),
        send_progress=AsyncMock(),
        send_result=AsyncMock(side_effect=lambda text, reply_markup: sent.setdefault("text", text)),
        send_photo_fn=AsyncMock(),
        fail_fn=AsyncMock(),
    )
    return sent["text"]


@pytest.mark.asyncio
async def test_arcp_and_cesr_pathway_outputs_diverge_in_lead_framing(monkeypatch):
    """Same evidence, different pathway. The lead framing must diverge:
    ARCP leads with ARCP risk and the next 3 urgent filing actions; CESR
    leads with long-term readiness and a yearly evidence plan.
    """
    arcp_text = await _run_health_capture(monkeypatch, 6001, Pathway.training_arcp)
    cesr_text = await _run_health_capture(monkeypatch, 6002, Pathway.cesr_portfolio)

    # Training (CCT) pathway framing — ARCP is a checkpoint inside this pathway,
    # not a standalone pathway label.
    assert "Training (CCT) pathway" in arcp_text
    assert "ARCP readiness check" in arcp_text
    assert "Training (ARCP)" not in arcp_text
    assert "ARCP risk:" in arcp_text
    assert "Next 3 urgent filing actions before ARCP" in arcp_text
    # ARCP must NOT carry CESR / yearly-plan framing
    assert "CESR" not in arcp_text
    assert "this year" not in arcp_text.lower()
    assert "5-year" not in arcp_text.lower()
    assert "yearly" not in arcp_text.lower()

    # CESR framing
    assert "CESR / Portfolio Pathway" in cesr_text
    assert "Long-term CESR readiness:" in cesr_text
    assert "This year's evidence plan" in cesr_text
    assert "WPBA progress toward 36" in cesr_text
    assert "5-year evidence window" in cesr_text
    # CESR must NOT carry ARCP-deadline framing
    assert "ARCP risk" not in cesr_text
    assert "next ARCP" not in cesr_text.lower()
    assert "before ARCP" not in cesr_text
    assert "ARCP" not in cesr_text


@pytest.mark.asyncio
async def test_cesr_message_contains_long_term_and_domain_balance(monkeypatch):
    cesr_text = await _run_health_capture(monkeypatch, 6003, Pathway.cesr_portfolio)

    assert "Domain balance" in cesr_text
    assert "Missing domains" in cesr_text
    assert "consultant report" in cesr_text.lower()
    # Long-term framing wording
    assert "multi-year" in cesr_text or "long-term" in cesr_text.lower()
    # Evidence-window framing present
    assert "Evidence window:" in cesr_text


def test_health_paywall_copy_is_pathway_neutral():
    """The /health paywall must not promise ARCP-only analysis, and must not
    label ARCP as a pathway. ARCP is a checkpoint inside the Training (CCT)
    pathway."""
    import inspect
    import bot

    src = inspect.getsource(bot.health_command)
    assert "monthly ARCP readiness analysis" not in src
    # "training (ARCP)" frames ARCP as the pathway label — corrected model
    # is Training (CCT) with ARCP as a checkpoint.
    assert "training (ARCP)" not in src.lower()
    assert ("CESR" in src) or ("portfolio readiness" in src.lower())


@pytest.mark.asyncio
async def test_pathway_command_describes_arcp_as_checkpoint_not_pathway(isolated_health_store):
    """The /pathway selector must present Training (CCT) and CESR / Portfolio
    Pathway as the two pathways. ARCP, if mentioned, must read as a
    checkpoint/review/readiness check inside Training (CCT), never as a
    pathway label such as "Training (ARCP)" or "choose ARCP"."""
    import bot

    sim = BotSimulator(user_id=4243)
    context = sim._make_context()

    await bot.pathway_command(sim._make_text_update("/pathway"), context)

    text = sim.get_last_text()
    buttons = sim.get_last_buttons()

    # Pathways: Training (CCT) and Portfolio (CESR)
    assert "Training (CCT)" in text
    assert ("CESR" in text) or ("Portfolio Pathway" in text)
    assert ("Training (CCT)", "PATHWAY|training_arcp") in buttons
    assert ("Portfolio (CESR)", "PATHWAY|cesr_portfolio") in buttons

    # Forbidden framings — ARCP as a pathway label
    assert "Training (ARCP)" not in text
    assert "ARCP pathway" not in text
    assert "ARCP path" not in text
    assert "choose ARCP" not in text.lower()

    # If ARCP is mentioned at all, it must be described as a checkpoint /
    # review / readiness check, i.e. as something that sits *inside* the
    # Training (CCT) pathway.
    if "ARCP" in text:
        assert any(
            word in text.lower()
            for word in ("checkpoint", "review", "readiness check")
        ), f"ARCP must read as a checkpoint inside Training (CCT); got: {text!r}"


def test_bot_commands_health_description_is_not_arcp_only():
    """The /health command description in the Telegram menu must not present
    the feature as ARCP-only — Portfolio Health is pathway-aware."""
    import bot

    health_desc = next(
        (description for command, description in bot.BOT_COMMANDS if command == "health"),
        None,
    )
    assert health_desc is not None
    assert "ARCP analysis" not in health_desc
    assert "ARCP" not in health_desc or "checkpoint" in health_desc.lower()


def test_upgrade_copy_calls_feature_portfolio_health_not_arcp_health():
    """The upgrade / paywall copy must list the feature as Portfolio Health
    (pathway-aware), not as "ARCP Health" — which would imply the feature is
    only for the ARCP checkpoint."""
    import inspect
    import bot

    src = inspect.getsource(bot.upgrade_command)
    assert "ARCP Health" not in src
    assert "ARCP health" not in src
    assert "Portfolio Health" in src


def test_pathway_for_detected_role_docstring_uses_training_cct_not_arcp():
    """The role→pathway mapping docstring must describe the trainee pathway
    as Training (CCT), not as ARCP. ARCP is a checkpoint inside that
    pathway, not the pathway itself."""
    import bot

    doc = bot._pathway_for_detected_role.__doc__ or ""
    assert "Training (CCT)" in doc
    # Forbid framings that present ARCP as a pathway / destination label
    assert "→ ARCP" not in doc
    assert "ARCP pathway" not in doc


# ── Weekly digest: caption composition ────────────────────────────────────────


def test_weekly_digest_caption_empty_state():
    import bot

    text = bot._build_weekly_digest_text({"cases": 0, "gap": None})
    assert "No cases" in text
    assert "/health" in text
    assert len(text) < 200


def test_weekly_digest_caption_with_gap():
    import bot

    text = bot._build_weekly_digest_text({
        "cases": 9,
        "gap": ("Procedure Log", 23),
    })
    assert "Strong week" in text
    assert "Procedure Log" in text
    assert "23 days" in text
    assert "/health" in text
    assert len(text) < 200


def test_weekly_digest_caption_no_gap_but_filed():
    import bot

    text = bot._build_weekly_digest_text({"cases": 5, "gap": None})
    assert "Solid week" in text
    assert "No major gaps" in text
    assert "/health" in text
    assert len(text) < 200


def test_weekly_digest_caption_is_pathway_neutral():
    """Weekly digest caption must not reference ARCP, CESR, or any
    pathway-specific career framing."""
    import bot

    for stats in (
        {"cases": 0, "gap": None},
        {"cases": 9, "gap": ("Mini-CEX", 14)},
        {"cases": 5, "gap": None},
    ):
        text = bot._build_weekly_digest_text(stats)
        assert "ARCP" not in text
        assert "CESR" not in text
        assert "CCT" not in text
        assert "training pathway" not in text.lower()


def test_weekly_digest_caption_under_200_chars():
    """Every caption variant must stay under 200 characters — it is a
    nudge, not a dashboard."""
    import bot

    scenarios = [
        {"cases": 0, "gap": None},
        {"cases": 1, "gap": None},
        {"cases": 12, "gap": None},
        {"cases": 9, "gap": ("Procedure Log", 23)},
        {"cases": 3, "gap": ("DOPS", 7)},
    ]
    for stats in scenarios:
        text = bot._build_weekly_digest_text(stats)
        assert len(text) < 200, f"Caption too long ({len(text)} chars) for {stats}: {text!r}"


# ── Weekly nudge chart — rendering ───────────────────────────────────────────


def test_weekly_nudge_chart_functions_exist():
    import portfolio_chart

    assert callable(getattr(portfolio_chart, "generate_weekly_nudge_chart_async", None))
    assert callable(getattr(portfolio_chart, "_render_nudge_card", None))
    assert callable(getattr(portfolio_chart, "_nudge_line_win", None))
    assert callable(getattr(portfolio_chart, "_nudge_line_signal", None))
    assert callable(getattr(portfolio_chart, "_nudge_line_deficiency_and_action", None))


def test_nudge_line_win_formats_correctly():
    import portfolio_chart

    assert "9 cases" in portfolio_chart._nudge_line_win(9)
    assert "1 case" in portfolio_chart._nudge_line_win(1)
    assert "0 cases" in portfolio_chart._nudge_line_win(0)


def test_nudge_line_signal_with_top_form():
    import portfolio_chart

    line = portfolio_chart._nudge_line_signal(8, ("CBD", 4))
    assert "8 form types" in line
    assert "CBD" in line
    assert "(4)" in line


def test_nudge_line_signal_empty():
    import portfolio_chart

    line = portfolio_chart._nudge_line_signal(0, None)
    assert "No filings" in line


def test_nudge_line_deficiency_and_action_with_gap():
    import portfolio_chart

    deficiency, action = portfolio_chart._nudge_line_deficiency_and_action(
        ("Procedure Log", 23)
    )
    assert "Procedure Log" in deficiency
    assert "23 days" in deficiency
    assert "Procedure Log" in action
    assert "/health" in action


def test_nudge_line_deficiency_and_action_no_gap():
    import portfolio_chart

    deficiency, action = portfolio_chart._nudge_line_deficiency_and_action(None)
    assert deficiency == ""
    assert "/health" in action


def test_render_nudge_card_creates_png():
    import tempfile
    import os
    import portfolio_chart

    path = portfolio_chart._render_nudge_card(
        user_id=999999,
        cases_this_week=9,
        form_types_this_month=8,
        top_form=("CBD", 3),
        gap=("Procedure Log", 23),
    )
    assert path.endswith(".png")
    assert os.path.getsize(path) > 500
    os.unlink(path)


def test_render_nudge_card_empty_state():
    import tempfile
    import os
    import portfolio_chart

    path = portfolio_chart._render_nudge_card(
        user_id=999999,
        cases_this_week=0,
        form_types_this_month=0,
        top_form=None,
        gap=None,
    )
    assert path.endswith(".png")
    assert os.path.getsize(path) > 500
    os.unlink(path)


def test_render_nudge_card_no_gap():
    import tempfile
    import os
    import portfolio_chart

    path = portfolio_chart._render_nudge_card(
        user_id=999999,
        cases_this_week=5,
        form_types_this_month=3,
        top_form=None,
        gap=None,
    )
    assert path.endswith(".png")
    assert os.path.getsize(path) > 500
    os.unlink(path)
