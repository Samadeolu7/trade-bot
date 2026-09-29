import numpy as np
import pandas as pd
import pytest

from bot.strategy.crt import CandleRangeTheoryStrategy


def make_df(bars):
    """bars: list of (open, high, low, close)."""
    index = pd.date_range("2024-01-01", periods=len(bars), freq="D", tz="UTC")
    return pd.DataFrame(
        [{"open": o, "high": h, "low": l, "close": c, "volume": 1.0} for o, h, l, c in bars],
        index=index,
    )


RANGE = (100.0, 110.0, 90.0, 105.0)  # range candle: high 110, low 90


def test_bullish_sweep_of_low_closing_back_inside_goes_long():
    df = make_df([RANGE, (95.0, 100.0, 85.0, 95.0)])  # wicked to 85, closed 95 inside
    signal = CandleRangeTheoryStrategy({}).generate_signal(df)
    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == 95.0
    assert signal.stop_loss == 85.0
    assert signal.take_profit == 110.0  # opposite end of the range candle
    assert signal.context["reward_risk"] == 1.5


def test_bearish_sweep_of_high_closing_back_inside_goes_short():
    df = make_df([RANGE, (105.0, 115.0, 100.0, 108.0)])  # risk 7 to 115, reward 18 to 90
    signal = CandleRangeTheoryStrategy({}).generate_signal(df)
    assert signal is not None
    assert signal.direction == "short"
    assert signal.stop_loss == 115.0
    assert signal.take_profit == 90.0


def test_close_beyond_range_is_a_breakout_not_crt():
    df = make_df([RANGE, (95.0, 100.0, 85.0, 88.0)])  # swept low and closed below it
    assert CandleRangeTheoryStrategy({}).generate_signal(df) is None


def test_outside_bar_sweeping_both_sides_is_skipped():
    df = make_df([RANGE, (100.0, 115.0, 85.0, 100.0)])
    assert CandleRangeTheoryStrategy({}).generate_signal(df) is None


def test_no_sweep_no_signal():
    df = make_df([RANGE, (100.0, 108.0, 92.0, 100.0)])
    assert CandleRangeTheoryStrategy({}).generate_signal(df) is None


def test_min_rr_filters_setups_with_little_room_to_target():
    # closed at 108: reward to 110 is 2, risk to 85 is 23
    df = make_df([RANGE, (95.0, 109.0, 85.0, 108.0)])
    assert CandleRangeTheoryStrategy({"min_rr": 1.0}).generate_signal(df) is None
    assert CandleRangeTheoryStrategy({"min_rr": 0.0}).generate_signal(df) is not None


def test_rr_target_mode_uses_fixed_multiple_of_risk():
    df = make_df([RANGE, (95.0, 100.0, 85.0, 95.0)])
    signal = CandleRangeTheoryStrategy({"target": "rr", "rr_multiple": 2.0}).generate_signal(df)
    assert signal.take_profit == 115.0  # 95 + 2 * (95 - 85)


def test_stop_buffer_moves_stop_past_the_wick():
    df = make_df([RANGE, (95.0, 100.0, 85.0, 95.0)])
    signal = CandleRangeTheoryStrategy({"stop_buffer_pct": 0.01}).generate_signal(df)
    assert signal.stop_loss == pytest.approx(84.15)


def test_trend_filter_blocks_counter_trend_longs():
    # long downtrend, then a bullish CRT below the EMA
    bars = [(200.0 - i, 201.0 - i, 199.0 - i, 200.0 - i) for i in range(60)]
    last = bars[-1]
    bars.append((last[3], last[3] + 0.5, last[2] - 2.0, last[3] - 0.5))
    df = make_df(bars)
    assert CandleRangeTheoryStrategy({"min_rr": 0.0}).generate_signal(df) is not None
    assert CandleRangeTheoryStrategy({"min_rr": 0.0, "trend_ema_period": 20}).generate_signal(df) is None


def test_invalid_target_mode_rejected():
    with pytest.raises(ValueError):
        CandleRangeTheoryStrategy({"target": "moon"})


def test_entry_signals_match_generate_signal_bar_by_bar():
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 2, 300))
    high = close + rng.uniform(0.5, 4, 300)
    low = close - rng.uniform(0.5, 4, 300)
    open_ = close + rng.normal(0, 1, 300)
    df = make_df(list(zip(open_, high, low, close)))

    strategy = CandleRangeTheoryStrategy({"trend_ema_period": 10})
    vectorized = strategy.entry_signals(df)
    fired = 0
    for i in range(strategy.min_lookback, len(df)):
        # full history up to i, so the EMA matches the vectorized run exactly
        signal = strategy.generate_signal(df.iloc[: i + 1])
        row = vectorized.iloc[i]
        if signal is None:
            assert pd.isna(row["direction"])
        else:
            fired += 1
            assert row["direction"] == signal.direction
            assert row["stop_loss"] == pytest.approx(signal.stop_loss)
            assert row["take_profit"] == pytest.approx(signal.take_profit)
    assert fired > 0  # the random walk should produce at least some setups


def test_diagnose_reports_next_range_and_last_bar_verdict():
    strategy = CandleRangeTheoryStrategy({})
    breakout = strategy.diagnose(make_df([RANGE, (95.0, 100.0, 85.0, 88.0)]))
    assert breakout["ready"] is True
    assert breakout["next_range_high"] == 100.0
    assert breakout["next_range_low"] == 85.0
    assert "breakout" in breakout["last_bar"]
    assert breakout["near_miss"] is False
    assert strategy.diagnose(make_df([RANGE, (95.0, 100.0, 85.0, 95.0)]))["last_bar"] == "CRT setup"
