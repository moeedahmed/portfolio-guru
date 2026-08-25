"""Guard the GDPR Art. 17 erasure path: delete_user_data purges the right tables.

The bug this pins: /reset used to clear only LOCAL state, leaving cloud copies of
credentials, clinical cases, profile and usage in Supabase indefinitely. This test
asserts delete_user_data issues deletes against every sensitive table, scoped to the
user's own telegram_user_id, and respects the billing-link retention default.

Consent records are deliberately NOT erasable: they are the evidence of the lawful
basis for processing that already happened. A withdrawal appends a row instead.
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
    "pg_chase_log",
    "pg_beta_requests",
}


def test_default_erasure_purges_sensitive_tables_keeps_billing(monkeypatch):
    sink = []
    _patch(monkeypatch, sink)

    result = supabase_sync.delete_user_data(42)

    deleted_tables = {t for (t, op, _c, _v) in sink if op == "delete"}
    assert SENSITIVE.issubset(deleted_tables)
    # Billing link is retained by default so a /reset doesn't orphan a subscription.
    assert "pg_users" not in deleted_tables
    # Consent history is never erased — it proves the basis for past processing.
    assert "pg_consent_records" not in deleted_tables
    # Every delete is scoped to this user, never a broad wipe.
    assert all(col == "telegram_user_id" and val == 42 for (_t, _o, col, val) in sink)
    assert result["pg_filings"] == "deleted"


def test_full_erasure_includes_billing_link(monkeypatch):
    sink = []
    _patch(monkeypatch, sink)

    supabase_sync.delete_user_data(42, include_billing_link=True)

    deleted_tables = {t for (t, op, _c, _v) in sink if op == "delete"}
    assert "pg_users" in deleted_tables


def test_unconfigured_mirror_is_a_noop(monkeypatch):
    """There is no account link any more, so the only remaining reason to skip
    is that Supabase isn't configured. Erasure must still not raise."""
    sink = []
    monkeypatch.setattr(supabase_sync, "_supabase", lambda: None)

    result = supabase_sync.delete_user_data(42)

    assert sink == []
    assert result["_skipped"] == "supabase not configured"


def test_erasure_no_longer_depends_on_an_account_link(monkeypatch):
    """The old mirror resolved every write through an emgurus_user_id exactly one
    user had. Its absence is what makes erasure work for every doctor."""
    assert not hasattr(supabase_sync, "_resolve_emgurus_user_id")
