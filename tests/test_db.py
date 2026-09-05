import sqlite3

from bot.storage.db import get_latest_open_time, query_candles_df, upsert_candles

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


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(CREATE_CANDLES_TABLE)
    conn.commit()
    return conn


def test_upsert_inserts_rows():
    conn = make_conn()
    candles = [
        [1000, 10.0, 11.0, 9.0, 10.5, 100.0],
        [2000, 10.5, 12.0, 10.0, 11.5, 200.0],
    ]
    count = upsert_candles(conn, "binance", "BTC/USDT", "1h", candles)
    assert count == 2

    df = query_candles_df(conn, "binance", "BTC/USDT", "1h")
    assert len(df) == 2
    assert list(df["close"]) == [10.5, 11.5]


def test_upsert_is_idempotent_on_open_time():
    conn = make_conn()
    upsert_candles(conn, "binance", "BTC/USDT", "1h", [[1000, 10.0, 11.0, 9.0, 10.5, 100.0]])
    # re-upsert same open_time with a different close — should replace, not duplicate
    upsert_candles(conn, "binance", "BTC/USDT", "1h", [[1000, 10.0, 11.0, 9.0, 99.0, 100.0]])

    df = query_candles_df(conn, "binance", "BTC/USDT", "1h")
    assert len(df) == 1
    assert df["close"].iloc[0] == 99.0


def test_get_latest_open_time():
    conn = make_conn()
    assert get_latest_open_time(conn, "binance", "BTC/USDT", "1h") is None

    upsert_candles(
        conn,
        "binance",
        "BTC/USDT",
        "1h",
        [[1000, 1, 1, 1, 1, 1], [2000, 1, 1, 1, 1, 1]],
    )
    assert get_latest_open_time(conn, "binance", "BTC/USDT", "1h") == 2000


def test_query_scoped_by_exchange_symbol_timeframe():
    conn = make_conn()
    upsert_candles(conn, "binance", "BTC/USDT", "1h", [[1000, 1, 1, 1, 1, 1]])
    upsert_candles(conn, "binance", "ETH/USDT", "1h", [[1000, 2, 2, 2, 2, 2]])
    upsert_candles(conn, "binance", "BTC/USDT", "4h", [[1000, 3, 3, 3, 3, 3]])

    df = query_candles_df(conn, "binance", "BTC/USDT", "1h")
    assert len(df) == 1
    assert df["close"].iloc[0] == 1
