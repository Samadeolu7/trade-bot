import pandas as pd
import pytest

from bot.backtest.engine import position_size, run_backtest
from bot.strategy.base import Signal, Strategy


def make_df(rows):
    """rows: list of dicts with open/high/low/close. Index is the row position."""
    return pd.DataFrame(rows)


class FakeStrategy(Strategy):
    """Returns a pre-scripted signal keyed by the current bar's index label,
    and never trails the stop (default base behavior) unless overridden."""

    name = "fake"

    def __init__(self, signals_by_index: dict, min_lookback: int = 1):
        super().__init__({})
        self.signals_by_index = signals_by_index
        self.min_lookback = min_lookback

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        return self.signals_by_index.get(df.index[-1])


def long_signal(entry=100.0, stop=90.0, tp=120.0, timestamp=1):
    return Signal(
        symbol="BTC/USDT",
        timeframe="1h",
        direction="long",
        entry_price=entry,
        stop_loss=stop,
        take_profit=tp,
        reason="test",
        timestamp=timestamp,
    )


def test_position_size_scales_with_risk_and_stop_distance():
    assert position_size(10_000, 0.01, entry_price=100, stop_loss=90) == 10.0
    assert position_size(10_000, 0.02, entry_price=100, stop_loss=90) == 20.0


def test_position_size_zero_when_stop_equals_entry():
    assert position_size(10_000, 0.01, entry_price=100, stop_loss=100) == 0.0


def test_long_trade_take_profit_no_fees():
    df = make_df(
        [
            {"open": 100, "high": 100.5, "low": 99.5, "close": 100},  # 0: filler
            {"open": 100, "high": 100.5, "low": 99.5, "close": 100},  # 1: entry bar
            {"open": 105, "high": 105.5, "low": 104.5, "close": 105},  # 2: no exit
            {"open": 118, "high": 125.0, "low": 115.0, "close": 118},  # 3: take-profit hit
            {"open": 120, "high": 120.5, "low": 119.5, "close": 120},  # 4: flat
        ]
    )
    strategy = FakeStrategy({1: long_signal()})

    result = run_backtest(
        df, strategy, fee=0.0, slippage=0.0, initial_capital=10_000.0, risk_pct=0.01
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.direction == "long"
    assert trade.entry_price == 100.0
    assert trade.exit_price == 120.0
    assert trade.size == 10.0
    assert trade.pnl == 200.0
    assert trade.exit_reason == "take_profit"

    assert list(result.equity_curve.values) == [10_000.0, 10_050.0, 10_200.0, 10_200.0]
    assert result.final_equity == 10_200.0


def test_fee_and_slippage_reduce_pnl():
    df = make_df(
        [
            {"open": 100, "high": 100.5, "low": 99.5, "close": 100},
            {"open": 100, "high": 100.5, "low": 99.5, "close": 100},
            {"open": 118, "high": 125.0, "low": 115.0, "close": 118},
        ]
    )
    strategy = FakeStrategy({1: long_signal()})

    result = run_backtest(
        df, strategy, fee=0.001, slippage=0.0005, initial_capital=10_000.0, risk_pct=0.01
    )

    trade = result.trades[0]
    effective_entry = 100 * 1.0005
    effective_exit = 120 * (1 - 0.0005)
    gross_pnl = (effective_exit - effective_entry) * 10.0
    entry_fee = effective_entry * 10.0 * 0.001
    exit_fee = effective_exit * 10.0 * 0.001
    expected_pnl = gross_pnl - entry_fee - exit_fee

    assert trade.entry_price == pytest.approx(effective_entry)
    assert trade.exit_price == pytest.approx(effective_exit)
    assert trade.pnl == pytest.approx(expected_pnl)


def test_stop_loss_takes_precedence_when_both_hit_same_bar():
    df = make_df(
        [
            {"open": 100, "high": 100.5, "low": 99.5, "close": 100},
            {"open": 100, "high": 100.5, "low": 99.5, "close": 100},
            # both stop (90) and take-profit (120) are within this bar's range
            {"open": 100, "high": 130.0, "low": 80.0, "close": 100},
        ]
    )
    strategy = FakeStrategy({1: long_signal(stop=90.0, tp=120.0)})

    result = run_backtest(df, strategy, fee=0.0, slippage=0.0, risk_pct=0.01)

    trade = result.trades[0]
    assert trade.exit_reason == "stop"
    assert trade.exit_price == 90.0


def test_open_position_force_closed_at_end_of_data():
    df = make_df(
        [
            {"open": 100, "high": 100.5, "low": 99.5, "close": 100},
            {"open": 100, "high": 100.5, "low": 99.5, "close": 100},
            {"open": 105, "high": 106.0, "low": 104.0, "close": 105},
        ]
    )
    strategy = FakeStrategy({1: long_signal(stop=50.0, tp=200.0)})  # never hit

    result = run_backtest(df, strategy, fee=0.0, slippage=0.0, risk_pct=0.01)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "end_of_data"
    assert trade.exit_price == 105.0  # last bar's close
    # size = (10_000 * 0.01) / |100 - 50| = 2.0
    assert result.final_equity == 10_010.0  # 10_000 + (105 - 100) * 2.0


def test_insufficient_data_returns_empty_result():
    df = make_df([{"open": 100, "high": 100.5, "low": 99.5, "close": 100}])
    strategy = FakeStrategy({}, min_lookback=1)  # len(df) == min_lookback -> no room to run

    result = run_backtest(df, strategy, risk_pct=0.01)

    assert result.trades == []
    assert result.final_equity == 0.0


def test_no_signals_leaves_equity_unchanged():
    df = make_df([{"open": 100, "high": 100.5, "low": 99.5, "close": 100}] * 5)
    strategy = FakeStrategy({}, min_lookback=1)

    result = run_backtest(df, strategy, initial_capital=10_000.0, risk_pct=0.01)

    assert result.trades == []
    assert result.final_equity == 10_000.0
    assert (result.equity_curve == 10_000.0).all()
