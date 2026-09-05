import numpy as np
import pandas as pd

from bot.strategy.base import Direction, Signal, Strategy


class DonchianBreakoutStrategy(Strategy):
    """Trend-following breakout (spec Section 7e): long when price closes
    above the highest high of the prior N bars, short when it closes below
    the lowest low of the prior N bars.

    Exit is the *opposite* channel boundary, managed as a trailing stop (the
    same mechanism as ema_cross's ATR stop) rather than a separate close-only
    check — a resting stop order at the channel level is how this would
    actually be placed live, and it reuses the engine's existing intrabar
    stop/target handling instead of a second exit code path."""

    name = "donchian"

    def __init__(self, params: dict):
        super().__init__(params)
        self.channel_period = params.get("channel_period", 20)
        # entry compares close against the PRIOR N bars (shifted), so one
        # extra bar beyond the rolling window itself is needed for that
        # shifted value to be defined at the last row.
        self.min_lookback = self.channel_period + 1

    def _channels(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        upper = df["high"].rolling(self.channel_period).max().shift(1)
        lower = df["low"].rolling(self.channel_period).min().shift(1)
        return upper, lower

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_lookback:
            return None

        upper, lower = self._channels(df)
        last_upper, last_lower = upper.iloc[-1], lower.iloc[-1]
        if pd.isna(last_upper) or pd.isna(last_lower):
            return None

        entry = df["close"].iloc[-1]
        timestamp = df.index[-1]

        if entry > last_upper:
            return Signal(
                symbol="",
                timeframe="",
                direction="long",
                entry_price=entry,
                stop_loss=last_lower,
                take_profit=None,
                reason=f"close broke above {self.channel_period}-bar high channel",
                timestamp=timestamp,
            )
        if entry < last_lower:
            return Signal(
                symbol="",
                timeframe="",
                direction="short",
                entry_price=entry,
                stop_loss=last_upper,
                take_profit=None,
                reason=f"close broke below {self.channel_period}-bar low channel",
                timestamp=timestamp,
            )
        return None

    def entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        upper, lower = self._channels(df)
        close = df["close"]

        breakout_up = close > upper
        breakout_down = close < lower

        direction = np.where(breakout_up, "long", np.where(breakout_down, "short", None))
        stop_loss = np.where(breakout_up, lower, np.where(breakout_down, upper, np.nan))
        reason = np.where(
            breakout_up,
            f"close broke above {self.channel_period}-bar high channel",
            np.where(
                breakout_down,
                f"close broke below {self.channel_period}-bar low channel",
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

    def trail_stop(self, df: pd.DataFrame, direction: Direction, current_stop: float) -> float:
        upper, lower = self._channels(df)
        if direction == "long":
            candidate = lower.iloc[-1]
            if pd.isna(candidate):
                return current_stop
            return max(current_stop, candidate)
        candidate = upper.iloc[-1]
        if pd.isna(candidate):
            return current_stop
        return min(current_stop, candidate)
