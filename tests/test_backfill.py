import sqlite3

import ccxt

from bot.data.backfill import backfill_candles
from bot.storage.db import query_candles_df

CREATE_CANDLES_TABLE = """
CREATE TABLE IF NOT EXISTS candles (
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_time INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    UNIQUE(exchange, symbol, timeframe, open_time)
)
"""

HOUR_MS = 3600 * 1000
START = ccxt.Exchange.parse8601("2020-01-01T00:00:00Z")


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(CREATE_CANDLES_TABLE)
    conn.commit()
    return conn


class FakeExchange:
    """No network calls — returns canned OHLCV pages to exercise pagination."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def parse_timeframe(self, timeframe):
        assert timeframe == "1h"
        return 3600  # seconds

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        self.calls.append(since)
        if not self.pages:
            return []
        return self.pages.pop(0)


def candle(open_time, close=1.0):
    return [open_time, close, close, close, close, 1.0]


def test_backfill_stops_on_short_page():
    conn = make_conn()
    page1 = [candle(START + i * HOUR_MS) for i in range(3)]  # full page, limit=3
    page2 = [candle(START + 3 * HOUR_MS)]  # short page -> stop
    exchange = FakeExchange([page1, page2])

    total = backfill_candles(
        exchange, conn, "binance", "BTC/USDT", "1h", "2020-01-01T00:00:00Z", limit=3
    )

    assert total == 4
    assert len(exchange.calls) == 2
    df = query_candles_df(conn, "binance", "BTC/USDT", "1h")
    assert len(df) == 4


def test_backfill_resumes_from_latest_stored_candle():
    conn = make_conn()
    # pretend candles up through hour 2 are already stored
    from bot.storage.db import upsert_candles

    upsert_candles(
        conn,
        "binance",
        "BTC/USDT",
        "1h",
        [candle(START + i * HOUR_MS) for i in range(3)],
    )

    page = [candle(START + 3 * HOUR_MS)]  # short page -> stop after one call
    exchange = FakeExchange([page])

    total = backfill_candles(
        exchange, conn, "binance", "BTC/USDT", "1h", "2020-01-01T00:00:00Z", limit=3
    )

    assert total == 1
    # should have asked for candles starting after the last stored one, not from START
    assert exchange.calls == [START + 3 * HOUR_MS]


def test_backfill_returns_zero_when_no_data():
    conn = make_conn()
    exchange = FakeExchange([])

    total = backfill_candles(
        exchange, conn, "binance", "BTC/USDT", "1h", "2020-01-01T00:00:00Z", limit=3
    )

    assert total == 0
