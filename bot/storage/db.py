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

# Phase 4 (shadow run) — no real capital, so trades are tracked as a single
# open "paper" position per (exchange, symbol, timeframe) plus a log of
# completed round-trips and every signal fired, per spec Section 11
# (`signals`, `trades` tables).
CREATE_SIGNALS_TABLE = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL,
    reason TEXT,
    fired_at INTEGER NOT NULL
)
"""

CREATE_PAPER_POSITION_TABLE = """
CREATE TABLE IF NOT EXISTS paper_position (
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL,
    entry_time INTEGER NOT NULL,
    entry_fee REAL NOT NULL,
    size REAL NOT NULL,
    PRIMARY KEY (exchange, symbol, timeframe)
)
"""

CREATE_PAPER_TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_time INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_time INTEGER NOT NULL,
    exit_price REAL NOT NULL,
    pnl REAL NOT NULL,
    pnl_pct REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    closed_at INTEGER NOT NULL
)
"""

# Tiny key-value store for scheduling bookkeeping (e.g. "last heartbeat sent
# at") that needs to survive process restarts.
CREATE_BOT_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT
)
"""


def connect(db_path: str = "data/trades.db") -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_CANDLES_TABLE)
    conn.execute(CREATE_SIGNALS_TABLE)
    conn.execute(CREATE_PAPER_POSITION_TABLE)
    conn.execute(CREATE_PAPER_TRADES_TABLE)
    conn.execute(CREATE_BOT_STATE_TABLE)
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


def _to_epoch_ms(timestamp) -> int:
    """Accepts a pandas Timestamp (fresh from a live df) or an already-stored
    epoch-ms int (read back from a prior row) transparently."""
    if isinstance(timestamp, pd.Timestamp):
        return int(timestamp.value // 1_000_000)
    return int(timestamp)


def record_signal(conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str, signal) -> None:
    conn.execute(
        """
        INSERT INTO signals
            (exchange, symbol, timeframe, direction, entry_price, stop_loss, take_profit, reason, fired_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            exchange, symbol, timeframe, signal.direction, signal.entry_price,
            signal.stop_loss, signal.take_profit, signal.reason, _to_epoch_ms(signal.timestamp),
        ),
    )
    conn.commit()


def get_open_paper_position(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str
) -> dict | None:
    cur = conn.execute(
        """
        SELECT direction, entry_price, stop_loss, take_profit, entry_time, entry_fee, size
        FROM paper_position WHERE exchange = ? AND symbol = ? AND timeframe = ?
        """,
        (exchange, symbol, timeframe),
    )
    row = cur.fetchone()
    if row is None:
        return None
    direction, entry_price, stop_loss, take_profit, entry_time, entry_fee, size = row
    return {
        "direction": direction,
        "entry_price": entry_price,
        "stop": stop_loss,
        "take_profit": take_profit,
        "entry_time": entry_time,
        "entry_fee": entry_fee,
        "size": size,
    }


def open_paper_position(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str, position: dict
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO paper_position
            (exchange, symbol, timeframe, direction, entry_price, stop_loss, take_profit,
             entry_time, entry_fee, size)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            exchange, symbol, timeframe, position["direction"], position["entry_price"],
            position["stop"], position["take_profit"], _to_epoch_ms(position["entry_time"]),
            position["entry_fee"], position["size"],
        ),
    )
    conn.commit()


def update_paper_position_stop(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str, new_stop: float
) -> None:
    conn.execute(
        "UPDATE paper_position SET stop_loss = ? WHERE exchange = ? AND symbol = ? AND timeframe = ?",
        (new_stop, exchange, symbol, timeframe),
    )
    conn.commit()


def close_paper_position(conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str) -> None:
    conn.execute(
        "DELETE FROM paper_position WHERE exchange = ? AND symbol = ? AND timeframe = ?",
        (exchange, symbol, timeframe),
    )
    conn.commit()


def record_paper_trade(
    conn: sqlite3.Connection,
    exchange: str,
    symbol: str,
    timeframe: str,
    direction: str,
    entry_time,
    entry_price: float,
    exit_time,
    exit_price: float,
    pnl: float,
    pnl_pct: float,
    exit_reason: str,
    closed_at,
) -> None:
    conn.execute(
        """
        INSERT INTO paper_trades
            (exchange, symbol, timeframe, direction, entry_time, entry_price,
             exit_time, exit_price, pnl, pnl_pct, exit_reason, closed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            exchange, symbol, timeframe, direction, _to_epoch_ms(entry_time), entry_price,
            _to_epoch_ms(exit_time), exit_price, pnl, pnl_pct, exit_reason, _to_epoch_ms(closed_at),
        ),
    )
    conn.commit()


def count_paper_trades_since(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str, since_ms: int
) -> int:
    cur = conn.execute(
        """
        SELECT COUNT(*) FROM paper_trades
        WHERE exchange = ? AND symbol = ? AND timeframe = ? AND closed_at >= ?
        """,
        (exchange, symbol, timeframe, since_ms),
    )
    return cur.fetchone()[0]


def count_paper_trades_all_time(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str
) -> int:
    cur = conn.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE exchange = ? AND symbol = ? AND timeframe = ?",
        (exchange, symbol, timeframe),
    )
    return cur.fetchone()[0]


def sum_paper_trade_pnl_pct(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str
) -> float:
    cur = conn.execute(
        """
        SELECT COALESCE(SUM(pnl_pct), 0) FROM paper_trades
        WHERE exchange = ? AND symbol = ? AND timeframe = ?
        """,
        (exchange, symbol, timeframe),
    )
    return cur.fetchone()[0]


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    cur = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
