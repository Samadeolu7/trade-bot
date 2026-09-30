import numpy as np
import pandas as pd
import pytest

from bot.backtest.exposure import run_exposure_backtest
from bot.backtest.portfolio import run_portfolio_backtest, select_universe
from bot.strategy.donchian_ensemble import DonchianEnsembleStrategy


def frame(closes, volume=1.0, start="2022-01-01", freq="D"):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": volume}, index=idx)


def walk(n, seed, drift=0.001):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(drift, 0.03, n)))


def test_one_coin_one_slot_matches_the_single_asset_exposure_backtest():
    df = frame(walk(900, 1))
    strategy = DonchianEnsembleStrategy({"lookback_days": [5, 20, 60], "vol_window_days": 20, "rebalance_threshold": 0.1})
    weights = strategy.target_weights(df)
    window = df.index[500:]
    single = run_exposure_backtest(df.loc[window], strategy, fee=0.001, slippage=0.0005, weights=weights.reindex(window))
    port = run_portfolio_backtest({"AAA": df}, {"AAA": weights}, window, size=1, fee=0.001, slippage=0.0005,
                                  rebalance_threshold=0.1, min_history_days=0)
    assert port.equity_curve.iloc[-1] == pytest.approx(single.equity_curve.iloc[-1], rel=1e-9)


def test_universe_ranks_by_prior_volume_and_needs_a_year_of_history():
    old_big = frame(walk(800, 2), volume=1000.0, start="2020-01-01")
    old_small = frame(walk(800, 3), volume=10.0, start="2020-01-01")
    new_huge = frame(walk(200, 4), volume=1e6, start="2021-09-01")  # listed < 1 year before 2022-01
    starts = pd.DatetimeIndex([pd.Timestamp("2022-01-01", tz="UTC")])
    picked = select_universe({"BIG": old_big, "SMALL": old_small, "NEW": new_huge}, starts, size=1)
    assert picked[starts[0]] == ["BIG"]
    two = select_universe({"BIG": old_big, "SMALL": old_small, "NEW": new_huge}, starts, size=5)
    assert two[starts[0]] == ["BIG", "SMALL"]


def test_universe_ignores_volume_after_the_month_start():
    a = frame(walk(800, 5), volume=100.0, start="2020-01-01")
    b = frame(walk(800, 6), volume=1.0, start="2020-01-01")
    b.loc[b.index >= "2021-06-01", "volume"] = 1e9  # b only becomes big later
    starts = pd.DatetimeIndex([pd.Timestamp("2021-06-01", tz="UTC")])
    assert select_universe({"A": a, "B": b}, starts, size=1)[starts[0]] == ["A"]


def test_equal_slots_cap_exposure_and_a_coin_leaving_the_universe_is_sold():
    n = 900
    a = frame(np.linspace(100, 300, n), volume=100.0, start="2020-01-01")  # steady uptrend
    b = frame(np.linspace(100, 300, n), volume=50.0, start="2020-01-01")
    b.loc[b.index >= "2022-03-01", "volume"] = 0.0  # b's volume vanishes: drops out
    weights = {"A": pd.Series(1.0, index=a.index), "B": pd.Series(1.0, index=b.index)}
    window = a.index[a.index >= "2022-01-01"]
    res = run_portfolio_backtest({"A": a, "B": b}, weights, window, size=2, fee=0.0, slippage=0.0)
    assert res.universe["2022-01"] == ["A", "B"]
    assert res.universe["2022-04"] == ["A"]
    assert res.exposure.max() <= 1.0 + 1e-9
    assert float(res.exposure.loc["2022-04-15"]) == pytest.approx(0.5, abs=0.02)  # only A's slot invested
