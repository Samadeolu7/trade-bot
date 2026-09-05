import sqlite3
from pathlib import Path

import pandas as pd

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


def connect(db_path: str = "data/trades.db") -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_CANDLES_TABLE)
    conn.commit()
    return conn


def upsert_candles(
    conn: sqlite3.Connection,
    exchange: str,
    symbol: str,
    timeframe: str,
    candles: list[list],
) -> int:
    """candles: rows of [open_time, open, high, low, close, volume] as returned by ccxt.fetch_ohlcv"""
    if not candles:
        return 0
    rows = [(exchange, symbol, timeframe, *candle) for candle in candles]
    conn.executemany(
        """
        INSERT OR REPLACE INTO candles
            (exchange, symbol, timeframe, open_time, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def get_latest_open_time(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str
) -> int | None:
    cur = conn.execute(
        """
        SELECT MAX(open_time) FROM candles
        WHERE exchange = ? AND symbol = ? AND timeframe = ?
        """,
        (exchange, symbol, timeframe),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else None


def query_candles_df(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str
) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT open_time, open, high, low, close, volume FROM candles
        WHERE exchange = ? AND symbol = ? AND timeframe = ?
        ORDER BY open_time ASC
        """,
        conn,
        params=(exchange, symbol, timeframe),
    )
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.set_index("open_time")
