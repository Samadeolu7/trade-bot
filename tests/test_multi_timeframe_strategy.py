import pandas as pd
import pytest

from bot.strategy.multi_timeframe import MultiTimeframeTrendPullbackStrategy


def make_df(closes, wick=0.3, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D", tz="UTC")
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {"open": close, "high": close + wick, "low": close - wick, "close": close, "volume": 1.0}
    )


def build_strategy():
    return MultiTimeframeTrendPullbackStrategy(
        {
            "htf_rule": "W",
            "htf_sma_period": 3,
            "approx_htf_period_bars": 7,
            "pullback_ema_period": 3,
            "pullback_lookback": 2,
            "atr_period": 2,
            "atr_mult": 1.0,
        }
    )


# steady uptrend for 50 days (so the weekly-resampled close sits comfortably
# above its own 3-week SMA), then a 3-day pullback, then a reclaim
LONG_CLOSES = [100 + i * 0.5 for i in range(50)] + [123, 121, 119] + [126]

# mirror: steady downtrend, then a bounce (pullback within the downtrend),
# then a reclaim back down
SHORT_CLOSES = [150 - i * 0.5 for i in range(50)] + [128, 130, 132] + [124]


def test_min_lookback_formula():
    strategy = build_strategy()
    assert strategy.min_lookback == (3 + 3) * 7 + max(3, 2) * 3  # == 51


def test_no_signal_when_insufficient_data():
    strategy = build_strategy()
    df = make_df(LONG_CLOSES[:40])
    assert strategy.generate_signal(df) is None


def test_long_signal_on_htf_uptrend_with_daily_pullback_reclaimed():
    strategy = build_strategy()
    df = make_df(LONG_CLOSES)

    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == 126.0
    assert signal.stop_loss == pytest.approx(121.325)
    assert signal.take_profit is None
    assert "uptrend" in signal.reason and "reclaimed" in signal.reason


def test_short_signal_on_htf_downtrend_with_daily_pullback_reclaimed():
    strategy = build_strategy()
    df = make_df(SHORT_CLOSES)

    signal = strategy.generate_signal(df)

    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry_price == 124.0
    assert signal.stop_loss == pytest.approx(129.2375)
    assert "downtrend" in signal.reason and "reclaimed" in signal.reason


def test_no_signal_without_a_pullback():
    # pure monotonic rise -> higher-timeframe trend is bullish, but there's
    # never a dip below the daily EMA to reclaim, so no entry trigger fires
    strategy = build_strategy()
    closes = [100 + i * 0.5 for i in range(54)]
    df = make_df(closes)
    assert strategy.generate_signal(df) is None


def test_htf_trend_uses_only_completed_bars():
    strategy = build_strategy()
    df = make_df(LONG_CLOSES)
    bullish, bearish = strategy._htf_trend(df)
    assert bullish is True
    assert bearish is False


def test_trail_stop_only_ratchets_favorably():
    strategy = build_strategy()
    df = make_df(LONG_CLOSES)

    ratcheted = strategy.trail_stop(df, "long", current_stop=50.0)
    assert ratcheted > 50.0

    stop_already_tight = 125.9
    assert strategy.trail_stop(df, "long", stop_already_tight) == stop_already_tight
