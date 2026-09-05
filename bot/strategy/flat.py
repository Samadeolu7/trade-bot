import pandas as pd

from bot.strategy.base import Signal, Strategy


class FlatStrategy(Strategy):
    """Always flat — never generates a signal. The default "ranging"
    sub-strategy for regime_switched (spec Section 8 pilot findings): an
    initial sweep found rsi_bb produced too few trades (2-5 over a 4-year
    window) to distinguish a real edge from noise, and it did not beat
    donchian run alone on the identical window. Until a mean-reversion rule
    demonstrates an edge on real data, the defensible default is to simply
    not trade while "ranging" rather than hand control to an unvalidated
    rule — rsi_bb remains available as a config-driven choice
    (strategy.regime_switched.ranging_strategy) for future experimentation."""

    name = "flat"

    def __init__(self, params: dict | None = None):
        super().__init__(params or {})
        self.min_lookback = 1

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        return None

    def entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        n = len(df)
        return pd.DataFrame(
            {
                "direction": [None] * n,
                "entry_price": [float("nan")] * n,
                "stop_loss": [float("nan")] * n,
                "take_profit": [float("nan")] * n,
                "reason": [None] * n,
            },
            index=df.index,
        )
