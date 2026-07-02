"""Retention purge tests (launch checklist 1.5).

Invariants:

1. Expired rows get their clinical payload (case_text_encrypted AND
   extracted_fields) nulled — and ONLY that payload: the update must not touch
   form_type/status/created_at, which usage history and ARCP-health read.
2. The cutoff honours PG_CLINICAL_RETENTION_DAYS (default 180, floor 1,
   garbage falls back to default).
3. Supabase disabled → clean "disabled" no-op; Supabase errors are reported,
   never raised (best-effort like every other mirror touch).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import retention


class _FakeTable:
    def __init__(self, result_rows):
        self._result_rows = result_rows
        self.update_payload = None
        self.lt_args = None

    def update(self, payload):
        self.update_payload = payload
        return self

    def lt(self, column, value):
        self.lt_args = (column, value)
        return self

    def execute(self):
        resp = MagicMock()
        resp.data = self._result_rows
        return resp


def test_purge_nulls_only_the_clinical_payload_of_expired_rows(monkeypatch):
    monkeypatch.delenv("PG_CLINICAL_RETENTION_DAYS", raising=False)
    fake_table = _FakeTable(result_rows=[{"id": 1}, {"id": 2}])
    sb = MagicMock()
    sb.table.return_value = fake_table

    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    with patch("supabase_sync._supabase", return_value=sb):
        result = retention.purge_expired_clinical_content(now=now)

    sb.table.assert_called_once_with("portfolio_cases")
    # Exactly the two clinical columns are nulled — nothing else is touched.
    assert fake_table.update_payload == {"case_text_encrypted": None, "extracted_fields": None}
    column, cutoff = fake_table.lt_args
    assert column == "created_at"
    assert cutoff == (now - timedelta(days=180)).isoformat()
    assert result == {"status": "ok", "cutoff": cutoff, "rows": 2}


def test_retention_window_env_parsing(monkeypatch):
    monkeypatch.delenv("PG_CLINICAL_RETENTION_DAYS", raising=False)
    assert retention.retention_days() == 180
    monkeypatch.setenv("PG_CLINICAL_RETENTION_DAYS", "30")
    assert retention.retention_days() == 30
    monkeypatch.setenv("PG_CLINICAL_RETENTION_DAYS", "0")
    assert retention.retention_days() == 1  # floor — never "purge everything"
    monkeypatch.setenv("PG_CLINICAL_RETENTION_DAYS", "garbage")
    assert retention.retention_days() == 180


def test_purge_is_a_noop_when_supabase_is_disabled():
    with patch("supabase_sync._supabase", return_value=None):
        assert retention.purge_expired_clinical_content() == {"status": "disabled"}


def test_purge_reports_errors_instead_of_raising():
    sb = MagicMock()
    sb.table.side_effect = RuntimeError("supabase down")
    with patch("supabase_sync._supabase", return_value=sb):
        result = retention.purge_expired_clinical_content()
    assert result["status"] == "error"
    assert "supabase down" in result["error"]
