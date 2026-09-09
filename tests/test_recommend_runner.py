import time
from unittest.mock import MagicMock, patch

import pandas as pd

from bot.backtest.engine import open_position as engine_open_position
from bot.recommend.runner import recommend_poll_once, run_recommend_loop
from bot.storage.db import (
    connect,
    get_open_paper_position,
    upsert_candles,
    upsert_fear_greed_entries,
)
from bot.strategy.base import Signal, Strategy


class FakeExchange:
    """Backfill-compatible fake: parse_timeframe as an instance method (like
    real ccxt), fetch_ohlcv serving pre-scripted pages."""

    def __init__(self, pages):
        self.pages = list(pages)

    def parse_timeframe(self, timeframe):
        assert timeframe == "1d"
        return 86400

    def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        if not self.pages:
            return []
        return self.pages.pop(0)


class ScriptedStrategy(Strategy):
    """Same pattern as test_shadow_runner.py's fixture — returns a fixed
    signal (or None) regardless of input."""

    name = "scripted"

    def __init__(self, signal_to_return: Signal | None = None, min_lookback: int = 1, trail_to=None):
        super().__init__({})
        self.signal_to_return = signal_to_return
        self.min_lookback = min_lookback
        self.trail_to = trail_to

    def generate_signal(self, df):
        return self.signal_to_return

    def trail_stop(self, df, direction, current_stop):
        return self.trail_to if self.trail_to is not None else current_stop


class RaisingStrategy(Strategy):
    name = "raising"

    def __init__(self):
        super().__init__({})
        self.min_lookback = 1

    def generate_signal(self, df):
        raise RuntimeError("boom")


def day_ms(n_days_ago: int) -> int:
    ts = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=n_days_ago)
    return int(ts.value // 1_000_000)


def seed_complete_history(conn, n_days=10, close=100.0):
    candles = [[day_ms(d), close, close + 0.5, close - 0.5, close, 1.0] for d in range(n_days, 0, -1)]
    upsert_candles(conn, "binance", "BTC/USDT", "1d", candles)


def seed_fresh_fear_greed(conn, value=55, classification="Neutral"):
    """Avoids recommend_poll_once hitting the real Fear & Greed API in
    tests — a recently-fetched row already satisfies maybe_refresh_fear_greed's
    24h freshness check."""
    now_s = int(time.time())
    upsert_fear_greed_entries(
        conn, [{"value": str(value), "value_classification": classification, "timestamp": str(now_s)}]
    )


def make_conn(tmp_path):
    return connect(str(tmp_path / "recommend.db"))


def test_opens_recommended_position_and_alerts_with_reco_label(tmp_path):
    conn = make_conn(tmp_path)
    seed_complete_history(conn, n_days=10)
    seed_fresh_fear_greed(conn)
    exchange = FakeExchange(pages=[[]])
    alerter = MagicMock()
    alerter.send.return_value = True
    signal = Signal(
        symbol="", timeframe="", direction="long", entry_price=100.0, stop_loss=90.0,
        take_profit=None, reason="test breakout", timestamp=pd.Timestamp.now(tz="UTC"),
        context={"regime": "trending"},
    )
    strategy = ScriptedStrategy(signal_to_return=signal, min_lookback=1)

    recommend_poll_once(
        exchange, conn, alerter, "binance", "BTC/USDT", "1d", [("donchian_adx_control", strategy)],
        fee=0.0, slippage=0.0, backfill_start_date="2020-01-01T00:00:00Z",
    )

    position = get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "reco_donchian_adx_control")
    assert position is not None
    assert position["direction"] == "long"

    alerter.send.assert_called_once()
    msg = alerter.send.call_args[0][0]
    assert "RECOMMENDATION_ENTRY" in msg
    assert "strategy=reco_donchian_adx_control" in msg
    assert "ctx_regime=trending" in msg
    assert "fear_greed=55 (Neutral)" in msg


