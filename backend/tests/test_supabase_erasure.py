"""Guard the GDPR Art. 17 erasure path: delete_user_data purges the right tables.

The bug this pins: /reset used to clear only LOCAL state, leaving cloud copies of
credentials, clinical cases, profile and usage in Supabase indefinitely. This test
asserts delete_user_data issues deletes against every sensitive table, keyed by the
telegram_user_id, and respects the billing-link retention default.
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


SENSITIVE = {
    "pg_credentials",
    "pg_filings",
    "pg_profile",
    "pg_usage",
    "pg_kc_coverage",
    "pg_beta_requests",
}


def test_default_erasure_purges_sensitive_tables_keeps_billing(monkeypatch):
    sink = []
    _patch(monkeypatch, sink)

    result = supabase_sync.delete_user_data(42)

    deleted_tables = {t for (t, op, _c, _v) in sink if op == "delete"}
    assert SENSITIVE == deleted_tables
    # Billing link is retained by default.
    assert "pg_users" not in deleted_tables
    # Every delete is scoped to the Telegram ID, never a broad wipe.
    assert all(col == "telegram_user_id" and val == 42 for (_t, _o, col, val) in sink)
    assert result["pg_filings"] == "deleted"


def test_full_erasure_includes_billing_link(monkeypatch):
    sink = []
    _patch(monkeypatch, sink)

    supabase_sync.delete_user_data(42, include_billing_link=True)

    deleted_tables = {t for (t, op, _c, _v) in sink if op == "delete"}
    assert deleted_tables == SENSITIVE | {"pg_users"}
    assert "pg_consent_records" not in deleted_tables


def test_unconfigured_mirror_is_noop(monkeypatch):
    monkeypatch.setattr(supabase_sync, "_supabase", lambda: None)
    assert supabase_sync.delete_user_data(42) == {"_skipped": "supabase not configured"}


def test_erasure_continues_after_one_table_fails(monkeypatch):
    sink = []
    class FailingClient(_Client):
        def table(self, name):
            if name == "pg_credentials":
                raise RuntimeError("unavailable")
            return super().table(name)
    monkeypatch.setattr(supabase_sync, "_supabase", lambda: FailingClient(sink))
    result = supabase_sync.delete_user_data(42, include_billing_link=True)
    assert result["pg_credentials"].startswith("error: ")
    assert result["pg_users"] == "deleted"
    assert set(result) == SENSITIVE | {"pg_users"}
    assert all(col == "telegram_user_id" and val == 42 for _, _, col, val in sink)
