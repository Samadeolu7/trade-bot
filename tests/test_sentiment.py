import sqlite3
from unittest.mock import MagicMock, patch

from bot.data.sentiment import fetch_fear_greed_index
from bot.storage.db import CREATE_FEAR_GREED_TABLE, get_latest_fear_greed, upsert_fear_greed_entries


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(CREATE_FEAR_GREED_TABLE)
    conn.commit()
    return conn


def fake_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_fetch_fear_greed_index_parses_data_list():
    payload = {
        "name": "Fear and Greed Index",
        "data": [
            {"value": "66", "value_classification": "Greed", "timestamp": "1788912000"},
            {"value": "69", "value_classification": "Greed", "timestamp": "1788825600"},
        ],
    }
    with patch("bot.data.sentiment.requests.get", return_value=fake_response(payload)) as mock_get:
        entries = fetch_fear_greed_index(limit=2)

    assert len(entries) == 2
    assert entries[0]["value"] == "66"
    assert entries[0]["value_classification"] == "Greed"
    mock_get.assert_called_once()
    assert mock_get.call_args.kwargs["params"] == {"limit": 2}


def test_fetch_fear_greed_index_raises_on_http_error():
    response = MagicMock()
    response.raise_for_status.side_effect = Exception("boom")
    with patch("bot.data.sentiment.requests.get", return_value=response):
        try:
            fetch_fear_greed_index()
            assert False, "expected an exception"
        except Exception:
            pass


def test_upsert_and_get_latest_fear_greed():
    conn = make_conn()
    assert get_latest_fear_greed(conn) is None

    entries = [
        {"value": "40", "value_classification": "Fear", "timestamp": "1788825600"},
        {"value": "45", "value_classification": "Fear", "timestamp": "1788912000"},
    ]
    count = upsert_fear_greed_entries(conn, entries)
    assert count == 2

    latest = get_latest_fear_greed(conn)
    assert latest == {"fetched_at": 1788912000, "value": 45, "classification": "Fear"}


def test_upsert_fear_greed_entries_is_idempotent():
    conn = make_conn()
    entry = [{"value": "40", "value_classification": "Fear", "timestamp": "1788825600"}]
    upsert_fear_greed_entries(conn, entry)
    upsert_fear_greed_entries(conn, entry)

    cur = conn.execute("SELECT COUNT(*) FROM fear_greed_index")
    assert cur.fetchone()[0] == 1


def test_upsert_fear_greed_entries_returns_zero_for_empty_list():
    conn = make_conn()
    assert upsert_fear_greed_entries(conn, []) == 0
