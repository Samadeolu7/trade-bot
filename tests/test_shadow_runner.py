from unittest.mock import MagicMock, patch

import pandas as pd

from bot.backtest.engine import open_position as engine_open_position
from bot.shadow.runner import (
    drop_incomplete_bar,
    maybe_send_daily_summary,
    maybe_send_heartbeat,
    maybe_send_near_miss_alert,
    run_shadow_loop,
    shadow_poll_once,
)
from bot.storage.db import (
    connect,
    get_open_paper_position,
    get_state,
    open_paper_position,
    upsert_candles,
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
    """Returns a fixed signal (or None) regardless of input, and never
    trails the stop (default base behavior) unless overridden — matches the
    FakeStrategy pattern already used in test_backtest_engine.py."""

    name = "scripted"

    def __init__(self, signal_to_return: Signal | None, min_lookback: int = 1):
        super().__init__({})
        self.signal_to_return = signal_to_return
        self.min_lookback = min_lookback

    def generate_signal(self, df):
        return self.signal_to_return


STRATEGY_LABEL = "scripted"


def day_ms(n_days_ago: int) -> int:
    ts = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=n_days_ago)
    return int(ts.value // 1_000_000)


def seed_complete_history(conn, n_days=10, close=100.0):
    """n_days of fully-completed daily candles, the most recent being
    yesterday — "today" is deliberately never seeded here, so tests control
    it explicitly when they need to."""
    candles = [[day_ms(d), close, close + 0.5, close - 0.5, close, 1.0] for d in range(n_days, 0, -1)]
    upsert_candles(conn, "binance", "BTC/USDT", "1d", candles)


def make_conn(tmp_path):
    return connect(str(tmp_path / "shadow.db"))


def test_skips_when_not_enough_complete_history(tmp_path):
    conn = make_conn(tmp_path)
    seed_complete_history(conn, n_days=2)
    exchange = FakeExchange(pages=[[]])
    alerter = MagicMock()
    strategy = ScriptedStrategy(signal_to_return=None, min_lookback=100)

    shadow_poll_once(
        exchange, conn, alerter, "binance", "BTC/USDT", "1d", strategy, STRATEGY_LABEL,
        fee=0.001, slippage=0.0005, backfill_start_date="2020-01-01T00:00:00Z",
    )

    assert get_open_paper_position(conn, "binance", "BTC/USDT", "1d", STRATEGY_LABEL) is None
    alerter.send.assert_not_called()


def test_opens_paper_position_and_alerts_on_signal(tmp_path):
    conn = make_conn(tmp_path)
    seed_complete_history(conn, n_days=10)
    exchange = FakeExchange(pages=[[]])
    alerter = MagicMock()
    signal = Signal(
        symbol="", timeframe="", direction="long", entry_price=100.0, stop_loss=90.0,
        take_profit=None, reason="test breakout", timestamp=pd.Timestamp.now(tz="UTC"),
    )
    strategy = ScriptedStrategy(signal_to_return=signal, min_lookback=1)

    shadow_poll_once(
        exchange, conn, alerter, "binance", "BTC/USDT", "1d", strategy, STRATEGY_LABEL,
        fee=0.0, slippage=0.0, backfill_start_date="2020-01-01T00:00:00Z",
    )

    position = get_open_paper_position(conn, "binance", "BTC/USDT", "1d", STRATEGY_LABEL)
    assert position is not None
    assert position["direction"] == "long"
    assert position["entry_price"] == 100.0

    alerter.send.assert_called_once()
    assert "SIGNAL_FIRED" in alerter.send.call_args[0][0]

    row = conn.execute("SELECT direction, reason, strategy_label FROM signals").fetchone()
    assert row == ("long", "test breakout", STRATEGY_LABEL)


def test_does_not_reevaluate_entries_while_position_open(tmp_path):
    conn = make_conn(tmp_path)
    seed_complete_history(conn, n_days=10)
    exchange = FakeExchange(pages=[[]])
    alerter = MagicMock()
    signal = Signal(
        symbol="", timeframe="", direction="long", entry_price=100.0, stop_loss=90.0,
        take_profit=None, reason="test breakout", timestamp=pd.Timestamp.now(tz="UTC"),
    )
    strategy = ScriptedStrategy(signal_to_return=signal, min_lookback=1)

    shadow_poll_once(
        exchange, conn, alerter, "binance", "BTC/USDT", "1d", strategy, STRATEGY_LABEL,
        fee=0.0, slippage=0.0, backfill_start_date="2020-01-01T00:00:00Z",
    )
    assert alerter.send.call_count == 1

    # a second iteration with the exact same "fresh signal" available should
    # NOT re-fire — a position is already open, so entry logic is skipped
    exchange.pages = [[]]
    shadow_poll_once(
        exchange, conn, alerter, "binance", "BTC/USDT", "1d", strategy, STRATEGY_LABEL,
        fee=0.0, slippage=0.0, backfill_start_date="2020-01-01T00:00:00Z",
    )
    assert alerter.send.call_count == 1
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1


def test_closes_paper_position_on_stop_hit(tmp_path):
    conn = make_conn(tmp_path)
    seed_complete_history(conn, n_days=10)  # latest complete bar = yesterday, flat closes=100
    exchange = FakeExchange(pages=[[]])
    alerter = MagicMock()

    entry_signal = Signal(
        symbol="", timeframe="", direction="long", entry_price=100.0, stop_loss=95.0,
        take_profit=None, reason="pre-existing", timestamp=day_ms(2),
    )
    placeholder_strategy = ScriptedStrategy(signal_to_return=None, min_lookback=1)
    position = engine_open_position(
        entry_signal, size=1.0, fee=0.0, slippage=0.0, owner=placeholder_strategy
    )
    open_paper_position(conn, "binance", "BTC/USDT", "1d", STRATEGY_LABEL, position)

    # overwrite yesterday's (already-complete) candle so its low breaches the stop
    upsert_candles(conn, "binance", "BTC/USDT", "1d", [[day_ms(1), 100.0, 100.5, 90.0, 92.0, 1.0]])

    strategy = ScriptedStrategy(signal_to_return=None, min_lookback=1)
    shadow_poll_once(
        exchange, conn, alerter, "binance", "BTC/USDT", "1d", strategy, STRATEGY_LABEL,
        fee=0.0, slippage=0.0, backfill_start_date="2020-01-01T00:00:00Z",
    )

    assert get_open_paper_position(conn, "binance", "BTC/USDT", "1d", STRATEGY_LABEL) is None
    trade_row = conn.execute(
        "SELECT direction, exit_price, exit_reason FROM paper_trades"
    ).fetchone()
    assert trade_row == ("long", 95.0, "stop")
    alerter.send.assert_called_once()
    assert "POSITION_CLOSED" in alerter.send.call_args[0][0]


def test_end_to_end_with_real_production_strategy(tmp_path):
    """Not a strategy-correctness test (that's what backtests are for) —
    this exists to catch integration bugs (argument/attribute mismatches
    between the shadow runner and real Strategy objects) that a stubbed
    ScriptedStrategy can't reveal, using the actual deployed config."""
    import main as main_module
    from bot.config import load_config

    conn = make_conn(tmp_path)
    config = load_config()
    strategy = main_module._build_strategy("regime_switched", config["strategy"])

    # a steady sustained uptrend both breaks the donchian entry channel and
    # builds up ADX enough to read as "trending" by the end of the series
    n_days = 90
    candles = [
        [day_ms(d), 30000.0 + (n_days - d) * 50.0, 30000.0 + (n_days - d) * 50.0 + 20,
         30000.0 + (n_days - d) * 50.0 - 20, 30000.0 + (n_days - d) * 50.0, 1.0]
        for d in range(n_days, 0, -1)
    ]
    upsert_candles(conn, "binance", "BTC/USDT", "1d", candles)

    exchange = FakeExchange(pages=[[]])
    alerter = MagicMock()

    shadow_poll_once(
        exchange, conn, alerter, "binance", "BTC/USDT", "1d", strategy, "regime_switched",
        fee=0.001, slippage=0.0005, backfill_start_date="2020-01-01T00:00:00Z",
    )

    position = get_open_paper_position(conn, "binance", "BTC/USDT", "1d", "regime_switched")
    assert position is not None
    assert position["direction"] == "long"
    alerter.send.assert_called_once()
    assert "SIGNAL_FIRED" in alerter.send.call_args[0][0]


def test_heartbeat_retries_every_call_until_delivery_succeeds(tmp_path):
    conn = make_conn(tmp_path)
    failing_alerter = MagicMock()
    failing_alerter.send.return_value = False

    maybe_send_heartbeat(conn, failing_alerter, "BTC/USDT", "1d", STRATEGY_LABEL, interval_seconds=86400)
    assert get_state(conn, f"{STRATEGY_LABEL}:last_heartbeat_at") is None  # not marked sent — should retry

    maybe_send_heartbeat(conn, failing_alerter, "BTC/USDT", "1d", STRATEGY_LABEL, interval_seconds=86400)
    assert failing_alerter.send.call_count == 2  # retried immediately, not backed off 24h

    succeeding_alerter = MagicMock()
    succeeding_alerter.send.return_value = True
    maybe_send_heartbeat(conn, succeeding_alerter, "BTC/USDT", "1d", STRATEGY_LABEL, interval_seconds=86400)
    assert get_state(conn, f"{STRATEGY_LABEL}:last_heartbeat_at") is not None  # now correctly marked sent

    # a further call within the interval should not re-send
    maybe_send_heartbeat(conn, succeeding_alerter, "BTC/USDT", "1d", STRATEGY_LABEL, interval_seconds=86400)
    assert succeeding_alerter.send.call_count == 1


def test_heartbeat_scoped_by_strategy_label(tmp_path):
    conn = make_conn(tmp_path)
    alerter = MagicMock()
    alerter.send.return_value = True

    maybe_send_heartbeat(conn, alerter, "BTC/USDT", "1d", "donchian", interval_seconds=86400)
    # a different strategy's heartbeat schedule is independent — not skipped
    maybe_send_heartbeat(conn, alerter, "BTC/USDT", "1d", "multi_timeframe", interval_seconds=86400)
    assert alerter.send.call_count == 2


def test_daily_summary_retries_until_delivery_succeeds(tmp_path):
    conn = make_conn(tmp_path)
    strategy = ScriptedStrategy(signal_to_return=None, min_lookback=100)  # never "ready" — keeps diagnose out of the way
    failing_alerter = MagicMock()
    failing_alerter.send.return_value = False

    maybe_send_daily_summary(conn, failing_alerter, "binance", "BTC/USDT", "1d", STRATEGY_LABEL, strategy)
    assert get_state(conn, f"{STRATEGY_LABEL}:last_summary_date") is None

    maybe_send_daily_summary(conn, failing_alerter, "binance", "BTC/USDT", "1d", STRATEGY_LABEL, strategy)
    assert failing_alerter.send.call_count == 2  # retried, not skipped for the rest of the day

    succeeding_alerter = MagicMock()
    succeeding_alerter.send.return_value = True
    maybe_send_daily_summary(conn, succeeding_alerter, "binance", "BTC/USDT", "1d", STRATEGY_LABEL, strategy)
    assert get_state(conn, f"{STRATEGY_LABEL}:last_summary_date") is not None

    maybe_send_daily_summary(conn, succeeding_alerter, "binance", "BTC/USDT", "1d", STRATEGY_LABEL, strategy)
    assert succeeding_alerter.send.call_count == 1


def test_daily_summary_includes_diagnosis_when_strategy_is_ready(tmp_path):
    conn = make_conn(tmp_path)
    seed_complete_history(conn, n_days=10)
    strategy = ScriptedStrategy(signal_to_return=None, min_lookback=1)
    alerter = MagicMock()
    alerter.send.return_value = True

    maybe_send_daily_summary(conn, alerter, "binance", "BTC/USDT", "1d", STRATEGY_LABEL, strategy)

    msg = alerter.send.call_args[0][0]
    assert "diag_near_miss=False" in msg
    assert "diag_near_miss_key" not in msg  # internal dedup token, not for humans


def test_near_miss_alert_fires_once_then_suppresses_until_state_changes(tmp_path):
    conn = make_conn(tmp_path)
    close = pd.Series([100.0] * 5, index=pd.date_range("2020-01-01", periods=5, freq="D", tz="UTC"))
    df = pd.DataFrame(
        {"open": close, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 1.0}
    )

    class NearMissStrategy(Strategy):
        name = "near_miss_test"

        def __init__(self):
            super().__init__({})
            self.min_lookback = 1
            self.key = "condition_a"

        def generate_signal(self, df):
            return None

        def diagnose(self, df):
            return {"near_miss": True, "near_miss_key": self.key, "near_miss_reason": f"reason for {self.key}"}

    strategy = NearMissStrategy()
    alerter = MagicMock()
    alerter.send.return_value = True

    maybe_send_near_miss_alert(conn, alerter, "BTC/USDT", "1d", STRATEGY_LABEL, strategy, df)
    assert alerter.send.call_count == 1
    assert "NEAR_MISS" in alerter.send.call_args[0][0]

    # same condition again — suppressed, not re-alerted every poll
    maybe_send_near_miss_alert(conn, alerter, "BTC/USDT", "1d", STRATEGY_LABEL, strategy, df)
    assert alerter.send.call_count == 1

    # a materially different condition re-triggers it
    strategy.key = "condition_b"
    maybe_send_near_miss_alert(conn, alerter, "BTC/USDT", "1d", STRATEGY_LABEL, strategy, df)
    assert alerter.send.call_count == 2


def test_drop_incomplete_bar_keeps_bars_whose_period_has_ended():
    close = pd.Series([100.0, 101.0, 102.0])
    df = pd.DataFrame(
        {"open": close, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 1.0},
        index=[
            pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=3),
            pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=2),
            pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=1),
        ],
    )
    result = drop_incomplete_bar(df, "1d")
    assert len(result) == 3  # all three days have fully ended


