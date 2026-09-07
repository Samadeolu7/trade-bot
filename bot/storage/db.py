import json
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
# open "paper" position per (exchange, symbol, timeframe, strategy_label)
# plus a log of completed round-trips and every signal fired, per spec
# Section 11 (`signals`, `trades` tables). strategy_label is part of the
# identity (not just symbol/timeframe) so multiple strategies can shadow-run
# the same symbol concurrently, each with independent state — see spec
# Section 13/PILOT_LOG: no shared capital between concurrent shadow runs.
CREATE_SIGNALS_TABLE = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    strategy_label TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL,
    reason TEXT,
    context TEXT,
    fired_at INTEGER NOT NULL
)
"""

CREATE_PAPER_POSITION_TABLE = """
CREATE TABLE IF NOT EXISTS paper_position (
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    strategy_label TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL,
    entry_time INTEGER NOT NULL,
    entry_fee REAL NOT NULL,
    size REAL NOT NULL,
    context TEXT,
    PRIMARY KEY (exchange, symbol, timeframe, strategy_label)
)
"""

CREATE_PAPER_TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    strategy_label TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_time INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_time INTEGER NOT NULL,
    exit_price REAL NOT NULL,
    pnl REAL NOT NULL,
    pnl_pct REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    context TEXT,
    closed_at INTEGER NOT NULL
)
"""

# Perpetual-futures funding rate history (spec Section 7d) — a candidate
# confirmation/veto input for FundingFilteredStrategy, fetched read-only from
# Binance's USD-M futures market (funding is a futures concept; Quidax spot
# execution never sees it). Kept in its own table/exchange identity since
# it's unrelated to (and prints far less often than) OHLCV candles.
CREATE_FUNDING_RATES_TABLE = """
CREATE TABLE IF NOT EXISTS funding_rates (
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    funding_time INTEGER NOT NULL,
    funding_rate REAL NOT NULL,
    UNIQUE(exchange, symbol, funding_time)
)
"""

