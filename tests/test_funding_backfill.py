import sqlite3

import ccxt

from bot.data.funding_backfill import FUNDING_INTERVAL_MS, backfill_funding_rates
from bot.storage.db import CREATE_FUNDING_RATES_TABLE, query_funding_rates_df

START = ccxt.Exchange.parse8601("2020-01-01T00:00:00Z")


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(CREATE_FUNDING_RATES_TABLE)
    conn.commit()
    return conn


class FakeFundingExchange:
    """No network calls — returns canned funding-rate pages to exercise pagination."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def fetchFundingRateHistory(self, symbol, since=None, limit=None):
        self.calls.append(since)
        if not self.pages:
            return []
        return self.pages.pop(0)


def rate(ts, funding_rate=0.0001):
    return {"timestamp": ts, "fundingRate": funding_rate, "symbol": "BTC/USDT:USDT"}


def test_backfill_stops_on_short_page():
    conn = make_conn()
    page1 = [rate(START + i * FUNDING_INTERVAL_MS) for i in range(3)]  # full page, limit=3
    page2 = [rate(START + 3 * FUNDING_INTERVAL_MS)]  # short page -> stop
    exchange = FakeFundingExchange([page1, page2])

    total = backfill_funding_rates(
        exchange, conn, "binanceusdm", "BTC/USDT:USDT", "2020-01-01T00:00:00Z", limit=3
    )

    assert total == 4
    assert len(exchange.calls) == 2
    df = query_funding_rates_df(conn, "binanceusdm", "BTC/USDT:USDT")
    assert len(df) == 4
    assert df["funding_rate"].iloc[0] == 0.0001


def test_backfill_resumes_from_latest_stored_rate():
    conn = make_conn()
    from bot.storage.db import upsert_funding_rates

    upsert_funding_rates(
        conn, "binanceusdm", "BTC/USDT:USDT",
        [rate(START + i * FUNDING_INTERVAL_MS) for i in range(3)],
    )

    page = [rate(START + 3 * FUNDING_INTERVAL_MS)]  # short page -> stop after one call
    exchange = FakeFundingExchange([page])

    total = backfill_funding_rates(
        exchange, conn, "binanceusdm", "BTC/USDT:USDT", "2020-01-01T00:00:00Z", limit=3
    )

    assert total == 1
    assert exchange.calls == [START + 3 * FUNDING_INTERVAL_MS]


def test_backfill_returns_zero_when_no_data():
    conn = make_conn()
    exchange = FakeFundingExchange([])

    total = backfill_funding_rates(
        exchange, conn, "binanceusdm", "BTC/USDT:USDT", "2020-01-01T00:00:00Z", limit=3
    )

    assert total == 0
    assert len(query_funding_rates_df(conn, "binanceusdm", "BTC/USDT:USDT")) == 0