def test_drop_incomplete_bar_drops_bar_still_in_progress():
    close = pd.Series([100.0, 101.0])
    df = pd.DataFrame(
        {"open": close, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 1.0},
        index=[
            pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=1),
            pd.Timestamp.now(tz="UTC").normalize(),  # today — still forming
        ],
    )
    result = drop_incomplete_bar(df, "1d")
    assert len(result) == 1
    assert result.index[-1] == df.index[0]


def test_run_shadow_loop_staggers_startup_and_jitters_interval(tmp_path):
    """Concurrent shadow containers all restart together on every deploy, so
    without a startup stagger they'd poll Binance in the same instant every
    cycle indefinitely — this hit in production (2026-09-08). Verifies the
    startup sleep and the per-iteration jitter stay within their intended
    bounds, without ever actually sleeping."""
    conn = make_conn(tmp_path)
    seed_complete_history(conn, n_days=2)  # too little history -> poll is a fast no-op
    exchange = FakeExchange(pages=[[]])
    alerter = MagicMock()
    strategy = ScriptedStrategy(signal_to_return=None, min_lookback=100)

    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            raise KeyboardInterrupt  # escape the infinite loop after 3 sleeps

    with patch("bot.shadow.runner.time.sleep", side_effect=fake_sleep):
        try:
            run_shadow_loop(
                exchange, conn, alerter, "binance", "BTC/USDT", "1d", strategy, STRATEGY_LABEL,
                fee=0.0, slippage=0.0, backfill_start_date="2020-01-01T00:00:00Z",
                interval_seconds=300, jitter_seconds=30,
            )
        except KeyboardInterrupt:
            pass

    assert len(sleep_calls) == 3
    startup_sleep, first_iteration_sleep, second_iteration_sleep = sleep_calls
    assert 0 <= startup_sleep <= 300  # one-time stagger, up to a full interval
    for jittered in (first_iteration_sleep, second_iteration_sleep):
        assert 270 <= jittered <= 330  # interval +/- jitter_seconds, never negative
