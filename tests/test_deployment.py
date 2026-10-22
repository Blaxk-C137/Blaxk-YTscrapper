import base64
import json

import pytest

from app.database import CompatRow, _PostgresCompat
import app.scraper_service as scraper_service


def test_postgres_compat_translates_portable_sql():
    insert = _PostgresCompat._sql(
        "INSERT OR IGNORE INTO creator_regions(channel_id, region) VALUES(?, ?)"
    )
    assert insert == (
        "INSERT INTO creator_regions(channel_id, region) VALUES(%s, %s) "
        "ON CONFLICT DO NOTHING"
    )

    query = _PostgresCompat._sql(
        "SELECT * FROM creators WHERE title LIKE :term ORDER BY title COLLATE NOCASE"
    )
    assert "title ILIKE %(term)s" in query
    assert "COLLATE NOCASE" not in query


def test_compat_row_supports_mapping_and_integer_access():
    row = CompatRow({"total": 7, "label": "Nigeria"})
    assert row["total"] == 7
    assert row[0] == 7
    assert row[1] == "Nigeria"


def test_hosted_storage_state_decodes_base64(monkeypatch):
    state = {"cookies": [{"name": "SID", "value": "secret"}], "origins": []}
    encoded = base64.b64encode(json.dumps(state).encode()).decode()
    monkeypatch.setattr(scraper_service, "YOUTUBE_STORAGE_STATE_B64", encoded)
    assert scraper_service.hosted_storage_state() == state


def test_hosted_storage_state_rejects_invalid_value(monkeypatch):
    monkeypatch.setattr(scraper_service, "YOUTUBE_STORAGE_STATE_B64", "not-base64!")
    with pytest.raises(RuntimeError, match="not valid storage state"):
        scraper_service.hosted_storage_state()
