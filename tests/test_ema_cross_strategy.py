import pandas as pd

from bot.strategy.ema_cross import EmaCrossStrategy


def make_df(closes, wick=0.5):
    close = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + wick,
            "low": close - wick,
            "close": close,
            "volume": 1.0,
        }
    )


def build_strategy():
    # small periods so an 11-bar window (this strategy's min_lookback) is enough
    return EmaCrossStrategy(
        {"fast_period": 2, "slow_period": 3, "atr_period": 2, "atr_mult": 1.0}
    )


def test_min_lookback_computed_from_params():
    strategy = build_strategy()
    assert strategy.min_lookback == max(3, 2) * 3 + 2  # == 11


def test_no_signal_when_below_min_lookback():
    strategy = build_strategy()
    df = make_df([100.0] * 5)
    assert strategy.generate_signal(df) is None


def test_long_signal_on_cross_up():
    strategy = build_strategy()
    # flat, then a dip (fast EMA falls below slow), then a sharp rally
    # (fast EMA — more reactive — jumps back above slow): a cross up on the last bar
    df = make_df([100.0] * 9 + [95.0, 110.0])
    assert len(df) == strategy.min_lookback

    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == 110.0
    assert signal.stop_loss < signal.entry_price
    assert signal.take_profit is None
    assert "crossed above" in signal.reason


def test_short_signal_on_cross_down():
    strategy = build_strategy()
    # flat, then a spike (fast EMA rises above slow), then a sharp crash
    # (fast EMA falls back below slow): a cross down on the last bar
    df = make_df([100.0] * 9 + [105.0, 90.0])
    assert len(df) == strategy.min_lookback

    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry_price == 90.0
    assert signal.stop_loss > signal.entry_price
    assert signal.take_profit is None
    assert "crossed below" in signal.reason


def test_no_signal_once_trend_is_established():
    strategy = build_strategy()
    # steady uptrend for long enough that fast/slow have long since settled
    # into fast-above-slow with no *new* cross on the final bar
    df = make_df([100 + i for i in range(30)])
    assert strategy.generate_signal(df) is None


def test_trail_stop_only_ratchets_favorably():
    strategy = build_strategy()
    df = make_df([100.0] * 9 + [95.0, 110.0])  # close=110, so ATR-implied stop < 110

    # long: a stop far below the ATR-implied level should ratchet up toward it
    stop_far_below = 50.0
    ratcheted = strategy.trail_stop(df, "long", stop_far_below)
    assert ratcheted > stop_far_below

    # long: a stop already tighter than the ATR-implied level should not loosen
    stop_already_tight = 109.9
    assert strategy.trail_stop(df, "long", stop_already_tight) == stop_already_tight

    # short: mirrors the long case — ratchets down, never loosens (moves up)
    stop_far_above = 500.0
    ratcheted_short = strategy.trail_stop(df, "short", stop_far_above)
    assert ratcheted_short < stop_far_above

    stop_already_tight_short = 90.1
    assert strategy.trail_stop(df, "short", stop_already_tight_short) == stop_already_tight_short
