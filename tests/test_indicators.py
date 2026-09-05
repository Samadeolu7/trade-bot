import numpy as np
import pandas as pd

from bot.indicators.indicators import adx, atr, bollinger_bands, ema, rsi


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


def test_ema_matches_pandas_ewm():
    close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0])
    result = ema(close, period=3)
    expected = close.ewm(span=3, adjust=False, min_periods=3).mean()
    pd.testing.assert_series_equal(result, expected)


def test_ema_nan_before_min_periods():
    close = pd.Series([1.0, 2.0, 3.0, 4.0])
    result = ema(close, period=3)
    assert result.iloc[:2].isna().all()
    assert not pd.isna(result.iloc[2])


def test_rsi_all_gains_is_100():
    close = pd.Series(range(1, 30), dtype=float)  # strictly increasing
    result = rsi(close, period=14)
    assert result.iloc[-1] == 100


def test_rsi_flat_series_is_neutral_not_overbought():
    close = pd.Series([100.0] * 30)  # zero gains and zero losses throughout
    result = rsi(close, period=14)
    assert result.iloc[-1] == 50


def test_rsi_bounded_0_100():
    rng = np.random.default_rng(42)
    close = pd.Series(100 + rng.normal(0, 1, 200).cumsum())
    result = rsi(close, period=14).dropna()
    assert (result >= 0).all() and (result <= 100).all()


def test_bollinger_band_ordering():
    rng = np.random.default_rng(1)
    close = pd.Series(100 + rng.normal(0, 1, 100).cumsum())
    bands = bollinger_bands(close, period=20, num_std=2.0).dropna()
    assert (bands["bb_upper"] >= bands["bb_mid"]).all()
    assert (bands["bb_mid"] >= bands["bb_lower"]).all()


def test_atr_nonnegative():
    df = make_df([100, 101, 99, 102, 98, 103, 97, 104, 96, 105, 95, 106, 94, 107, 93])
    result = atr(df["high"], df["low"], df["close"], period=5).dropna()
    assert (result >= 0).all()


def test_adx_higher_for_strong_trend_than_choppy_range():
    trend_closes = [100 + i for i in range(60)]  # steady uptrend
    # oscillates with no net drift -> up/down moves roughly cancel out
    choppy_closes = [100 + 5 * np.sin(i * 0.5) for i in range(60)]

    trend_df = make_df(trend_closes)
    choppy_df = make_df(choppy_closes)

    trend_adx = adx(trend_df["high"], trend_df["low"], trend_df["close"], period=14)["adx"].iloc[-1]
    choppy_adx = adx(choppy_df["high"], choppy_df["low"], choppy_df["close"], period=14)["adx"].iloc[-1]

    assert trend_adx > choppy_adx
