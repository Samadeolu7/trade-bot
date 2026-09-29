import numpy as np
import pandas as pd
import pytest

from bot.backtest.exposure import run_exposure_backtest
from bot.strategy.donchian_ensemble import DonchianEnsembleStrategy


def make_df(closes, freq="D"):
    close = pd.Series(closes, dtype=float)
    index = pd.date_range("2024-01-01", periods=len(close), freq=freq, tz="UTC")
    return pd.DataFrame(
        {"open": close.values, "high": close.values, "low": close.values, "close": close.values, "volume": 1.0},
        index=index,
    )


def one_model(n=5, **params):
    return DonchianEnsembleStrategy({"lookback_days": [n], "vol_window_days": 3, **params})


def test_model_enters_on_close_above_prior_high_and_exits_below_ratcheting_midpoint():
    # flat 100 for 5 bars, breakout to 110, drift up, then drop through the midpoint
    closes = [100] * 5 + [110, 112, 114, 116, 118, 108]
    positions = one_model(5).model_positions(make_df(closes))[5].tolist()
    assert positions[:5] == [0.0] * 5
    assert positions[5:10] == [1.0] * 5
    # midpoint of the last 5 closes at bar 9 is (118 + 110) / 2 = 114; 108 < 114 exits
    assert positions[10] == 0.0


def test_stop_never_moves_down():
    closes = [100] * 5 + [110, 120, 105, 104]  # stop ratchets to 110 at bar 6, 105 is below it
    positions = one_model(5).model_positions(make_df(closes))[5].tolist()
    assert positions[6] == 1.0
    assert positions[7] == 0.0


def test_ensemble_signal_is_fraction_of_models_long():
    closes = [100] * 20 + [101]  # breaks every lookback's prior high at once
    strategy = DonchianEnsembleStrategy({"lookback_days": [5, 10, 20], "vol_window_days": 3})
    signal = strategy.ensemble_signal(make_df(closes))
    assert signal.iloc[-1] == 1.0
    assert signal.iloc[-2] == 0.0


def test_weights_scale_inversely_with_volatility_and_are_capped():
    rng = np.random.default_rng(0)
    calm = 100 * np.exp(np.cumsum(np.full(60, 0.002) + rng.normal(0, 0.005, 60)))
    wild = 100 * np.exp(np.cumsum(np.full(60, 0.002) + rng.normal(0, 0.05, 60)))
    s = DonchianEnsembleStrategy({"lookback_days": [5], "vol_window_days": 20, "vol_target": 0.25})
    calm_w = s.target_weights(make_df(calm))
    wild_w = s.target_weights(make_df(wild))
    assert calm_w.max() <= 1.0  # max_weight caps a low-vol series
    long_bars = (s.ensemble_signal(make_df(wild)) > 0) & (s.ensemble_signal(make_df(calm)) > 0)
    assert (wild_w[long_bars.values].mean()) < (calm_w[long_bars.values].mean())


def test_bars_per_day_scales_lookbacks():
    s = DonchianEnsembleStrategy({"bars_per_day": 6})
    assert s.lookbacks == [30, 60, 120, 180, 360, 540, 900, 1500, 2160]
    assert s.vol_window == 540
    assert s.periods_per_year == 2190


def test_generate_signal_is_none_and_diagnose_reports_models():
    closes = list(np.linspace(100, 200, 400))
    s = DonchianEnsembleStrategy({})
    df = make_df(closes)
    assert s.generate_signal(df) is None
    d = s.diagnose(df)
    assert d["ready"] is True
    assert d["models_long"] == "9/9"
    assert d["near_miss"] is False


def test_exposure_backtest_full_weight_matches_price_move_minus_costs():
    df = make_df([100, 110, 121])
    weights = pd.Series([1.0, 1.0, 1.0], index=df.index)
    result = run_exposure_backtest(df, one_model(), fee=0.001, slippage=0.0, initial_capital=1000, weights=weights)
    # buy at 100 paying 0.1%, then +21%, still holding at the end
    assert result.equity_curve.iloc[-1] == pytest.approx(1000 * 0.999 * 1.21)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "end_of_data"


def test_exposure_backtest_half_weight_earns_half_the_move():
    df = make_df([100, 120])
    weights = pd.Series([0.5, 0.5], index=df.index)
    result = run_exposure_backtest(df, one_model(), fee=0.0, slippage=0.0, initial_capital=1000, weights=weights)
    assert result.equity_curve.iloc[-1] == pytest.approx(1100)


def test_exposure_backtest_weight_applies_from_next_bar_no_lookahead():
    df = make_df([100, 200, 200])
    weights = pd.Series([0.0, 1.0, 1.0], index=df.index)  # goes long at bar 1's close, after the jump
    result = run_exposure_backtest(df, one_model(), fee=0.0, slippage=0.0, initial_capital=1000, weights=weights)
    assert result.equity_curve.iloc[-1] == pytest.approx(1000)


def test_exposure_backtest_records_episodes_and_charges_both_sides():
    df = make_df([100, 100, 100, 100])
    weights = pd.Series([1.0, 0.0, 1.0, 0.0], index=df.index)
    result = run_exposure_backtest(df, one_model(), fee=0.01, slippage=0.0, initial_capital=1000, weights=weights)
    assert len(result.trades) == 2
    assert all(t.exit_reason == "flat" and t.pnl < 0 for t in result.trades)
    assert result.equity_curve.iloc[-1] == pytest.approx(1000 * 0.99**4)


def test_rebalance_threshold_skips_small_changes_but_always_goes_flat():
    df = make_df([100, 100, 100, 100])
    weights = pd.Series([0.5, 0.52, 0.51, 0.0], index=df.index)
    s = one_model(rebalance_threshold=0.05)
    result = run_exposure_backtest(df, s, fee=0.01, slippage=0.0, initial_capital=1000, weights=weights)
    # only two trades of notional 0.5 (in, then out), each costing 0.5 * 1%
    assert result.equity_curve.iloc[-1] == pytest.approx(1000 * 0.995 * 0.995)
