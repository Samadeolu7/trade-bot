import pandas as pd

from bot.strategy.donchian import DonchianBreakoutStrategy


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


def build_strategy(channel_period=20):
    return DonchianBreakoutStrategy({"channel_period": channel_period})


def test_min_lookback_is_channel_period_plus_one():
    strategy = build_strategy(20)
    assert strategy.min_lookback == 21


def test_no_signal_when_below_min_lookback():
    strategy = build_strategy(20)
    df = make_df([100.0] * 10)
    assert strategy.generate_signal(df) is None


def test_long_signal_on_breakout_above_prior_high_channel():
    strategy = build_strategy(20)
    # 20 flat bars (channel: high=100.5, low=99.5), then a close breaking above
    df = make_df([100.0] * 20 + [110.0])
    assert len(df) == strategy.min_lookback

    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == 110.0
    assert signal.stop_loss == 99.5  # the opposite (lower) channel boundary
    assert signal.take_profit is None
    assert "broke above" in signal.reason


def test_short_signal_on_breakout_below_prior_low_channel():
    strategy = build_strategy(20)
    df = make_df([100.0] * 20 + [90.0])

    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry_price == 90.0
    assert signal.stop_loss == 100.5  # the opposite (upper) channel boundary
    assert "broke below" in signal.reason


def test_no_signal_when_close_stays_within_channel():
    strategy = build_strategy(20)
    df = make_df([100.0] * 21)  # never breaks its own flat channel
    assert strategy.generate_signal(df) is None


def test_entry_signals_matches_generate_signal():
    strategy = build_strategy(20)
    df = make_df([100.0] * 20 + [110.0])

    signal = strategy.generate_signal(df)
    row = strategy.entry_signals(df).iloc[-1]

    assert row["direction"] == signal.direction == "long"
    assert row["entry_price"] == signal.entry_price
    assert row["stop_loss"] == signal.stop_loss
    assert row["reason"] == signal.reason


def test_trail_stop_ratchets_toward_opposite_channel_only_favorably():
    strategy = build_strategy(20)
    # channel rises after entry: bars 1-19 flat at 100, bar 20 breaks out to 110,
    # bars 21-39 stay flat at 105 so the rolling low channel lifts toward 104.5
    df = make_df([100.0] * 20 + [110.0] + [105.0] * 19)

    # long position: a stop far below the current low-channel should ratchet up
    ratcheted = strategy.trail_stop(df, "long", current_stop=50.0)
    assert ratcheted > 50.0

    # a stop already above the current low-channel should never loosen
    tight_stop = 106.0
    assert strategy.trail_stop(df, "long", tight_stop) == tight_stop

    # short position mirrors this in the opposite direction
    ratcheted_short = strategy.trail_stop(df, "short", current_stop=500.0)
    assert ratcheted_short < 500.0
