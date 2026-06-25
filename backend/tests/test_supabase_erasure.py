"""Guard the GDPR Art. 17 erasure path: delete_user_data purges the right tables.

The bug this pins: /reset used to clear only LOCAL state, leaving cloud copies of
credentials, clinical cases, profile and usage in Supabase indefinitely. This test
asserts delete_user_data issues deletes against every sensitive table, keyed by the
resolved emgurus_user_id, and respects the billing-link retention default.
"""
import supabase_sync


class _DeleteRecorder:
    def __init__(self, sink, table):
        self._sink = sink
        self._table = table
        self._op = None

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._sink.append((self._table, self._op, col, val))
        return self

    def execute(self):
        return None


class _Client:
    def __init__(self, sink):
        self._sink = sink

    def table(self, name):
        return _DeleteRecorder(self._sink, name)


def _patch(monkeypatch, sink):
    monkeypatch.setattr(supabase_sync, "_supabase", lambda: _Client(sink))
    monkeypatch.setattr(supabase_sync, "_resolve_emgurus_user_id", lambda _uid: "uuid-xyz")


SENSITIVE = {
    "portfolio_credentials",
    "portfolio_cases",
    "portfolio_profile",
    "portfolio_usage",
    "portfolio_chase_log",
    "portfolio_link_tokens",
}


def test_default_erasure_purges_sensitive_tables_keeps_billing(monkeypatch):
    sink = []
    _patch(monkeypatch, sink)

    result = supabase_sync.delete_user_data(42)

    deleted_tables = {t for (t, op, _c, _v) in sink if op == "delete"}
    assert SENSITIVE.issubset(deleted_tables)
    # Billing link is retained by default.
    assert "portfolio_users" not in deleted_tables
    # Every delete is scoped to the resolved UUID, never a broad wipe.
    assert all(col == "emgurus_user_id" and val == "uuid-xyz" for (_t, _o, col, val) in sink)
    assert result["portfolio_cases"] == "deleted"


def test_full_erasure_includes_billing_link(monkeypatch):
    sink = []
    _patch(monkeypatch, sink)

    supabase_sync.delete_user_data(42, include_billing_link=True)

    deleted_tables = {t for (t, op, _c, _v) in sink if op == "delete"}
    assert "portfolio_users" in deleted_tables


def test_unlinked_user_is_noop(monkeypatch):
    sink = []
    monkeypatch.setattr(supabase_sync, "_supabase", lambda: _Client(sink))
    monkeypatch.setattr(supabase_sync, "_resolve_emgurus_user_id", lambda _uid: None)

    result = supabase_sync.delete_user_data(42)

    assert sink == []
    assert result["_skipped"] == "user not linked"
