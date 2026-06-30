from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest
from telegram.ext import ConversationHandler

from health_models import HealthDomain, HealthProfile, HealthScore, HealthSnapshot, Pathway
from tests.bot_simulator import BotSimulator


def _snapshot_with_score(
    score: HealthScore,
    *,
    next_actions: list[str] | None = None,
    domain_counts: dict[HealthDomain, int] | None = None,
) -> HealthSnapshot:
    counts = domain_counts or {domain: 5 for domain in HealthDomain}
    return HealthSnapshot(
        user_id="1",
        computed_at=datetime.now(UTC),
        pathway=Pathway.training_arcp,
        health_score=score,
        domain_counts=counts,
        pathway_readiness={},
        gap_summary=[],
        next_actions=next_actions or ["File a CBD from a recent supervised case"],
    )


def _make_pc_mock(snapshot_text: str = "") -> SimpleNamespace:
    """Return a minimal portfolio_chart module stub for tests that do not
    exercise the activity-snapshot text content."""
    async def _chart(*_a, **_k):
        return None

    async def _snapshot(*_a, **_k):
        return snapshot_text

    return SimpleNamespace(
        generate_health_chart_async=_chart,
        format_health_activity_snapshot_async=_snapshot,
    )


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
    assert "Training (CCT)" in text
    assert ("📋 Portfolio defaults", "ACTION|portfolio_defaults") in buttons
    assert [[button.callback_data for button in row] for row in keyboard.inline_keyboard] == [
        ["ACTION|setup"],
        ["ACTION|voice"],
        ["ACTION|portfolio_defaults"],
        ["ACTION|delete"],
    ]


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
    # The pathway picker is a section under Portfolio defaults, so its Back
    # button must return to the Portfolio defaults submenu, not main /settings.
    assert ("🔙 Back to portfolio defaults", "ACTION|portfolio_defaults") in sim.get_last_buttons()
    assert ("🔙 Back to settings", "ACTION|settings") not in sim.get_last_buttons()

    result = await bot.handle_pathway_choice(
        sim._make_callback_update("PATHWAY_SETTINGS|cesr_portfolio"),
        context,
    )

    assert result == ConversationHandler.END
    stored = health_profile_store.get_health_profile(sim.user_id)
    assert stored is not None
    assert stored.pathway == Pathway.cesr_portfolio
    assert "Portfolio (CESR)" in sim.get_last_text()
    assert ("📋 Portfolio defaults", "ACTION|portfolio_defaults") in sim.get_last_buttons()


@pytest.mark.asyncio
async def test_portfolio_defaults_back_button_returns_to_settings(isolated_health_store, monkeypatch):
    """The Portfolio defaults submenu sits directly under main /settings, so its
    Back button must read 'Back to settings' and route to ACTION|settings."""
    import bot

    monkeypatch.setattr(bot, "get_curriculum", lambda _user_id: "2025")
    monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST5")
    monkeypatch.setattr(bot, "get_kaizen_role", lambda _user_id: None)

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|portfolio_defaults"),
        context,
    )

    buttons = sim.get_last_buttons()
    assert ("🔙 Back to settings", "ACTION|settings") in buttons
    # The submenu must not strand the user with a bare "Back" label.
    assert ("🔙 Back", "ACTION|settings") not in buttons


@pytest.mark.asyncio
async def test_change_level_back_button_returns_to_portfolio_defaults(monkeypatch):
    """The Portfolio (training level) section is reached from Portfolio defaults,
    so its Back button must return there — never to main /settings."""
    import bot

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|change_level"),
        context,
    )

    buttons = sim.get_last_buttons()
    assert ("🔙 Back to portfolio defaults", "ACTION|portfolio_defaults") in buttons
    assert ("🔙 Back to settings", "ACTION|settings") not in buttons


