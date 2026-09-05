import pandas as pd
import pytest

from bot.backtest.engine import Trade
from bot.backtest.metrics import (
    breakdown_by_strategy,
    buy_hold_return_pct,
    max_drawdown_pct,
    periods_per_year,
    profit_factor,
    sharpe_ratio,
    summarize,
    total_return_pct,
    win_rate_pct,
)


def make_trade(pnl: float, strategy_name: str = "test") -> Trade:
    return Trade(
        direction="long",
        entry_time=0,
        entry_price=100.0,
        exit_time=1,
        exit_price=100.0,
        size=1.0,
        pnl=pnl,
        exit_reason="test",
        strategy_name=strategy_name,
    )


def test_periods_per_year_hourly():
    assert periods_per_year("1h") == pytest.approx(365 * 24)


def test_periods_per_year_daily():
    assert periods_per_year("1d") == pytest.approx(365)


def test_total_return_pct():
    equity_curve = pd.Series([10_000.0, 11_000.0])
    assert total_return_pct(equity_curve, 10_000.0) == pytest.approx(10.0)


def test_total_return_pct_empty_series():
    assert total_return_pct(pd.Series(dtype=float), 10_000.0) == 0.0


def test_max_drawdown_pct():
    equity_curve = pd.Series([100.0, 120.0, 90.0, 110.0])
    assert max_drawdown_pct(equity_curve) == pytest.approx(-25.0)


def test_sharpe_ratio_zero_for_constant_returns():
    # exactly 10% growth every step -> zero variance in returns
    equity_curve = pd.Series([100.0, 110.0, 121.0, 133.1])
    assert sharpe_ratio(equity_curve, "1h") == 0.0


def test_sharpe_ratio_positive_for_noisy_uptrend():
    equity_curve = pd.Series([100.0, 110.0, 105.0, 115.0, 112.0, 120.0])
    assert sharpe_ratio(equity_curve, "1h") > 0


def test_sharpe_ratio_zero_for_short_series():
    assert sharpe_ratio(pd.Series([100.0]), "1h") == 0.0


def test_win_rate_pct():
    trades = [make_trade(100), make_trade(-50), make_trade(50), make_trade(-25)]
    assert win_rate_pct(trades) == pytest.approx(50.0)


def test_win_rate_pct_empty():
    assert win_rate_pct([]) == 0.0


def test_profit_factor():
    trades = [make_trade(100), make_trade(-50), make_trade(50), make_trade(-25)]
    assert profit_factor(trades) == pytest.approx(2.0)  # 150 gross profit / 75 gross loss


def test_profit_factor_no_losses_is_infinite():
    trades = [make_trade(100), make_trade(50)]
    assert profit_factor(trades) == float("inf")


def test_profit_factor_no_trades():
    assert profit_factor([]) == 0.0


def test_summarize_contains_expected_keys():
    equity_curve = pd.Series([10_000.0, 10_500.0, 10_200.0, 10_800.0])
    trades = [make_trade(500), make_trade(-300), make_trade(600)]

    result = summarize(trades, equity_curve, 10_000.0, "1h")

    assert set(result.keys()) == {
        "trades",
        "total_return_pct",
        "max_drawdown_pct",
        "sharpe_ratio",
        "win_rate_pct",
        "profit_factor",
    }
    assert result["trades"] == 3


def test_summarize_includes_buy_hold_pct_when_close_given():
    equity_curve = pd.Series([10_000.0, 10_500.0])
    close = pd.Series([30_000.0, 33_000.0])

    result = summarize([], equity_curve, 10_000.0, "1h", close=close)

    assert result["buy_hold_pct"] == pytest.approx(10.0)


def test_buy_hold_return_pct():
    close = pd.Series([30_000.0, 36_000.0])
    assert buy_hold_return_pct(close) == pytest.approx(20.0)


def test_buy_hold_return_pct_empty_or_single_row():
    assert buy_hold_return_pct(pd.Series(dtype=float)) == 0.0
    assert buy_hold_return_pct(pd.Series([30_000.0])) == 0.0


def test_breakdown_by_strategy_groups_and_computes_per_group_metrics():
    trades = [
        make_trade(100, "donchian"),
        make_trade(-50, "donchian"),
        make_trade(-20, "rsi_bb"),
        make_trade(-30, "rsi_bb"),
    ]

    result = breakdown_by_strategy(trades)

    assert set(result.keys()) == {"donchian", "rsi_bb"}

    assert result["donchian"]["trades"] == 2
    assert result["donchian"]["win_rate_pct"] == pytest.approx(50.0)
    assert result["donchian"]["profit_factor"] == pytest.approx(2.0)
    assert result["donchian"]["total_pnl"] == pytest.approx(50.0)

    assert result["rsi_bb"]["trades"] == 2
    assert result["rsi_bb"]["win_rate_pct"] == 0.0
    assert result["rsi_bb"]["profit_factor"] == 0.0  # no wins at all
    assert result["rsi_bb"]["total_pnl"] == pytest.approx(-50.0)


def test_breakdown_by_strategy_empty_trades():
    assert breakdown_by_strategy([]) == {}
