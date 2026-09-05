import pandas as pd

from bot.indicators.indicators import atr, ema
from bot.strategy.base import Direction, Signal, Strategy


class EmaCrossStrategy(Strategy):
    """Trend-following: long/short on a fast/slow EMA cross, exit via an
    ATR trailing stop rather than a fixed target (spec Section 7a). Intended
    to run only while RegimeFilter reports "trending"."""

    name = "ema_cross"

    def __init__(self, params: dict):
        super().__init__(params)
        self.fast_period = params.get("fast_period", 20)
        self.slow_period = params.get("slow_period", 50)
        self.atr_period = params.get("atr_period", 14)
        self.atr_mult = params.get("atr_mult", 2.0)
        # slow EMA + ATR both need warm-up; +2 so the crossover check
        # (comparing the last two bars) has a valid prior bar too.
        self.min_lookback = max(self.slow_period, self.atr_period) * 3 + 2

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_lookback:
            return None

        fast = ema(df["close"], self.fast_period)
        slow = ema(df["close"], self.slow_period)
        atr_val = atr(df["high"], df["low"], df["close"], self.atr_period)

        if pd.isna(fast.iloc[-2]) or pd.isna(slow.iloc[-2]) or pd.isna(atr_val.iloc[-1]):
            return None

        crossed_up = fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]
        crossed_down = fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]

        entry = df["close"].iloc[-1]
        last_atr = atr_val.iloc[-1]
        timestamp = df.index[-1]

        if crossed_up:
            return Signal(
                symbol="",
                timeframe="",
                direction="long",
                entry_price=entry,
                stop_loss=entry - self.atr_mult * last_atr,
                take_profit=None,
                reason=f"EMA{self.fast_period} crossed above EMA{self.slow_period}",
                timestamp=timestamp,
            )
        if crossed_down:
            return Signal(
                symbol="",
                timeframe="",
                direction="short",
                entry_price=entry,
                stop_loss=entry + self.atr_mult * last_atr,
                take_profit=None,
                reason=f"EMA{self.fast_period} crossed below EMA{self.slow_period}",
                timestamp=timestamp,
            )
        return None

    def trail_stop(self, df: pd.DataFrame, direction: Direction, current_stop: float) -> float:
        atr_val = atr(df["high"], df["low"], df["close"], self.atr_period)
        last_atr = atr_val.iloc[-1]
        if pd.isna(last_atr):
            return current_stop
        last_close = df["close"].iloc[-1]
        if direction == "long":
            return max(current_stop, last_close - self.atr_mult * last_atr)
        return min(current_stop, last_close + self.atr_mult * last_atr)
