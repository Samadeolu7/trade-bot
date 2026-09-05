import numpy as np
import pandas as pd

from bot.strategy.regime import RegimeFilter, Sma200RegimeFilter


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


def test_regime_none_when_insufficient_data():
    rf = RegimeFilter(adx_period=14, adx_threshold=25)
    df = make_df([100.0] * 10)  # far fewer than min_lookback (42)
    assert rf.regime(df) is None


def test_regime_trending_on_strong_uptrend():
    rf = RegimeFilter(adx_period=14, adx_threshold=25)
    df = make_df([100 + i for i in range(60)])
    assert rf.regime(df) == "trending"


def test_regime_ranging_on_choppy_series():
    rf = RegimeFilter(adx_period=14, adx_threshold=25)
    df = make_df([100 + 5 * np.sin(i * 0.5) for i in range(60)])
    assert rf.regime(df) == "ranging"


def test_regime_series_last_value_matches_regime():
    rf = RegimeFilter(adx_period=14, adx_threshold=25)
    df = make_df([100 + i for i in range(60)])
    assert rf.regime_series(df).iloc[-1] == rf.regime(df) == "trending"


def test_regime_series_none_at_start_before_adx_warms_up():
    rf = RegimeFilter(adx_period=14, adx_threshold=25)
    df = make_df([100 + i for i in range(60)])
    series = rf.regime_series(df)
    assert pd.isna(series.iloc[0])
    assert pd.notna(series.iloc[-1])


def build_sma_filter():
    # small periods so tests stay lightweight, same convention as elsewhere
    return Sma200RegimeFilter(sma_period=10, band_pct=0.02, adx_period=5, adx_threshold=15)


def test_sma_filter_min_lookback_is_max_of_sma_and_adx_warmup():
    rf = build_sma_filter()
    assert rf.min_lookback == max(10, 5 * 3)  # == 15


def test_sma_filter_none_when_insufficient_data():
    rf = build_sma_filter()
    df = make_df([100.0] * 5)
    assert rf.regime(df) is None


def test_sma_filter_trending_on_strong_uptrend():
    rf = build_sma_filter()
    df = make_df([100 + i for i in range(40)])
    assert rf.regime(df) == "trending"


def test_sma_filter_ranging_on_choppy_series_near_its_average():
    # a wider band than build_sma_filter()'s, since a small-amplitude sine
    # wobble can still momentarily drift a couple % from its own lagging SMA
    rf = Sma200RegimeFilter(sma_period=10, band_pct=0.10, adx_period=5, adx_threshold=15)
    df = make_df([100 + 3 * np.sin(i * 0.5) for i in range(40)])
    assert rf.regime(df) == "ranging"


def test_sma_filter_ranging_when_price_hugs_average_even_if_adx_would_confirm():
    # price barely off its SMA (well inside band_pct) should never read as
    # "trending" no matter what ADX says — SMA-vs-price is the primary call
    rf = Sma200RegimeFilter(sma_period=10, band_pct=0.50, adx_period=5, adx_threshold=15)
    df = make_df([100 + i for i in range(40)])  # would be "trending" with a tight band
    assert rf.regime(df) == "ranging"
