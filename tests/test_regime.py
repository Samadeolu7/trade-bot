import numpy as np
import pandas as pd

from bot.strategy.regime import RegimeFilter


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