def test_stop_update_alert_fires_only_when_stop_actually_changes(tmp_path):
    conn = make_conn(tmp_path)
    seed_complete_history(conn, n_days=10)
    seed_fresh_fear_greed(conn)
    exchange = FakeExchange(pages=[[]])
    alerter = MagicMock()
    alerter.send.return_value = True

    entry_signal = Signal(
        symbol="", timeframe="", direction="long", entry_price=100.0, stop_loss=90.0,
        take_profit=None, reason="pre-existing", timestamp=day_ms(2),
    )
    placeholder = ScriptedStrategy()
    position = engine_open_position(entry_signal, size=1.0, fee=0.0, slippage=0.0, owner=placeholder)
    from bot.storage.db import open_paper_position

    open_paper_position(conn, "binance", "BTC/USDT", "1d", "reco_donchian_adx_control", position)

    strategy = ScriptedStrategy(signal_to_return=None, min_lookback=1, trail_to=95.0)
    recommend_poll_once(
        exchange, conn, alerter, "binance", "BTC/USDT", "1d", [("donchian_adx_control", strategy)],
        fee=0.0, slippage=0.0, backfill_start_date="2020-01-01T00:00:00Z",
    )

    alerter.send.assert_called_once()
    msg = alerter.send.call_args[0][0]
    assert "RECOMMENDATION_STOP_UPDATE" in msg
    assert "old_stop=90.00" in msg
    assert "new_stop=95.00" in msg

    updated = get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "reco_donchian_adx_control")
    assert updated["stop"] == 95.0

    # a second iteration where the trail doesn't move should not re-alert
    alerter.reset_mock()
    exchange.pages = [[]]
    recommend_poll_once(
        exchange, conn, alerter, "binance", "BTC/USDT", "1d", [("donchian_adx_control", strategy)],
        fee=0.0, slippage=0.0, backfill_start_date="2020-01-01T00:00:00Z",
    )
    assert alerter.send.call_count == 0


def test_exit_alert_and_trade_recorded_on_stop_hit(tmp_path):
    conn = make_conn(tmp_path)
    seed_complete_history(conn, n_days=10)
    seed_fresh_fear_greed(conn)
    exchange = FakeExchange(pages=[[]])
    alerter = MagicMock()
    alerter.send.return_value = True

    entry_signal = Signal(
        symbol="", timeframe="", direction="long", entry_price=100.0, stop_loss=95.0,
        take_profit=None, reason="pre-existing", timestamp=day_ms(2),
    )
    placeholder = ScriptedStrategy()
    position = engine_open_position(entry_signal, size=1.0, fee=0.0, slippage=0.0, owner=placeholder)
    from bot.storage.db import open_paper_position

    open_paper_position(conn, "binance", "BTC/USDT", "1d", "reco_donchian_adx_control", position)

    # overwrite yesterday's candle so its low breaches the stop
    upsert_candles(conn, "binance", "BTC/USDT", "1d", [[day_ms(1), 100.0, 100.5, 90.0, 92.0, 1.0]])

    strategy = ScriptedStrategy(signal_to_return=None, min_lookback=1)
    recommend_poll_once(
        exchange, conn, alerter, "binance", "BTC/USDT", "1d", [("donchian_adx_control", strategy)],
        fee=0.0, slippage=0.0, backfill_start_date="2020-01-01T00:00:00Z",
    )

    assert get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "reco_donchian_adx_control") is None
    trade_row = conn.execute(
        "SELECT direction, exit_price, exit_reason, strategy_label FROM paper_trades"
    ).fetchone()
    assert trade_row == ("long", 95.0, "stop", "reco_donchian_adx_control")
    msg = alerter.send.call_args[0][0]
    assert "RECOMMENDATION_EXIT" in msg