@pytest.mark.asyncio
async def test_change_curriculum_back_button_returns_to_portfolio_defaults(monkeypatch):
    """The Curriculum section is reached from Portfolio defaults, so its Back
    button must return there — never to main /settings."""
    import bot

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|change_curriculum"),
        context,
    )

    buttons = sim.get_last_buttons()
    assert ("🔙 Back to portfolio defaults", "ACTION|portfolio_defaults") in buttons
    assert ("🔙 Back to settings", "ACTION|settings") not in buttons


@pytest.mark.asyncio
async def test_change_pathway_back_button_returns_to_portfolio_defaults(isolated_health_store, monkeypatch):
    """The Pathway section is reached from Portfolio defaults, so its Back button
    must return there — never to main /settings."""
    import bot

    sim = BotSimulator(user_id=4242)
    context = sim._make_context()

    await bot.handle_action_button(
        sim._make_callback_update("ACTION|change_pathway"),
        context,
    )

    buttons = sim.get_last_buttons()
    assert ("🔙 Back to portfolio defaults", "ACTION|portfolio_defaults") in buttons
    assert ("🔙 Back to settings", "ACTION|settings") not in buttons


@pytest.mark.asyncio
async def test_health_empty_state_clarifies_portfolio_guru_scope(monkeypatch):
    """Empty state must say cases are absent in Portfolio Guru, not in Kaizen."""
    import bot

    user_id = 5150
    monkeypatch.setattr(bot, "list_evidence_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(bot, "get_case_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(bot, "get_health_profile", lambda _user_id: _profile(user_id, Pathway.training_arcp))
    monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST6")

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
    assert "No Portfolio Guru cases filed yet" in text
    assert "existing Kaizen cases aren't affected" in text
    # Must not read as "you have no cases in Kaizen".
    assert "No cases filed yet." not in text


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

    monkeypatch.setitem(sys.modules, "portfolio_chart", _make_pc_mock())
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
    # No Kaizen index → limited scan, not a readiness verdict.
    assert "Full Kaizen scan not available" in text
    assert "Filing-history snapshot (limited scan)" in text
    assert "Long-term CESR readiness:" not in text
    assert "🔴 Early" not in text
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
async def test_health_includes_activity_snapshot_without_sending_photo(monkeypatch):
    import sys
    import bot

    user_id = 5152
    history = [
        {"form_type": "CBD", "filed_at": "2026-05-01 09:00:00", "status": "filed", "telegram_user_id": user_id},
        {"form_type": "DOPS", "filed_at": "2026-05-02 09:00:00", "status": "filed", "telegram_user_id": user_id},
    ]

    async def format_health_activity_snapshot_async(_user_id, history_6mo=None, training_level=None):
        assert history_6mo == history
        assert training_level == "ST6"
        return (
            "*Activity snapshot*\n"
            "- This month: 2 cases\n"
            "- Form mix: CBD 1, DOPS 1\n"
            "- Curriculum coverage: 3/12 SLOs covered from filed forms\n"
            "- Weekly filings: 1-7 2\n"
            "- Plan: Beta: unlimited\n"
            "- Portfolio level: ST6"
        )

    monkeypatch.setitem(
        sys.modules,
        "portfolio_chart",
        SimpleNamespace(format_health_activity_snapshot_async=format_health_activity_snapshot_async),
    )
    monkeypatch.setattr(bot, "get_health_profile", lambda _user_id: _profile(user_id, Pathway.cesr_portfolio))
    monkeypatch.setattr(bot, "get_training_level", lambda _user_id: "ST6")
    monkeypatch.setattr(bot, "get_case_history", AsyncMock(return_value=history))
    monkeypatch.setattr(bot, "analyse_portfolio_health", AsyncMock())

    sent: dict[str, str] = {}

    async def send_result(text, _reply_markup):
        sent["text"] = text

    send_photo = AsyncMock()

    await bot._run_health_analysis(
        user_id=user_id,
        chat=SimpleNamespace(send_action=AsyncMock()),
        send_progress=AsyncMock(),
        send_result=send_result,
        send_photo_fn=send_photo,
        fail_fn=AsyncMock(),
    )

    assert "*Portfolio Health — CESR / Portfolio Pathway*" in sent["text"]
    assert "*Activity snapshot*" in sent["text"]
    assert "Form mix: CBD 1, DOPS 1" in sent["text"]
    assert "Curriculum coverage: 3/12" in sent["text"]
    send_photo.assert_not_awaited()


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

    async def fail_analysis(*_args, **_kwargs):
        raise RuntimeError("provider 402")

    monkeypatch.setitem(sys.modules, "portfolio_chart", _make_pc_mock())
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
    assert "*Portfolio Health — Training (CCT) evidence scan*" in text
    assert "Training (ARCP)" not in text
    assert "*Evidence basis*" in text
    assert "Scanned: Portfolio Guru filing history only: 3 case(s) in last 6 months" in text
    assert "Window: last 6 months of Portfolio Guru filings only; add your ARCP month to time this to your cycle" in text
    assert "Confidence: low" in text
    assert "AI ARCP narrative is temporarily unavailable" in text
    # No Kaizen index → limited scan, not a red gap-level verdict.
    assert "Full Kaizen scan not available" in text
    assert "Filing-history snapshot (limited scan)" in text
    assert "Evidence gap level:" not in text
    assert "🔴 Red" not in text
    assert "ARCP risk:" not in text
    assert "Next 3 useful filing actions" in text
    assert "before ARCP" not in text
    assert "Visible in this limited scan" in text
    assert "Not seen in this limited scan" in text
    assert "Already strong" not in text
    assert "Missing domains" not in text
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

    monkeypatch.setitem(sys.modules, "portfolio_chart", _make_pc_mock())
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
    assert "*Portfolio Health — Training (CCT) evidence scan*" in text
    assert "Training (ARCP)" not in text
    assert "*Evidence basis*" in text
    assert "Scanned: Portfolio Guru filing history only: 3 case(s) in last 6 months" in text
    assert "Window: last 6 months of Portfolio Guru filings only; add your ARCP month to time this to your cycle" in text
    # No Kaizen index → limited scan, not a red gap-level verdict.
    assert "Full Kaizen scan not available" in text
    assert "Filing-history snapshot (limited scan)" in text
    assert "Evidence gap level:" not in text
    assert "🔴 Red" not in text
    assert "ARCP risk:" not in text
    assert "Next 3 useful filing actions" in text
    assert "before ARCP" not in text
    assert "Visible in this limited scan" in text
    assert "Not seen in this limited scan" in text
    assert "Already strong" not in text
    assert "Missing domains" not in text
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

    monkeypatch.setitem(sys.modules, "portfolio_chart", _make_pc_mock())
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
async def test_health_default_pathway_is_labelled_as_assumed(monkeypatch):
    import sys
    import bot

    user_id = 6000
    history = _history_for_user(user_id)

    monkeypatch.setitem(sys.modules, "portfolio_chart", _make_pc_mock())
    monkeypatch.setattr(bot, "get_health_profile", lambda _user_id: None)
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

    assert "Assumed pathway: Training (CCT) — change if wrong" in sent["text"]
    assert "Pathway: default Training (CCT)" not in sent["text"]


@pytest.mark.asyncio
async def test_arcp_and_cesr_pathway_outputs_diverge_in_lead_framing(monkeypatch):
    """Same evidence, different pathway. With no Kaizen index both lead with the
    sync-needed banner, but their bodies still diverge: ARCP shows the next
    useful filing actions; CESR shows a yearly evidence plan and WPBA progress.
    """
    arcp_text = await _run_health_capture(monkeypatch, 6001, Pathway.training_arcp)
    cesr_text = await _run_health_capture(monkeypatch, 6002, Pathway.cesr_portfolio)

    # Both lead with the limited-scan banner (no full verdict).
    assert "Full Kaizen scan not available" in arcp_text
    assert "Full Kaizen scan not available" in cesr_text
    assert "Evidence gap level:" not in arcp_text
    assert "Long-term CESR readiness:" not in cesr_text

    # Training (CCT) pathway framing — ARCP is a checkpoint inside this pathway,
    # not a standalone pathway label.
    assert "Training (CCT) evidence scan" in arcp_text
    assert "ARCP evidence review" not in arcp_text
    assert "Training (ARCP)" not in arcp_text
    assert "Next 3 useful filing actions" in arcp_text
    # ARCP must NOT carry CESR / yearly-plan framing
    assert "CESR" not in arcp_text
    assert "this year" not in arcp_text.lower()
    assert "5-year" not in arcp_text.lower()
    assert "yearly" not in arcp_text.lower()

    # CESR framing
    assert "CESR / Portfolio Pathway" in cesr_text
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
    # Limited scan (no Kaizen index) → "Not seen", not full-portfolio "Missing domains".
    assert "Not seen in this limited scan" in cesr_text
    assert "Missing domains" not in cesr_text
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


def test_health_is_in_command_menu_and_still_typeable():
    """Portfolio Health is a primary beta command and must appear in the
    Telegram slash-command menu as well as remain directly typeable."""
    import bot

    commands = {command: description for command, description in bot.BOT_COMMANDS}

    assert commands["health"] == "View portfolio health and evidence gaps"
    assert callable(bot.health_command)


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


# ── ESLE / urgency consistency with the deterministic health score ───────────


_URGENCY_WORDS = ("urgent", "urgently", "critical", "critically", "immediately", "asap")


def test_green_arcp_report_softens_urgent_esle_suggestion():
    """A Green report must never carry urgent missing-evidence copy. An LLM
    'Urgently schedule an ESLE for SLO8' suggestion must be reframed as an
    optional/confirmatory ESLE action so the status and the copy agree."""
    import bot

    snapshot = _snapshot_with_score(HealthScore.green)
    msg = bot._format_arcp_action_plan_message(
        snapshot=snapshot,
        history=[],
        month_label="June 2026",
        analysis={"suggestions": ["Urgently schedule an ESLE to cover SLO8"]},
        limited_view=False,
    )

    assert "🟢 Green" in msg
    lowered = msg.lower()
    for word in _URGENCY_WORDS:
        assert word not in lowered, f"Green report must not contain urgent copy: {word!r}"
    # ESLE is still surfaced, but framed as optional/confirmatory.
    assert "ESLE" in msg
    assert "Consider" in msg
    assert "already evidenced" in msg


def test_green_arcp_report_softens_generic_urgent_suggestion():
    """Non-ESLE urgent suggestions are softened too — Green leaves no urgency."""
    import bot

    snapshot = _snapshot_with_score(HealthScore.green)
    msg = bot._format_arcp_action_plan_message(
        snapshot=snapshot,
        history=[],
        month_label="June 2026",
        analysis={"suggestions": ["Urgently add a Mini-CEX"]},
        limited_view=False,
    )

    assert "🟢 Green" in msg
    lowered = msg.lower()
    for word in _URGENCY_WORDS:
        assert word not in lowered
    # The underlying action survives, just without the urgent qualifier.
    assert "Mini-CEX" in msg


_CRISIS_PHRASES = (
    "recovery plan",
    "severe lack",
    "severe lack of portfolio progression",
    "crisis",
    "remediation",
    "remediate",
    "failing",
)


def test_green_arcp_report_strips_crisis_remediation_language():
    """A Green report must never imply failing progression. LLM crisis framing
    like 'Start a recovery plan for the severe lack of portfolio progression'
    must be replaced with a neutral confirmatory action so the status and copy
    agree."""
    import bot

    snapshot = _snapshot_with_score(HealthScore.green)
    msg = bot._format_arcp_action_plan_message(
        snapshot=snapshot,
        history=[],
        month_label="June 2026",
        analysis={
            "suggestions": [
                "Urgently start a recovery plan for the severe lack of portfolio progression",
                "Immediate remediation required — you are failing progression",
            ]
        },
        limited_view=False,
    )

    assert "🟢 Green" in msg
    lowered = msg.lower()
    for phrase in _CRISIS_PHRASES:
        assert phrase not in lowered, f"Green report must not contain crisis copy: {phrase!r}"
    for word in _URGENCY_WORDS:
        assert word not in lowered, f"Green report must not contain urgent copy: {word!r}"
    # The neutral confirmatory action takes its place.
    assert "Keep your existing evidence recent" in msg


def test_reconcile_action_severity_replaces_crisis_phrases_on_grey():
    """Grey (not-enough-data) gets the same crisis-language guard as Green."""
    import bot

    reconciled = bot._reconcile_action_severity(
        ["Begin a recovery plan to address severe lack of portfolio progression"],
        HealthScore.grey,
    )
    assert reconciled == [
        "Keep your existing evidence recent and confirm coverage before your next review"
    ]


def test_amber_arcp_report_keeps_urgent_esle_priority_wording():
    """When the status is Amber the engine has flagged a real gap, so priority
    ESLE wording matches the severity and must be preserved."""
    import bot

    snapshot = _snapshot_with_score(
        HealthScore.amber,
        next_actions=["Add leadership or management evidence"],
        domain_counts={
            HealthDomain.clinical: 5,
            HealthDomain.cpd: 3,
            HealthDomain.qi: 2,
            HealthDomain.teaching: 0,
            HealthDomain.leadership: 0,
            HealthDomain.reflection: 1,
            HealthDomain.unclassified: 0,
        },
    )
    msg = bot._format_arcp_action_plan_message(
        snapshot=snapshot,
        history=[],
        month_label="June 2026",
        analysis={"suggestions": ["Urgently schedule an ESLE to cover SLO8"]},
        limited_view=False,
    )

    assert "🟡 Amber" in msg
    assert "Urgently schedule an ESLE" in msg


def test_reconcile_action_severity_is_noop_for_amber_and_red():
    import bot

    actions = [
        "Urgently schedule an ESLE",
        "Critically review QI",
        "Start a recovery plan for the severe lack of portfolio progression",
    ]
    assert bot._reconcile_action_severity(actions, HealthScore.amber) == actions
    assert bot._reconcile_action_severity(actions, HealthScore.red) == actions


# ── Evidence basis / Domain detail button-page copy ──────────────────────────


def test_evidence_basis_shows_last_scanned_and_arcp_month_setup_prompt():
    """The Evidence basis page must show a concise 'Last scanned' line when a
    Kaizen index timestamp is available, and frame the missing ARCP month as a
    setup prompt rather than a warning-like defect ('not set yet')."""
    import bot
    from kaizen_index import IndexRunRow, KaizenSyncStatus

    status = KaizenSyncStatus(
        last_run=IndexRunRow(
            id=1,
            user_id="4242",
            started_at="2026-06-01T11:30:00",
            finished_at="2026-06-01T11:38:00",
            status="ok",
            rows_seen=412,
            rows_written=412,
            rows_drifted=0,
        ),
        items_indexed=412,
    )

    context = bot._format_health_evidence_context(
        source="kaizen_index",
        evidence_count=412,
        history_count=3,
        profile_is_default=False,
        sync_status=status,
        pathway=Pathway.training_arcp,
    )

    assert context.startswith("*Evidence basis*")
    assert "Last scanned: 2026-06-01 12:38 BST" in context
    assert "add your ARCP month to time this to your cycle" in context
    assert "not set yet" not in context


def test_evidence_basis_omits_last_scanned_when_no_sync_run():
    """Without a Kaizen index run there is nothing to date, so the Last scanned
    line must be suppressed rather than printing a placeholder."""
    import bot

    context = bot._format_health_evidence_context(
        source="case_history",
        evidence_count=2,
        history_count=2,
        profile_is_default=False,
        sync_status=None,
        pathway=Pathway.training_arcp,
    )

    assert context.startswith("*Evidence basis*")
    assert "Last scanned" not in context


def test_arcp_domain_detail_uses_visible_coverage_heading_and_title_case():
    """The non-limited Domain detail must use the neutral 'Visible domain
    coverage' heading (not the verdict-style 'Already strong') and title-case
    the domain labels while preserving acronyms."""
    import bot

    snapshot = _snapshot_with_score(
        HealthScore.amber,
        domain_counts={
            HealthDomain.clinical: 2,
            HealthDomain.cpd: 1,
            HealthDomain.qi: 0,
            HealthDomain.teaching: 0,
            HealthDomain.leadership: 0,
            HealthDomain.reflection: 0,
            HealthDomain.unclassified: 0,
        },
    )

    full_text = bot._format_arcp_action_plan_message(
        snapshot=snapshot,
        history=[],
        month_label="June 2026",
        limited_view=False,
    )

    assert "*Visible domain coverage*" in full_text
    assert "Already strong" not in full_text
    assert "• Clinical: 2" in full_text
    assert "• CPD: 1" in full_text

    sections = bot._health_report_sections(full_text)
    domains = sections["domains"]
    assert domains.startswith("📋 *Domain detail*")
    assert "*Visible domain coverage*" in domains
    assert "• Clinical: 2" in domains


# ── Weekly digest: caption composition ────────────────────────────────────────


def test_weekly_digest_caption_empty_state():
    import bot

    text = bot._build_weekly_digest_text({"cases": 0, "gap": None})
    assert "No Portfolio Guru cases" in text
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


# ── format_health_activity_snapshot: text-only chart-data coverage ─────────────


def test_format_health_activity_snapshot_contains_all_chart_panel_fields():
    """format_health_activity_snapshot must include the text equivalent of all
    four chart panels: form types this month, SLO/curriculum coverage, weekly
    filing distribution, and the activity/plan summary.

    This is the invariant that keeps the text-only /health output as useful as
    the chart image it replaced.
    """
    from datetime import datetime

    import portfolio_chart

    now = datetime.now()
    this_month = now.strftime("%Y-%m")
    prior_month = f"{now.year - 1}-12" if now.month == 1 else f"{now.year}-{now.month - 1:02d}"

    # Mix of current-month and prior-month entries so the function can
    # distinguish "this month" form types from the 6-month SLO coverage.
    history = [
        {"form_type": "CBD", "filed_at": f"{this_month}-10 09:00:00"},
        {"form_type": "DOPS", "filed_at": f"{this_month}-15 09:00:00"},
        {"form_type": "MINI_CEX", "filed_at": f"{prior_month}-20 09:00:00"},
        {"form_type": "TEACH", "filed_at": f"{prior_month}-05 09:00:00"},
    ]

    text = portfolio_chart.format_health_activity_snapshot(
        history_6mo=history,
        cases_this_month=2,
        tier="pro_plus",
        limit=-1,
        training_level="ST5",
        kc_coverage={1: ["KC1.1"], 3: ["KC3.2"]},
        kc_stats={"total_kcs": 2, "slos_covered": 2, "slos_total": 12, "recent_kcs": []},
    )

    # Panel 1: this month by form type
    assert "Form mix" in text or "form" in text.lower()
    assert "CBD" in text

    # Panel 2: curriculum / SLO coverage (last 6 months)
    assert "SLO" in text or "coverage" in text.lower()
    assert "/12" in text

    # Panel 3: weekly filing distribution
    assert "Weekly" in text or "weekly" in text

    # Panel 4: activity summary — no plan/billing copy belongs here
    assert "2" in text  # cases this month
    assert "ST5" in text
    assert "Plan:" not in text
    # Curriculum coverage must clarify it is Portfolio Guru-linked evidence,
    # not a measure of the user's full Kaizen portfolio strength.
    assert "not your full Kaizen strength" in text

    # KC stats row
    assert "KC" in text or "KCs" in text
