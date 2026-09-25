"""No-network proofs for whole-bot traversal and effect policy."""
from types import SimpleNamespace
import json
import pytest
from tests.telegram_live_harness import explore_whole_bot, ExplorationLimits
from tests.telegram_live_policy import command_roots, control_policy


def test_command_policy_requires_exact_registration():
    with pytest.raises(AssertionError, match="command"):
        command_roots(["new_command"])


def test_protected_controls_are_payload_based():
    assert control_policy("APPROVE|draft", "", {}) == "protected"
    assert control_policy("ACTION|retry_filing", "", {}) == "protected"
    with pytest.raises(AssertionError, match="Unknown"):
        control_policy("ACTION|new_write", "", {})
    with pytest.raises(AssertionError, match="Unknown"):
        control_policy("ACTION|health_detail|unknown", "", {})


from tests.telegram_live_policy import COMMAND_POLICY, registered_catalogue
from tests.telegram_live_harness import exploration_fingerprint, semantic_message_state


class FakeClient:
    def __init__(self, graph=None, *, broken_reset=False, read_error=False, stale=False):
        self.graph = graph or {}
        self.history = []
        self.sent = []
        self.clicked = []
        self.counter = 0
        self.broken_reset, self.read_error, self.stale = broken_reset, read_error, stale

    async def get_me(self):
        return SimpleNamespace(id=99999)

    def conversation(self, target, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def show(self, node, *, edit=None):
        text, controls = self.graph.get(node, (node, []))
        if edit is None:
            self.counter += 1
        msg = SimpleNamespace(id=edit or self.counter, raw_text=text, out=False, buttons=[], reply_markup=bool(controls))
        for label, payload, destination in controls:
            async def click(payload=payload, destination=destination, mid=msg.id):
                self.clicked.append(payload)
                if payload == "FORM|disabled":
                    return SimpleNamespace(message="This form type is coming soon.")
                if not self.stale:
                    self.history = [m for m in self.history if m.id != mid]
                    self.show(destination, edit=mid)
            msg.buttons.append([SimpleNamespace(text=label, data=payload.encode(), url=None, click=click)])
        self.history.insert(0, msg)
        return msg

    async def send_message(self, text):
        self.counter += 1
        sent = SimpleNamespace(id=self.counter, raw_text=text, out=True, buttons=[], reply_markup=None)
        self.sent.append(text)
        self.history.insert(0, sent)
        if text == "/cancel":
            self.show("bad reset" if self.broken_reset else "Cancelled")
        elif text == "/chase":
            self.show("Assessor reminders are coming soon.")
        else:
            self.show(text)
        return sent

    async def get_messages(self, target, limit=50):
        if self.read_error and self.sent:
            raise ConnectionError("secret-error-email@example.com")
        return self.history[:limit]


@pytest.fixture
def approved(monkeypatch):
    monkeypatch.setenv("TELEGRAM_LIVE_APPROVED", "portfolio-guru-live-qa-approved")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "portfolio_guru_bot")
    monkeypatch.setenv("TELEGRAM_LIVE_ALLOWED_BOTS", "portfolio_guru_bot")


def catalogue():
    return {"commands": sorted(COMMAND_POLICY), "digest": "fixture", "forms": ["CBD"]}


def limits(**kwargs):
    return ExplorationLimits(**{"response_seconds": .2, "poll_interval": 0, **kwargs})


async def run(client, tmp_path, **kwargs):
    return await explore_whole_bot(client, "portfolio_guru_bot", catalogue(), tmp_path,
                                  expected_user_id=99999, limits=limits(**kwargs))


def test_real_command_enumeration_is_credential_free():
    actual = registered_catalogue()
    assert set(actual["commands"]) == set(COMMAND_POLICY)
    assert "CBD" in actual["forms"]
    assert "reset" not in command_roots(actual["commands"])


