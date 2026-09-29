import pandas as pd

from bot.strategy.base import Direction, Signal, Strategy


class LongOnlyStrategy(Strategy):
    """Wraps any signal strategy and drops its short entries. Quidax is
    spot-only (spec Section 2), so a strategy that was backtested long and
    short hasn't actually been tested for the venue it would trade on until
    its shorts are removed. Enabled per strategy with `long_only: true` in
    that strategy's config section (e.g. `--param donchian.long_only=1`),
    so research reports and backtests can compare both versions.

    Longs pass through unchanged, including the base strategy's trailing
    stop and diagnosis. A dropped short leaves the bar flat rather than
    being replaced with anything else."""

    def __init__(self, base: Strategy):
        super().__init__({})
        self.base = base
        self.name = base.name
        self.min_lookback = base.min_lookback

    def before_poll(self) -> None:
        self.base.before_poll()

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        signal = self.base.generate_signal(df)
        if signal is None or signal.direction == "short":
            return None
        return signal

    def entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = self.base.entry_signals(df).copy()
        result.loc[result["direction"] == "short", "direction"] = None
        return result

    def trail_stop(self, df: pd.DataFrame, direction: Direction, current_stop: float) -> float:
        return self.base.trail_stop(df, direction, current_stop)

    def diagnose(self, df: pd.DataFrame) -> dict:
        return {**self.base.diagnose(df), "long_only": True}
