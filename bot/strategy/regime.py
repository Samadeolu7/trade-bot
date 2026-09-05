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
