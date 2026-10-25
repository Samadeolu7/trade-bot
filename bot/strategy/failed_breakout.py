import numpy as np
import pandas as pd

from bot.indicators.indicators import atr
from bot.strategy.base import Direction, Signal, Strategy
from bot.strategy.failed_breakout_signal import (
    min_lookback_for,
    scan_bearish_failed_breakouts,
    scan_bullish_failed_breakdowns,
)


class FailedBreakoutStrategy(Strategy):
    """Hypothesis A (family `reversal/failed-breakout`, pre-registered
    2026-10-25, notes/research_log.md): the 4-step failed-breakout reversal
    — location, break, reclaim, follow-through — traded as its own
    standalone strategy. See failed_breakout_signal.py for the exact event
    definitions; this class only turns a confirmed follow-through into a
    Signal.

    Entry at the follow-through bar's close. Stop just beyond the sweep
    wick (`stop_buffer_pct` of price past it) — the level never held, so a
    position taken against the reversal should exit the moment price
    revisits where the failed breakout actually printed. No fixed target;
    exits ride an ATR trailing stop, the same mechanism as
    market_structure/donchian, so this isn't a different strategy family
    just because of its exit style.

    Untested against real data — this is a new research candidate, not a
    validated strategy."""

    name = "failed_breakout"

    def __init__(self, params: dict):
        super().__init__(params)
        self.swing_window = params.get("swing_window", 5)
        self.reclaim_window = params.get("reclaim_window", 5)
        self.follow_through_window = params.get("follow_through_window", 10)
        self.stop_buffer_pct = params.get("stop_buffer_pct", 0.0)
        self.atr_period = params.get("atr_period", 14)
        self.atr_mult = params.get("atr_mult", 2.0)
        self.min_lookback = min_lookback_for(
            self.swing_window, self.reclaim_window, self.follow_through_window, extra=self.atr_period * 3
        )

    def entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        bear = scan_bearish_failed_breakouts(df, self.swing_window, self.reclaim_window, self.follow_through_window)
        bull = scan_bullish_failed_breakdowns(df, self.swing_window, self.reclaim_window, self.follow_through_window)
        close = df["close"]

        short_stop = bear["sweep_extreme"] * (1 + self.stop_buffer_pct)
        long_stop = bull["sweep_extreme"] * (1 - self.stop_buffer_pct)
        direction = np.where(bear["follow_through"], "short", np.where(bull["follow_through"], "long", None))
        stop_loss = np.where(bear["follow_through"], short_stop, np.where(bull["follow_through"], long_stop, np.nan))
        reason = np.where(
            bear["follow_through"],
            "failed breakout: resistance swept, reclaimed, structure broken down",
            np.where(
                bull["follow_through"],
                "failed breakdown: support swept, reclaimed, structure broken up",
                None,
            ),
        )
        return pd.DataFrame(
            {
                "direction": direction,
                "entry_price": np.where(direction != None, close, np.nan),  # noqa: E711
                "stop_loss": stop_loss,
                "take_profit": np.nan,
                "reason": reason,
            },
            index=df.index,
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_lookback:
            return None
        last = self.entry_signals(df).iloc[-1]
        if last["direction"] is None:
            return None
        return Signal(
            symbol="",
            timeframe="",
            direction=last["direction"],
            entry_price=float(last["entry_price"]),
            stop_loss=float(last["stop_loss"]),
            take_profit=None,
            reason=last["reason"],
            timestamp=df.index[-1],
        )

    def trail_stop(self, df: pd.DataFrame, direction: Direction, current_stop: float) -> float:
        atr_val = atr(df["high"], df["low"], df["close"], self.atr_period)
        last_atr = atr_val.iloc[-1]
        if pd.isna(last_atr):
            return current_stop
        last_close = df["close"].iloc[-1]
        if direction == "long":
            return max(current_stop, last_close - self.atr_mult * last_atr)
        return min(current_stop, last_close + self.atr_mult * last_atr)
