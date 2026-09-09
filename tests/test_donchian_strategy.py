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


def test_exit_channel_period_defaults_to_channel_period():
    strategy = DonchianBreakoutStrategy({"channel_period": 20})
    assert strategy.exit_channel_period == 20


def test_min_lookback_uses_wider_of_entry_and_exit_periods():
    strategy = DonchianBreakoutStrategy({"channel_period": 10, "exit_channel_period": 30})
    assert strategy.min_lookback == 31


def test_stop_loss_uses_wider_exit_channel_when_configured():
    strategy = DonchianBreakoutStrategy({"channel_period": 5, "exit_channel_period": 15})
    assert strategy.min_lookback == 16

    # an early dip (bar 0) sets a much lower low than anything in the most
    # recent 5 bars, so the 15-bar exit channel's low differs from the
    # 5-bar entry channel's low
    df = make_df([80.0] + [100.0] * 14 + [110.0])  # 16 bars

    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.direction == "long"
    # the 5-bar entry channel's low (all-100 bars) would give 99.5, but the
    # wider 15-bar exit channel reaches back to the dip at 79.5
    assert signal.stop_loss == 79.5


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


def test_min_lookback_with_atr_exit_accounts_for_atr_warmup():
    strategy = DonchianBreakoutStrategy(
        {"channel_period": 5, "exit_method": "atr", "atr_period": 14}
    )
    assert strategy.min_lookback == max(5, 14 * 3) + 1  # == 43


def test_atr_exit_stop_differs_from_channel_exit_stop():
    channel_strategy = DonchianBreakoutStrategy({"channel_period": 20, "exit_method": "channel"})
    atr_strategy = DonchianBreakoutStrategy(
        {"channel_period": 20, "exit_method": "atr", "atr_period": 2, "atr_mult": 2.0}
    )
    df = make_df([100.0] * 20 + [110.0])
    assert len(df) >= atr_strategy.min_lookback

    channel_signal = channel_strategy.generate_signal(df)
    atr_signal = atr_strategy.generate_signal(df)

    assert channel_signal is not None and atr_signal is not None
    assert channel_signal.direction == atr_signal.direction == "long"
    # same entry, different stop-placement mechanism -> different stop
    assert atr_signal.stop_loss != channel_signal.stop_loss


def test_atr_exit_trail_stop_only_ratchets_favorably():
    strategy = DonchianBreakoutStrategy(
        {"channel_period": 20, "exit_method": "atr", "atr_period": 2, "atr_mult": 1.0}
    )
    df = make_df([100.0] * 20 + [110.0])

    ratcheted = strategy.trail_stop(df, "long", current_stop=50.0)
    assert ratcheted > 50.0

    stop_already_tight = 109.9
    assert strategy.trail_stop(df, "long", stop_already_tight) == stop_already_tight

    ratcheted_short = strategy.trail_stop(df, "short", current_stop=500.0)
    assert ratcheted_short < 500.0


def test_diagnose_not_ready_below_min_lookback():
    strategy = build_strategy(20)
    df = make_df([100.0] * 10)
    assert strategy.diagnose(df) == {"ready": False}


def test_diagnose_near_miss_when_close_to_upper_channel():
    strategy = build_strategy(20)
    # channel: high=100.5 (entry_upper), close within 1% of it without breaking
    df = make_df([100.0] * 20 + [100.3])

    diagnosis = strategy.diagnose(df)

    assert strategy.generate_signal(df) is None  # confirms it's a near miss, not an actual signal
    assert diagnosis["ready"] is True
    assert diagnosis["near_miss"] is True
    assert diagnosis["near_miss_key"] == "near_upper_channel"
    assert diagnosis["dist_to_upper_pct"] < DonchianBreakoutStrategy.NEAR_MISS_PCT


def test_diagnose_no_near_miss_when_far_from_either_channel():
    strategy = build_strategy(20)
    # a wide historical range (channel spans roughly 90-110) with the last
    # close sitting comfortably mid-channel, well outside the 1% near-miss band
    df = make_df([90.0, 110.0] + [100.0] * 18 + [100.0])

    diagnosis = strategy.diagnose(df)

    assert diagnosis["near_miss"] is False
    assert diagnosis["near_miss_key"] is None


def test_diagnose_no_near_miss_when_signal_actually_fires():
    strategy = build_strategy(20)
    df = make_df([100.0] * 20 + [110.0])  # a clean breakout, not a near miss

    diagnosis = strategy.diagnose(df)

    assert strategy.generate_signal(df) is not None
    assert diagnosis["near_miss"] is False


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
