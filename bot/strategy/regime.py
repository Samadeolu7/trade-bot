from typing import Literal

import numpy as np
import pandas as pd

from bot.indicators.indicators import adx

Regime = Literal["trending", "ranging"]


class RegimeFilter:
    """ADX-based market regime detector (spec Section 6) — decides which
    sub-strategy is "live" at any time: trend-following above the threshold,
    mean-reversion below it."""

    def __init__(self, adx_period: int = 14, adx_threshold: float = 25.0):
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        # Wilder smoothing needs several periods to warm up before the value
        # is stable, not just the first non-NaN row.
        self.min_lookback = adx_period * 3

    def regime(self, df: pd.DataFrame) -> Regime | None:
        if len(df) < self.min_lookback:
            return None
        adx_df = adx(df["high"], df["low"], df["close"], period=self.adx_period)
        latest_adx = adx_df["adx"].iloc[-1]
        if pd.isna(latest_adx):
            return None
        return "trending" if latest_adx >= self.adx_threshold else "ranging"

    def regime_series(self, df: pd.DataFrame) -> pd.Series:
        """Vectorized equivalent of `regime`: ADX computed once over the
        whole df, giving a regime label per bar (None where ADX hasn't
        warmed up yet)."""
        adx_val = adx(df["high"], df["low"], df["close"], period=self.adx_period)["adx"]
        labels = np.where(adx_val >= self.adx_threshold, "trending", "ranging")
        return pd.Series(np.where(adx_val.isna(), None, labels), index=df.index)


class Sma200RegimeFilter:
    """Price-vs-SMA(200) regime filter (spec Section 6): an independent
    reproducible backtest found this the strongest risk-adjusted *primary*
    trend/range switch, with ADX used only as secondary confirmation rather
    than the sole signal.

    "Trending" requires both: price meaningfully extended from its SMA(200)
    (more than `band_pct` away, in either direction — the primary call) *and*
    ADX confirming real trend strength (`adx_threshold` defaults lower than
    RegimeFilter's, since here it's a secondary check, not the primary one).
    Otherwise "ranging" — including whenever price is hugging the average,
    which price-vs-SMA(200) alone can't tell apart from a real trend."""

    def __init__(
        self,
        sma_period: int = 200,
        band_pct: float = 0.0,
        adx_period: int = 14,
        adx_threshold: float = 20.0,
    ):
        self.sma_period = sma_period
        self.band_pct = band_pct
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.min_lookback = max(sma_period, adx_period * 3)

    def regime(self, df: pd.DataFrame) -> Regime | None:
        series = self.regime_series(df)
        return series.iloc[-1] if len(series) else None

    def regime_series(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        sma = close.rolling(self.sma_period).mean()
        extended = (close - sma).abs() / sma > self.band_pct

        adx_val = adx(df["high"], df["low"], df["close"], period=self.adx_period)["adx"]
        confirmed = adx_val >= self.adx_threshold

        trending = extended & confirmed
        valid = sma.notna() & adx_val.notna()

        labels = np.where(trending, "trending", "ranging")
        return pd.Series(np.where(valid, labels, None), index=df.index)
