import pandas as pd

from bot.backtest.engine import run_backtest
from bot.research.backtest_runner import run_backtest_summary
from bot.strategy.donchian import DonchianBreakoutStrategy


def make_df(closes, wick=0.5):
    close = pd.Series(closes, dtype=float).to_numpy()
    return pd.DataFrame(
        {"open": close, "high": close + wick, "low": close - wick, "close": close, "volume": 1.0},
        index=pd.date_range("2024-01-01", periods=len(close), freq="1D", tz="UTC"),
    )


# flat, then a steady climb that makes a new 5-bar high every bar, then a drop
TREND = [100.0] * 30 + [101.0 + i for i in range(30)] + [110.0] * 5


def strategy():
    return DonchianBreakoutStrategy({"channel_period": 5, "exit_channel_period": 5})


def test_off_by_default_matches_the_engine_without_it():
    df = make_df(TREND)
    plain = run_backtest(df, strategy())
    zero = run_backtest(df, strategy(), pyramid={"max_adds": 0, "add_step_pct": 0.0})
    assert [(t.entry_price, t.exit_price, t.size, t.pnl) for t in plain.trades] == [
        (t.entry_price, t.exit_price, t.size, t.pnl) for t in zero.trades
    ]
    assert plain.equity_curve.equals(zero.equity_curve)
    assert all(t.adds == 0 for t in plain.trades)


def test_adds_on_fresh_signals_in_a_trend():
    df = make_df(TREND)
    plain = run_backtest(df, strategy(), risk_pct=0.002)
    pyramided = run_backtest(df, strategy(), risk_pct=0.002, pyramid={"max_adds": 3, "add_step_pct": 0.0})
    first = pyramided.trades[0]
    assert first.adds == 3
    assert first.size > plain.trades[0].size


def test_adds_stop_when_the_position_is_fully_invested():
    # 1% risk over this tight stop makes the first unit most of equity, so
    # only one add fits before notional would exceed equity (no leverage)
    first = run_backtest(make_df(TREND), strategy(), pyramid={"max_adds": 3}).trades[0]
    assert first.adds == 1
    assert first.size * first.entry_price <= 10_000 * 1.01


def test_step_rule_blocks_adds_until_price_moves_enough():
    df = make_df(TREND)
    far = run_backtest(df, strategy(), pyramid={"max_adds": 3, "add_step_pct": 0.5})
    assert far.trades[0].adds == 0
    near = run_backtest(df, strategy(), pyramid={"max_adds": 3, "add_step_pct": 0.02})
    assert 0 < near.trades[0].adds <= 3


def test_research_summary_reports_adds_only_when_pyramiding():
    df = make_df(TREND)
    base = {"donchian": {"channel_period": 5, "exit_channel_period": 5}}
    summary, _ = run_backtest_summary(df, "donchian", base, {}, "1d")
    assert "adds" not in summary
    summary, _ = run_backtest_summary(df, "donchian", {**base, "pyramid": {"max_adds": 2}}, {"risk_pct": 0.002}, "1d")
    assert summary["adds"] == 2
