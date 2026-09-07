import pandas as pd

from bot.storage.db import (
    close_paper_position,
    connect,
    count_paper_trades_all_time,
    count_paper_trades_since,
    get_open_paper_position,
    get_state,
    open_paper_position,
    record_paper_trade,
    record_signal,
    set_state,
    sum_paper_trade_pnl_pct,
    update_paper_position_stop,
)
from bot.strategy.base import Signal


def make_conn():
    return connect(":memory:")


def test_record_signal():
    conn = make_conn()
    signal = Signal(
        symbol="BTC/USDT", timeframe="1d", direction="long", entry_price=100.0,
        stop_loss=90.0, take_profit=None, reason="test",
        timestamp=pd.Timestamp("2026-01-01T00:00:00Z"),
        context={"channel_period": 20},
    )
    record_signal(conn, "binance", "BTC/USDT", "1d", "donchian", signal)

    row = conn.execute("SELECT direction, entry_price, strategy_label, context, fired_at FROM signals").fetchone()
    assert row[0] == "long"
    assert row[1] == 100.0
    assert row[2] == "donchian"
    assert row[3] == '{"channel_period": 20}'
    assert row[4] == int(pd.Timestamp("2026-01-01T00:00:00Z").value // 1_000_000)


def test_paper_position_round_trip():
    conn = make_conn()
    assert get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "donchian") is None

    position = {
        "direction": "long",
        "entry_price": 100.0,
        "stop": 90.0,
        "take_profit": None,
        "entry_time": pd.Timestamp("2026-01-01T00:00:00Z"),
        "entry_fee": 0.1,
        "size": 1.0,
        "context": {"atr_pct": 1.2},
    }
    open_paper_position(conn, "binance", "BTC/USDT", "1d", "donchian", position)

    fetched = get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "donchian")
    assert fetched["direction"] == "long"
    assert fetched["entry_price"] == 100.0
    assert fetched["stop"] == 90.0
    assert fetched["context"] == {"atr_pct": 1.2}

    update_paper_position_stop(conn, "binance", "BTC/USDT", "1d", "donchian", 95.0)
    fetched = get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "donchian")
    assert fetched["stop"] == 95.0

    close_paper_position(conn, "binance", "BTC/USDT", "1d", "donchian")
    assert get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "donchian") is None


def test_paper_position_scoped_by_symbol_timeframe():
    conn = make_conn()
    position = {
        "direction": "long", "entry_price": 100.0, "stop": 90.0, "take_profit": None,
        "entry_time": pd.Timestamp("2026-01-01T00:00:00Z"), "entry_fee": 0.0, "size": 1.0,
    }
    open_paper_position(conn, "binance", "BTC/USDT", "1d", "donchian", position)
    assert get_open_paper_position(conn, "binance", "ETH/USDT", "1d", "donchian") is None
    assert get_open_paper_position(conn, "binance", "BTC/USDT", "4h", "donchian") is None
    assert get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "donchian") is not None


def test_paper_position_scoped_by_strategy_label():
    """Concurrent shadow runs on the same symbol/timeframe must not collide —
    strategy_label is part of the identity."""
    conn = make_conn()
    position = {
        "direction": "long", "entry_price": 100.0, "stop": 90.0, "take_profit": None,
        "entry_time": pd.Timestamp("2026-01-01T00:00:00Z"), "entry_fee": 0.0, "size": 1.0,
    }
    open_paper_position(conn, "binance", "BTC/USDT", "1d", "donchian", position)
    assert get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "multi_timeframe") is None
    assert get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "donchian") is not None


def test_record_and_aggregate_paper_trades():
    conn = make_conn()
    record_paper_trade(
        conn, "binance", "BTC/USDT", "1d", "donchian", "long",
        pd.Timestamp("2026-01-01T00:00:00Z"), 100.0,
        pd.Timestamp("2026-01-05T00:00:00Z"), 110.0,
        pnl=10.0, pnl_pct=10.0, exit_reason="take_profit", closed_at=pd.Timestamp("2026-01-05T00:00:00Z"),
    )
    record_paper_trade(
        conn, "binance", "BTC/USDT", "1d", "donchian", "long",
        pd.Timestamp("2026-01-06T00:00:00Z"), 110.0,
        pd.Timestamp("2026-01-09T00:00:00Z"), 105.0,
        pnl=-5.0, pnl_pct=-4.5, exit_reason="stop", closed_at=pd.Timestamp("2026-01-09T00:00:00Z"),
    )
    # a concurrent strategy's trade on the same symbol/timeframe must not be
    # counted in donchian's aggregates
    record_paper_trade(
        conn, "binance", "BTC/USDT", "1d", "multi_timeframe", "long",
        pd.Timestamp("2026-01-01T00:00:00Z"), 100.0,
        pd.Timestamp("2026-01-05T00:00:00Z"), 120.0,
        pnl=20.0, pnl_pct=20.0, exit_reason="take_profit", closed_at=pd.Timestamp("2026-01-05T00:00:00Z"),
    )

    assert count_paper_trades_all_time(conn, "binance", "BTC/USDT", "1d", "donchian") == 2
    assert sum_paper_trade_pnl_pct(conn, "binance", "BTC/USDT", "1d", "donchian") == 5.5

    since_ms = int(pd.Timestamp("2026-01-06T00:00:00Z").value // 1_000_000)
    assert count_paper_trades_since(conn, "binance", "BTC/USDT", "1d", "donchian", since_ms) == 1


def test_bot_state_get_set():
    conn = make_conn()
    assert get_state(conn, "last_heartbeat_at") is None
    set_state(conn, "last_heartbeat_at", "12345")
    assert get_state(conn, "last_heartbeat_at") == "12345"
    set_state(conn, "last_heartbeat_at", "67890")
    assert get_state(conn, "last_heartbeat_at") == "67890"