@pytest.mark.asyncio
async def test_graph_dynamic_routes_edits_loops_reset_and_protected(approved, tmp_path):
    graph = {
        "/help": ("Help", [("Next", "ACTION|help", "menu")]),
        "menu": ("Menu", [("Next help page", "ACTION|help", "draft")]),
        "draft": ("Draft", [("Save", "APPROVE|draft", "BAD"), ("Back", "ACTION|help", "/help")]),
    }
    client = FakeClient(graph)
    receipt = await run(client, tmp_path)
    assert receipt["status"] == "passed" and receipt["overall_status"] == "pending"
    assert receipt["loops"]
    assert any(r["path"] == ["ACTION|help", "ACTION|help"] for r in receipt["routes"])
    assert any(p["status"] == "protected-boundary-reached" for p in receipt["protected"])
    assert "APPROVE|draft" not in client.clicked
    assert not {"/reset", "/delete", "/beta", "/setup"} & set(client.sent)
    assert client.sent[-1] == "/cancel"
    transcript = json.loads((tmp_path / "whole-bot-transcript.json").read_text())
    assert {"in", "out"} <= {t["direction"] for t in transcript}
    assert any(t["action"] == "click_button" for t in transcript)
    assert len([x for x in client.sent if x == "/cancel"]) >= len(receipt["routes"])
    other = tmp_path / "second"
    await run(FakeClient(graph), other)
    assert (tmp_path / "whole-bot-coverage.json").read_bytes() == (other / "whole-bot-coverage.json").read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unknown", "read", "stale", "reset", "depth", "actions", "routes", "time"])
async def test_incomplete_exploration_fails_and_retains_evidence(approved, tmp_path, mode):
    graph = {"/help": ("Help", [("Go", "ACTION|help", "next")]),
             "next": ("Next", [("Back", "ACTION|help", "/help")])}
    if mode == "unknown":
        graph["/help"] = ("email@example.com", [("Cancel", "ACTION|unknown", "BAD")])
    client = FakeClient(graph, read_error=mode == "read", stale=mode == "stale", broken_reset=mode == "reset")
    bounds = {"depth": 1} if mode == "depth" else ({mode: 1} if mode in {"actions", "routes"} else {})
    if mode == "time":
        bounds["seconds"] = .000001
    with pytest.raises((AssertionError, ConnectionError, TimeoutError)):
        await run(client, tmp_path, **bounds)
    receipt = json.loads((tmp_path / "whole-bot-coverage.json").read_text())
    assert receipt["status"] == "pending" and receipt["failures"]
    assert "email@example.com" not in (tmp_path / "whole-bot-transcript.json").read_text()
    assert "secret-error-email" not in json.dumps(receipt)


def test_payload_change_alone_changes_fingerprint():
    a = FakeClient({"a": ("same", [("same", "ACTION|help", "a")])}).show("a")
    b = FakeClient({"b": ("same", [("same", "ACTION|settings", "b")])}).show("b")
    assert a.id == b.id
    assert exploration_fingerprint(a) != exploration_fingerprint(b)
    assert semantic_message_state(a) != semantic_message_state(b)


@pytest.mark.asyncio
async def test_guard_and_artifact_failures_send_nothing(tmp_path):
    client = FakeClient()
    with pytest.raises(RuntimeError):
        await run(client, tmp_path)
    assert not client.sent


@pytest.mark.asyncio
async def test_multiple_menus_and_disabled_toast_are_observed(approved, tmp_path):
    class SplitClient(FakeClient):
        async def send_message(self, text):
            sent = await super().send_message(text)
            if text == "/help":
                self.show("other")
            return sent
    graph = {"/help": ("Help first", [("Soon", "FORM|disabled", "BAD")]),
             "other": ("Help second", [("Cancel", "APPROVE|draft", "BAD")])}
    client = SplitClient(graph)
    receipt = await run(client, tmp_path)
    assert "FORM|disabled" in client.clicked and "APPROVE|draft" not in client.clicked
    assert any(x["payload"] == "APPROVE|draft" for x in receipt["protected"])
    assert "callback_answer" in (tmp_path / "whole-bot-transcript.json").read_text()


