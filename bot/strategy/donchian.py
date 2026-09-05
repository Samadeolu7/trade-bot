import numpy as np
import pandas as pd

from bot.indicators.indicators import atr
from bot.strategy.base import Direction, Signal, Strategy


class DonchianBreakoutStrategy(Strategy):
    """Trend-following breakout (spec Section 7e): long when price closes
    above the highest high of the prior N bars, short when it closes below
    the lowest low of the prior N bars.

    `exit_method` controls how the stop is placed and trailed once in a
    position:
    - "channel" (default): the opposite boundary of a *separate*, wider
      `exit_channel_period` channel (defaults to `channel_period` if unset).
      With a single shared period for entry and exit, a routine pullback
      within an ongoing trend is often enough to breach the same-width exit
      channel, closing the position on normal chop rather than a real
      reversal, then requiring a fresh breakout to re-enter — missing
      whatever continuation happens in between. A wider exit channel lags
      further behind price, tolerating deeper pullbacks before triggering,
      at the cost of giving back more on a genuine reversal.
    - "atr": an ATR-based trailing stop, the same mechanism ema_cross uses
      (spec 7a) — purpose-built to size the stop off actual recent
      volatility rather than a second lookback window, which may distinguish
      routine chop from a real reversal more cleanly than widening the
      channel does. `exit_channel_period` is unused in this mode.

    Either way, the exit is a trailing stop checked intrabar (the engine's
    existing stop/target handling) rather than a separate close-only check —
    a resting stop order at the exit level is how this would actually be
    placed live."""

    name = "donchian"

    def __init__(self, params: dict):
        super().__init__(params)
        self.channel_period = params.get("channel_period", 20)
        self.exit_method = params.get("exit_method", "channel")
        self.exit_channel_period = params.get("exit_channel_period", self.channel_period)
        self.atr_period = params.get("atr_period", 14)
        self.atr_mult = params.get("atr_mult", 2.0)

        if self.exit_method == "atr":
            exit_lookback = self.atr_period * 3
        else:
            exit_lookback = self.exit_channel_period
        # entry/exit channels compare against the PRIOR N bars (shifted), so
        # one extra bar beyond the wider rolling window is needed for that
        # shifted value to be defined at the last row.
        self.min_lookback = max(self.channel_period, exit_lookback) + 1

    def _entry_channels(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        upper = df["high"].rolling(self.channel_period).max().shift(1)
        lower = df["low"].rolling(self.channel_period).min().shift(1)
        return upper, lower

    def _exit_stop_series(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """Returns (long_stop, short_stop): the stop level a position in
        that direction would use at each bar, under whichever exit_method is
        configured."""
        if self.exit_method == "atr":
            atr_val = atr(df["high"], df["low"], df["close"], self.atr_period)
            close = df["close"]
            return close - self.atr_mult * atr_val, close + self.atr_mult * atr_val
        upper = df["high"].rolling(self.exit_channel_period).max().shift(1)
        lower = df["low"].rolling(self.exit_channel_period).min().shift(1)
        return lower, upper

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_lookback:
            return None

        entry_upper, entry_lower = self._entry_channels(df)
        long_stop, short_stop = self._exit_stop_series(df)
        last_entry_upper, last_entry_lower = entry_upper.iloc[-1], entry_lower.iloc[-1]
        last_long_stop, last_short_stop = long_stop.iloc[-1], short_stop.iloc[-1]
        if pd.isna(last_entry_upper) or pd.isna(last_entry_lower):
            return None
        if pd.isna(last_long_stop) or pd.isna(last_short_stop):
            return None

        entry = df["close"].iloc[-1]
        timestamp = df.index[-1]

        if entry > last_entry_upper:
            return Signal(
                symbol="",
                timeframe="",
                direction="long",
                entry_price=entry,
                stop_loss=last_long_stop,
                take_profit=None,
                reason=f"close broke above {self.channel_period}-bar high channel",
                timestamp=timestamp,
            )
        if entry < last_entry_lower:
            return Signal(
                symbol="",
                timeframe="",
                direction="short",
                entry_price=entry,
                stop_loss=last_short_stop,
                take_profit=None,
                reason=f"close broke below {self.channel_period}-bar low channel",
                timestamp=timestamp,
            )
        return None

    def entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        entry_upper, entry_lower = self._entry_channels(df)
        long_stop, short_stop = self._exit_stop_series(df)
        close = df["close"]

        breakout_up = close > entry_upper
        breakout_down = close < entry_lower

        direction = np.where(breakout_up, "long", np.where(breakout_down, "short", None))
        stop_loss = np.where(
            breakout_up, long_stop, np.where(breakout_down, short_stop, np.nan)
        )
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
        long_stop, short_stop = self._exit_stop_series(df)
        if direction == "long":
            candidate = long_stop.iloc[-1]
            if pd.isna(candidate):
                return current_stop
            return max(current_stop, candidate)
        candidate = short_stop.iloc[-1]
        if pd.isna(candidate):
            return current_stop
        return min(current_stop, candidate)
