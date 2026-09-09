import json
import sqlite3
import time
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

# Crypto Fear & Greed Index (alternative.me) — market-wide sentiment context
# for the `recommend` system, advisory only (not wired into any strategy's
# entry/exit rule). Same upsert-idempotent shape as funding_rates.
CREATE_FEAR_GREED_TABLE = """
CREATE TABLE IF NOT EXISTS fear_greed_index (
    fetched_at INTEGER NOT NULL,
    value INTEGER NOT NULL,
    classification TEXT NOT NULL,
    UNIQUE(fetched_at)
)
"""

# Immutable-ish record of every backtest/sweep run (research infrastructure,
# 2026-09-09): so "why did we reject this" is reconstructable later, and
# "how many things have we tried" (multiple-testing/selection-bias exposure)
# is a real queryable count instead of implicit. One row per backtest, one
# row per sweep *combination* (not per sweep invocation) — that's what makes
# the attempted-count meaningful. decision/decision_reason are never set
# automatically; a good backtest does not self-promote a strategy.
CREATE_EXPERIMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    kind TEXT NOT NULL,
    strategy TEXT NOT NULL,
    strategy_label TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    window_start TEXT,
    window_end TEXT,
    touched_holdout INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    data_version TEXT,
    code_commit TEXT,
    rank_metric TEXT,
    rank_value REAL,
    result_json TEXT NOT NULL,
    decision TEXT,
    decision_reason TEXT,
    decided_at INTEGER
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
    conn.execute(CREATE_FEAR_GREED_TABLE)
    conn.execute(CREATE_EXPERIMENTS_TABLE)
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


def upsert_fear_greed_entries(conn: sqlite3.Connection, entries: list[dict]) -> int:
    """entries: alternative.me Fear & Greed API entries — dicts with 'value'
    (str), 'value_classification' (str), and 'timestamp' (str, epoch
    seconds) keys."""
    if not entries:
        return 0
    rows = [
        (int(e["timestamp"]), int(e["value"]), e["value_classification"]) for e in entries
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO fear_greed_index (fetched_at, value, classification)
        VALUES (?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def get_latest_fear_greed(conn: sqlite3.Connection) -> dict | None:
    cur = conn.execute(
        "SELECT fetched_at, value, classification FROM fear_greed_index ORDER BY fetched_at DESC LIMIT 1"
    )
    row = cur.fetchone()
    if row is None:
        return None
    fetched_at, value, classification = row
    return {"fetched_at": fetched_at, "value": value, "classification": classification}


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


def query_state_prefix(conn: sqlite3.Connection, prefix: str) -> dict[str, str]:
    """Every bot_state row whose key starts with `prefix` — e.g. every
    strategy's lifecycle stage at once, keyed as "lifecycle:{label}"."""
    cur = conn.execute("SELECT key, value FROM bot_state WHERE key LIKE ?", (f"{prefix}%",))
    return {key: value for key, value in cur.fetchall()}


_EXPERIMENT_COLUMNS = (
    "id", "created_at", "kind", "strategy", "strategy_label", "symbol", "timeframe",
    "window_start", "window_end", "touched_holdout", "config_json", "config_hash",
    "data_version", "code_commit", "rank_metric", "rank_value", "result_json",
    "decision", "decision_reason", "decided_at",
)


def record_experiment(
    conn: sqlite3.Connection,
    kind: str,
    strategy: str,
    strategy_label: str,
    symbol: str,
    timeframe: str,
    window_start: str | None,
    window_end: str | None,
    touched_holdout: bool,
    config_json: str,
    config_hash: str,
    data_version: str | None,
    code_commit: str | None,
    result_json: str,
    rank_metric: str | None = None,
    rank_value: float | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO experiments
            (created_at, kind, strategy, strategy_label, symbol, timeframe, window_start,
             window_end, touched_holdout, config_json, config_hash, data_version, code_commit,
             rank_metric, rank_value, result_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(time.time() * 1000), kind, strategy, strategy_label, symbol, timeframe,
            window_start, window_end, int(touched_holdout), config_json, config_hash,
            data_version, code_commit, rank_metric, rank_value, result_json,
        ),
    )
    conn.commit()
    return cur.lastrowid


def _row_to_experiment(row: tuple) -> dict:
    return dict(zip(_EXPERIMENT_COLUMNS, row))


def get_experiment(conn: sqlite3.Connection, experiment_id: int) -> dict | None:
    cur = conn.execute(
        f"SELECT {', '.join(_EXPERIMENT_COLUMNS)} FROM experiments WHERE id = ?", (experiment_id,)
    )
    row = cur.fetchone()
    return _row_to_experiment(row) if row else None


def list_experiments(
    conn: sqlite3.Connection, strategy: str | None = None, kind: str | None = None, limit: int = 50
) -> list[dict]:
    query = f"SELECT {', '.join(_EXPERIMENT_COLUMNS)} FROM experiments WHERE 1=1"
    params: list = []
    if strategy is not None:
        query += " AND strategy = ?"
        params.append(strategy)
    if kind is not None:
        query += " AND kind = ?"
        params.append(kind)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(query, params)
    return [_row_to_experiment(row) for row in cur.fetchall()]


def count_experiments(conn: sqlite3.Connection) -> dict[str, int]:
    cur = conn.execute("SELECT kind, COUNT(*) FROM experiments GROUP BY kind")
    counts = {kind: count for kind, count in cur.fetchall()}
    counts["total"] = sum(counts.values())
    return counts


def set_experiment_decision(
    conn: sqlite3.Connection, experiment_id: int, decision: str, reason: str | None
) -> bool:
    cur = conn.execute(
        "UPDATE experiments SET decision = ?, decision_reason = ?, decided_at = ? WHERE id = ?",
        (decision, reason, int(time.time() * 1000), experiment_id),
    )
    conn.commit()
    return cur.rowcount > 0