@pytest.mark.asyncio
async def test_artifact_failure_and_identity_mismatch_precede_sends(approved, tmp_path):
    client = FakeClient()
    bad_path = tmp_path / "file"
    bad_path.write_text("not a directory")
    with pytest.raises(OSError):
        await run(client, bad_path)
    with pytest.raises(AssertionError, match="account mismatch"):
        await explore_whole_bot(client, "portfolio_guru_bot", catalogue(), tmp_path,
                               expected_user_id=7, limits=limits())
    assert not client.sent


@pytest.mark.asyncio
async def test_contradictory_reply_and_missing_outbound_read_fail(approved, tmp_path):
    graph = {"/help": ("Before I draft this, I still need help",
                       [("Save", "APPROVE|draft", "BAD")])}
    with pytest.raises(AssertionError, match="Contradictory"):
        await run(FakeClient(graph), tmp_path / "contradiction")
    class MissingOutbound(FakeClient):
        async def get_messages(self, *args, **kwargs):
            return [m for m in await super().get_messages(*args, **kwargs) if not m.out]
    with pytest.raises(AssertionError, match="Outbound"):
        await run(MissingOutbound(), tmp_path / "outbound")


def test_whole_bot_readiness_cannot_skip_missing_credentials(monkeypatch, tmp_path):
    from tests.test_whole_bot_live import whole_bot_ready
    monkeypatch.setattr("tests.test_whole_bot_live.verify_candidate", lambda *args: {"status": "passed"})
    monkeypatch.setenv("WHOLE_BOT_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("WHOLE_BOT_LIVE_PROOF", "1")
    monkeypatch.setenv("TELEGRAM_E2E_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.delenv("TELETHON_SESSION", raising=False)
    with pytest.raises(AssertionError, match="incomplete"):
        whole_bot_ready.__wrapped__()
    assert not (tmp_path / "live_graph-transcript.json").exists()


@pytest.mark.asyncio
async def test_commands_can_edit_an_older_message(approved, tmp_path):
    class EditedCommands(FakeClient):
        async def send_message(self, text):
            if text == "/cancel":  # The real cancel handler replies with a new message.
                return await super().send_message(text)
            self.counter += 1
            sent = SimpleNamespace(id=self.counter, raw_text=text, out=True, buttons=[], reply_markup=None)
            self.sent.append(text)
            self.history = [m for m in self.history if m.id != 1]
            self.history.insert(0, sent)
            response = "Assessor reminders are coming soon." if text == "/chase" else text
            self.show(response, edit=1)
            return sent
    client = EditedCommands()
    client.show("Initial private history must not be archived")
    receipt = await run(client, tmp_path)
    assert receipt["status"] == "passed"
    transcript = (tmp_path / "whole-bot-transcript.json").read_text()
    assert "Initial private history" not in transcript
    assert '"message_id": 1' in transcript


@pytest.mark.parametrize("payload", ["APPROVE|submit", "UPGRADE|pro_plus", "ACTION|setup",
    "CONFIRM|reset", "SETLEVEL|HIGHER", "SUP|confirm-save-draft|abc", "FEEDBACK|ok|CBD"])
def test_each_write_family_is_protected(payload):
    assert control_policy(payload, "", {}) == "protected"


@pytest.mark.asyncio
async def test_response_burst_cannot_hide_unread_controls(approved, tmp_path):
    class BurstClient(FakeClient):
        def show(self, node, *, edit=None):
            if node == "burst":
                for i in range(55):
                    result = super().show("response " + str(i))
                return result
            return super().show(node, edit=edit)
    client = BurstClient({"/help": ("Help", [("Go", "ACTION|help", "burst")])})
    with pytest.raises(AssertionError, match="history ceiling"):
        await run(client, tmp_path)
