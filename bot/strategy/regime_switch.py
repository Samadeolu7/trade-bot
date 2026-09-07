from dataclasses import replace

import numpy as np
import pandas as pd

from bot.strategy.base import Direction, Signal, Strategy
from bot.strategy.regime import RegimeFilter


class RegimeSwitchedStrategy(Strategy):
    """Composite strategy (spec Section 6): delegates to a trend-following
    strategy while RegimeFilter reports "trending", and a mean-reversion
    strategy while it reports "ranging". Tracks which sub-strategy opened the
    current position so trailing-stop updates are delegated correctly even
    after the regime has since flipped."""

    name = "regime_switched"

    def __init__(self, trending: Strategy, ranging: Strategy, regime_filter: RegimeFilter):
        super().__init__({})
        self.trending = trending
        self.ranging = ranging
        self.regime_filter = regime_filter
        self.min_lookback = max(
            trending.min_lookback, ranging.min_lookback, regime_filter.min_lookback
        )
        self._active: Strategy | None = None

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        regime = self.regime_filter.regime(df)
        if regime is None:
            return None
        sub = self.trending if regime == "trending" else self.ranging
        signal = sub.generate_signal(df)
        if signal is not None:
            self._active = sub
            # so it's discoverable later which regime/filter/sub-strategy
            # combination actually produced this entry, not just that
            # "regime_switched" (a moving config) did
            signal = replace(
                signal,
                context={
                    **signal.context,
                    "regime": regime,
                    "regime_filter": type(self.regime_filter).__name__,
                    "sub_strategy": sub.name,
                },
            )
        return signal

    def trail_stop(self, df: pd.DataFrame, direction: Direction, current_stop: float) -> float:
        if self._active is None:
            return current_stop
        return self._active.trail_stop(df, direction, current_stop)

    def entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Vectorized: regime, and both sub-strategies' entry signals, are
        each computed once over the full df, then combined per bar. Adds a
        `strategy` column naming which sub-strategy owns each signal row, so
        the backtest engine can delegate that position's trailing-stop calls
        to the correct sub-strategy even after the regime later flips."""
        regimes = self.regime_filter.regime_series(df)
        trending_sig = self.trending.entry_signals(df)
        ranging_sig = self.ranging.entry_signals(df)

        is_trending = (regimes == "trending").to_numpy()
        combined = pd.DataFrame(index=df.index)
        for col in ["direction", "entry_price", "stop_loss", "take_profit", "reason"]:
            combined[col] = np.where(is_trending, trending_sig[col], ranging_sig[col])
        combined["strategy"] = np.where(is_trending, self.trending, self.ranging)
        combined.loc[regimes.isna(), ["direction", "strategy"]] = None
        return combined