def test_near_miss_reused_with_reco_label(tmp_path):
    conn = make_conn(tmp_path)
    seed_complete_history(conn, n_days=10)
    seed_fresh_fear_greed(conn)
    exchange = FakeExchange(pages=[[]])
    alerter = MagicMock()
    alerter.send.return_value = True

    class NearMissStrategy(ScriptedStrategy):
        def diagnose(self, df):
            return {"near_miss": True, "near_miss_key": "watching", "near_miss_reason": "coiled"}

    strategy = NearMissStrategy(signal_to_return=None, min_lookback=1)
    recommend_poll_once(
        exchange, conn, alerter, "binance", "BTC/USDT", "1d", [("vol_expansion", strategy)],
        fee=0.0, slippage=0.0, backfill_start_date="2020-01-01T00:00:00Z",
    )

    msg = alerter.send.call_args[0][0]
    assert "NEAR_MISS" in msg
    assert "strategy=reco_vol_expansion" in msg


def test_two_strategies_in_one_cycle_do_not_collide(tmp_path):
    conn = make_conn(tmp_path)
    seed_complete_history(conn, n_days=10)
    seed_fresh_fear_greed(conn)
    exchange = FakeExchange(pages=[[]])
    alerter = MagicMock()
    alerter.send.return_value = True

    signal_a = Signal(
        symbol="", timeframe="", direction="long", entry_price=100.0, stop_loss=90.0,
        take_profit=None, reason="a", timestamp=pd.Timestamp.now(tz="UTC"),
    )
    strategy_a = ScriptedStrategy(signal_to_return=signal_a, min_lookback=1)
    strategy_b = ScriptedStrategy(signal_to_return=None, min_lookback=1)

    recommend_poll_once(
        exchange, conn, alerter, "binance", "BTC/USDT", "1d",
        [("strategy_a", strategy_a), ("strategy_b", strategy_b)],
        fee=0.0, slippage=0.0, backfill_start_date="2020-01-01T00:00:00Z",
    )

    assert get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "reco_strategy_a") is not None
    assert get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "reco_strategy_b") is None


def test_one_strategy_failing_does_not_block_the_others(tmp_path):
    conn = make_conn(tmp_path)
    seed_complete_history(conn, n_days=10)
    seed_fresh_fear_greed(conn)
    exchange = FakeExchange(pages=[[]])
    alerter = MagicMock()
    alerter.send.return_value = True

    signal_b = Signal(
        symbol="", timeframe="", direction="long", entry_price=100.0, stop_loss=90.0,
        take_profit=None, reason="b", timestamp=pd.Timestamp.now(tz="UTC"),
    )
    broken = RaisingStrategy()
    working = ScriptedStrategy(signal_to_return=signal_b, min_lookback=1)

    # should not raise, despite `broken` throwing inside its own slice
    recommend_poll_once(
        exchange, conn, alerter, "binance", "BTC/USDT", "1d",
        [("broken", broken), ("working", working)],
        fee=0.0, slippage=0.0, backfill_start_date="2020-01-01T00:00:00Z",
    )

    assert get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "reco_working") is not None


def test_run_recommend_loop_staggers_startup_and_jitters_interval(tmp_path):
    conn = make_conn(tmp_path)
    seed_complete_history(conn, n_days=2)  # too little history -> fast no-op poll
    seed_fresh_fear_greed(conn)
    exchange = FakeExchange(pages=[[]])
    alerter = MagicMock()
    strategy = ScriptedStrategy(signal_to_return=None, min_lookback=100)

    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            raise KeyboardInterrupt

    with patch("bot.recommend.runner.time.sleep", side_effect=fake_sleep):
        try:
            run_recommend_loop(
                exchange, conn, alerter, "binance", "BTC/USDT", "1d", [("scripted", strategy)],
                fee=0.0, slippage=0.0, backfill_start_date="2020-01-01T00:00:00Z",
                interval_seconds=300, jitter_seconds=30,
            )
        except KeyboardInterrupt:
            pass

    assert len(sleep_calls) == 3
    startup_sleep, first_iteration_sleep, second_iteration_sleep = sleep_calls
    assert 0 <= startup_sleep <= 300
    for jittered in (first_iteration_sleep, second_iteration_sleep):
        assert 270 <= jittered <= 330