# Tiny key-value store for scheduling bookkeeping (e.g. "last heartbeat sent
# at") that needs to survive process restarts. Keyed by the caller (e.g.
# f"{strategy_label}:last_heartbeat_at") so concurrent shadow runs don't
# clobber each other's schedule.
CREATE_BOT_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT
)
"""


def _migrate_shadow_tables(conn: sqlite3.Connection) -> None:
    """One-time migration: signals/paper_position/paper_trades originally
    had no strategy_label column, so a symbol/timeframe could only track one
    strategy's paper state at a time. Concurrent shadow runs need it as part
    of the identity. Dropped and recreated rather than ALTER TABLE (which
    can't change a PRIMARY KEY in SQLite anyway) — every deployment that
    predates this change had these tables empty (no signal had fired yet)."""
    for table in ("signals", "paper_position", "paper_trades"):
        cur = conn.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cur.fetchall()]
        if columns and "strategy_label" not in columns:
            conn.execute(f"DROP TABLE {table}")
    conn.commit()


def connect(db_path: str = "data/trades.db") -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    if db_path != ":memory:":
        # Concurrent shadow-run containers share one SQLite file (same
        # candle data, independent strategy_label-scoped state) — WAL lets
        # readers/writers overlap instead of blocking on the default
        # rollback journal, and busy_timeout retries a write that still
        # collides instead of raising "database is locked" immediately.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    conn.execute(CREATE_CANDLES_TABLE)
    _migrate_shadow_tables(conn)
    conn.execute(CREATE_SIGNALS_TABLE)
    conn.execute(CREATE_PAPER_POSITION_TABLE)
    conn.execute(CREATE_PAPER_TRADES_TABLE)
    conn.execute(CREATE_FUNDING_RATES_TABLE)
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
    # explicit ns resolution: pandas 2.x+ preserves to_datetime(unit="ms")'s
    # native ms resolution instead of upcasting, and (as of pandas 3.0)
    # comparing a ms-resolution index against a plain pd.Timestamp(...)
    # elsewhere in the codebase raises instead of coercing — normalize here,
    # once, at the source, rather than at every comparison call site.
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).astype("datetime64[ns, UTC]")
    return df.set_index("open_time")


def upsert_funding_rates(
    conn: sqlite3.Connection, exchange: str, symbol: str, rates: list[dict]
) -> int:
    """rates: ccxt fetchFundingRateHistory entries — dicts with 'timestamp'
    (epoch ms) and 'fundingRate' keys."""
    if not rates:
        return 0
    rows = [(exchange, symbol, r["timestamp"], r["fundingRate"]) for r in rates]
    conn.executemany(
        """
        INSERT OR REPLACE INTO funding_rates (exchange, symbol, funding_time, funding_rate)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def get_latest_funding_time(conn: sqlite3.Connection, exchange: str, symbol: str) -> int | None:
    cur = conn.execute(
        "SELECT MAX(funding_time) FROM funding_rates WHERE exchange = ? AND symbol = ?",
        (exchange, symbol),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else None


def query_funding_rates_df(conn: sqlite3.Connection, exchange: str, symbol: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT funding_time, funding_rate FROM funding_rates
        WHERE exchange = ? AND symbol = ?
        ORDER BY funding_time ASC
        """,
        conn,
        params=(exchange, symbol),
    )
    # see query_candles_df — same ms-vs-ns resolution fix
    df["funding_time"] = pd.to_datetime(df["funding_time"], unit="ms", utc=True).astype(
        "datetime64[ns, UTC]"
    )
    return df.set_index("funding_time")


def _to_epoch_ms(timestamp) -> int:
    """Accepts a pandas Timestamp (fresh from a live df) or an already-stored
    epoch-ms int (read back from a prior row) transparently."""
    if isinstance(timestamp, pd.Timestamp):
        return int(timestamp.value // 1_000_000)
    return int(timestamp)


def _dump_context(context: dict | None) -> str | None:
    return json.dumps(context) if context else None


def _load_context(raw: str | None) -> dict:
    return json.loads(raw) if raw else {}


def record_signal(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str, strategy_label: str, signal
) -> None:
    conn.execute(
        """
        INSERT INTO signals
            (exchange, symbol, timeframe, strategy_label, direction, entry_price, stop_loss,
             take_profit, reason, context, fired_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            exchange, symbol, timeframe, strategy_label, signal.direction, signal.entry_price,
            signal.stop_loss, signal.take_profit, signal.reason, _dump_context(signal.context),
            _to_epoch_ms(signal.timestamp),
        ),
    )
    conn.commit()


def get_open_paper_position(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str, strategy_label: str
) -> dict | None:
    cur = conn.execute(
        """
        SELECT direction, entry_price, stop_loss, take_profit, entry_time, entry_fee, size, context
        FROM paper_position
        WHERE exchange = ? AND symbol = ? AND timeframe = ? AND strategy_label = ?
        """,
        (exchange, symbol, timeframe, strategy_label),
    )
    row = cur.fetchone()
    if row is None:
        return None
    direction, entry_price, stop_loss, take_profit, entry_time, entry_fee, size, context = row
    return {
        "direction": direction,
        "entry_price": entry_price,
        "stop": stop_loss,
        "take_profit": take_profit,
        "entry_time": entry_time,
        "entry_fee": entry_fee,
        "size": size,
        "context": _load_context(context),
    }


def open_paper_position(
    conn: sqlite3.Connection,
    exchange: str,
    symbol: str,
    timeframe: str,
    strategy_label: str,
    position: dict,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO paper_position
            (exchange, symbol, timeframe, strategy_label, direction, entry_price, stop_loss,
             take_profit, entry_time, entry_fee, size, context)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            exchange, symbol, timeframe, strategy_label, position["direction"],
            position["entry_price"], position["stop"], position["take_profit"],
            _to_epoch_ms(position["entry_time"]), position["entry_fee"], position["size"],
            _dump_context(position.get("context")),
        ),
    )
    conn.commit()


def update_paper_position_stop(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str, strategy_label: str, new_stop: float
) -> None:
    conn.execute(
        """
        UPDATE paper_position SET stop_loss = ?
        WHERE exchange = ? AND symbol = ? AND timeframe = ? AND strategy_label = ?
        """,
        (new_stop, exchange, symbol, timeframe, strategy_label),
    )
    conn.commit()


def close_paper_position(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str, strategy_label: str
) -> None:
    conn.execute(
        """
        DELETE FROM paper_position
        WHERE exchange = ? AND symbol = ? AND timeframe = ? AND strategy_label = ?
        """,
        (exchange, symbol, timeframe, strategy_label),
    )
    conn.commit()


def record_paper_trade(
    conn: sqlite3.Connection,
    exchange: str,
    symbol: str,
    timeframe: str,
    strategy_label: str,
    direction: str,
    entry_time,
    entry_price: float,
    exit_time,
    exit_price: float,
    pnl: float,
    pnl_pct: float,
    exit_reason: str,
    closed_at,
    context: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO paper_trades
            (exchange, symbol, timeframe, strategy_label, direction, entry_time, entry_price,
             exit_time, exit_price, pnl, pnl_pct, exit_reason, context, closed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            exchange, symbol, timeframe, strategy_label, direction, _to_epoch_ms(entry_time),
            entry_price, _to_epoch_ms(exit_time), exit_price, pnl, pnl_pct, exit_reason,
            _dump_context(context), _to_epoch_ms(closed_at),
        ),
    )
    conn.commit()


def count_paper_trades_since(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str, strategy_label: str, since_ms: int
) -> int:
    cur = conn.execute(
        """
        SELECT COUNT(*) FROM paper_trades
        WHERE exchange = ? AND symbol = ? AND timeframe = ? AND strategy_label = ? AND closed_at >= ?
        """,
        (exchange, symbol, timeframe, strategy_label, since_ms),
    )
    return cur.fetchone()[0]


def count_paper_trades_all_time(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str, strategy_label: str
) -> int:
    cur = conn.execute(
        """
        SELECT COUNT(*) FROM paper_trades
        WHERE exchange = ? AND symbol = ? AND timeframe = ? AND strategy_label = ?
        """,
        (exchange, symbol, timeframe, strategy_label),
    )
    return cur.fetchone()[0]


def sum_paper_trade_pnl_pct(
    conn: sqlite3.Connection, exchange: str, symbol: str, timeframe: str, strategy_label: str
) -> float:
    cur = conn.execute(
        """
        SELECT COALESCE(SUM(pnl_pct), 0) FROM paper_trades
        WHERE exchange = ? AND symbol = ? AND timeframe = ? AND strategy_label = ?
        """,
        (exchange, symbol, timeframe, strategy_label),
    )
    return cur.fetchone()[0]


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    cur = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
