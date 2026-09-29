"""London holds filing metadata only; retention must never contact Supabase."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import retention


@pytest.mark.parametrize("configured", [False, True])
def test_purge_holds_no_clinical_content_and_never_contacts_supabase(monkeypatch, configured):
    monkeypatch.setenv("PG_CLINICAL_RETENTION_DAYS", "30")
    if configured:
        monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake")
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    with patch("supabase_sync._supabase", side_effect=AssertionError("Must not connect")) as client:
        result = retention.purge_expired_clinical_content(now=now)
    client.assert_not_called()
    assert result == {
        "status": "ok", "rows": 0,
        "cutoff": (now - timedelta(days=30)).isoformat(),
        "reason": "pg_filings holds no clinical content",
    }


def test_retention_window_env_parsing(monkeypatch):
    monkeypatch.delenv("PG_CLINICAL_RETENTION_DAYS", raising=False)
    assert retention.retention_days() == 180
    monkeypatch.setenv("PG_CLINICAL_RETENTION_DAYS", "30")
    assert retention.retention_days() == 30
    monkeypatch.setenv("PG_CLINICAL_RETENTION_DAYS", "0")
    assert retention.retention_days() == 1  # floor — never "purge everything"
    monkeypatch.setenv("PG_CLINICAL_RETENTION_DAYS", "garbage")
    assert retention.retention_days() == 180


def test_repeated_purge_is_harmless_even_when_supabase_is_unavailable():
    with patch("supabase_sync._supabase", side_effect=RuntimeError("unavailable")):
        for _ in range(2):
            result = retention.purge_expired_clinical_content()
            assert result["status"] == "ok"
            assert result["rows"] == 0
