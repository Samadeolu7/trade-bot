import pandas as pd

from bot.strategy.market_structure import MarketStructureBreakoutStrategy


def make_ohlc_df(closes, wick=0.1):
    close = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {"open": close, "high": close + wick, "low": close - wick, "close": close, "volume": 1.0}
    )


def build_strategy():
    return MarketStructureBreakoutStrategy(
        {
            "swing_window": 2,
            "retest_window": 5,
            "retest_tolerance_pct": 0.01,
            "atr_period": 1,
            "atr_mult": 1.0,
        }
    )


# A single unambiguous swing high at index 10 (100), monotonic up then down
# around it so no other bar can tie/exceed it as a local extreme, then a
# gentle breakout (17), a retest touching back near the level (18), and a
# confirming close back above it (20).
LONG_SCENARIO_CLOSES = [
    80, 82, 84, 86, 88, 90, 92, 94, 96, 98,  # 0-9: rising into the peak
    100,                                       # 10: the swing high
    96, 92, 88, 84, 80,                        # 11-15: falling away from it
    85,                                         # 16: still below the level
    100.3,                                      # 17: breakout (close > 100.1)
    100.5,                                      # 18: retest (low 100.4 <= 101.101)
    99.8,                                       # 19: still hovering near the level
    103,                                         # 20: confirmation close
]


def test_min_lookback_formula():
    strategy = build_strategy()
    assert strategy.min_lookback == 2 * 4 + 5 + 1 * 3  # == 16


def test_no_signal_when_insufficient_data():
    strategy = build_strategy()
    df = make_ohlc_df(LONG_SCENARIO_CLOSES[:10])
    assert strategy.generate_signal(df) is None


def test_no_signal_before_breakout_occurs():
    strategy = build_strategy()
    df = make_ohlc_df(LONG_SCENARIO_CLOSES[:16])  # peak confirmed, but no breakout yet
    assert strategy.generate_signal(df) is None


def test_long_signal_on_confirmed_breakout_and_retest():
    strategy = build_strategy()
    df = make_ohlc_df(LONG_SCENARIO_CLOSES)

    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == 103.0
    assert signal.stop_loss == 99.7  # min low from the retest bar (18) through now (20)
    assert signal.take_profit is None
    assert "100.10" in signal.reason
    assert "retested" in signal.reason


def test_no_signal_if_retest_never_happens_within_window():
    # breakout at 17, then price runs straight up and away without ever
    # coming back near the level — no valid retest within retest_window
    closes = LONG_SCENARIO_CLOSES[:17] + [120, 121, 122, 123, 124, 125]
    strategy = build_strategy()
    df = make_ohlc_df(closes)
    assert strategy.generate_signal(df) is None


def test_trail_stop_only_ratchets_favorably():
    strategy = build_strategy()
    df = make_ohlc_df(LONG_SCENARIO_CLOSES)

    stop_far_below = 50.0
    ratcheted = strategy.trail_stop(df, "long", stop_far_below)
    assert ratcheted > stop_far_below

    stop_already_tight = 102.9
    assert strategy.trail_stop(df, "long", stop_already_tight) == stop_already_tight
