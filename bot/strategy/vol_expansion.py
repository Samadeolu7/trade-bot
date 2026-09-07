import numpy as np
import pandas as pd

from bot.indicators.indicators import atr, bollinger_bands
from bot.strategy.base import Direction, Signal, Strategy


class VolatilityExpansionBreakoutStrategy(Strategy):
    """Volatility-expansion breakout (roadmap shortlist candidate #4) — a
    different entry hypothesis from donchian's plain price-channel breakout:
    the *precondition* for a signal isn't just price clearing a channel, it's
    that volatility itself was recently compressed and is now expanding.

    Bollinger Band width (normalized by price, self-relative like
    NatrRegimeFilter rather than a fixed universal threshold) sitting at or
    below its own rolling `squeeze_percentile` over `squeeze_lookback` bars
    marks a squeeze — the market coiling rather than trending. A signal only
    fires when the bar *before* the breakout was squeezed and the breakout
    bar's width has expanded from there and closed beyond the *prior* bar's
    band edge: the squeeze-then-expansion sequence, not merely "band was
    already wide and price broke out," which donchian-with-a-volatility-
    regime-filter would already catch and isn't a distinct hypothesis.

    Breakout is checked against the prior bar's band (not the current bar's)
    for the same reason donchian compares against a shifted channel: with a
    short `bb_period`, a single extreme close pulls that same bar's own
    rolling mean/std wide enough to often still contain itself, which would
    make a real breakout invisible against same-bar bands.

    Same ATR trailing-stop mechanism as the other trend strategies, so any
    performance difference traces to the entry rule, not the exit."""

    name = "vol_expansion"

    def __init__(self, params: dict):
        super().__init__(params)
        self.bb_period = params.get("bb_period", 20)
        self.bb_std = params.get("bb_std", 2.0)
        self.squeeze_lookback = params.get("squeeze_lookback", 100)
        self.squeeze_percentile = params.get("squeeze_percentile", 0.2)
        self.atr_period = params.get("atr_period", 14)
        self.atr_mult = params.get("atr_mult", 2.0)
        # bb_period to warm up the bands, then squeeze_lookback more so the
        # rolling width-percentile at the last row is based on mostly
        # settled values, plus 1 to compare bar t-1 against bar t.
        self.min_lookback = (
            max(self.bb_period, self.atr_period * 3) + self.squeeze_lookback + 1
        )

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_lookback:
            return None

        bands = bollinger_bands(df["close"], self.bb_period, self.bb_std)
        width_pct = (bands["bb_upper"] - bands["bb_lower"]) / df["close"]
        threshold = width_pct.rolling(self.squeeze_lookback).quantile(self.squeeze_percentile)

        prior_squeeze = width_pct.iloc[-2] <= threshold.iloc[-2] if pd.notna(threshold.iloc[-2]) else False
        expanding = width_pct.iloc[-1] > width_pct.iloc[-2]
        # the prior (not current) bar's band — see class docstring
        upper, lower = bands["bb_upper"].iloc[-2], bands["bb_lower"].iloc[-2]
        close = df["close"].iloc[-1]

        if pd.isna(upper) or pd.isna(lower) or not prior_squeeze or not expanding:
            return None

        atr_val = atr(df["high"], df["low"], df["close"], self.atr_period).iloc[-1]
        if pd.isna(atr_val):
            return None

        timestamp = df.index[-1]
        context = {
            "bb_width_pct": round(float(width_pct.iloc[-1]), 5),
            "prior_bb_width_pct": round(float(width_pct.iloc[-2]), 5),
            "atr": round(float(atr_val), 2),
            "atr_pct": round(float(atr_val / close * 100), 3),
        }

        if close > upper:
            return Signal(
                symbol="", timeframe="", direction="long", entry_price=close,
                stop_loss=close - self.atr_mult * atr_val, take_profit=None,
                reason=(
                    f"squeeze (bottom {self.squeeze_percentile:.0%} of "
                    f"{self.squeeze_lookback}-bar BB width) then upside expansion breakout"
                ),
                timestamp=timestamp, context=context,
            )
        if close < lower:
            return Signal(
                symbol="", timeframe="", direction="short", entry_price=close,
                stop_loss=close + self.atr_mult * atr_val, take_profit=None,
                reason=(
                    f"squeeze (bottom {self.squeeze_percentile:.0%} of "
                    f"{self.squeeze_lookback}-bar BB width) then downside expansion breakout"
                ),
                timestamp=timestamp, context=context,
            )
        return None

    def entry_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        bands = bollinger_bands(df["close"], self.bb_period, self.bb_std)
        width_pct = (bands["bb_upper"] - bands["bb_lower"]) / df["close"]
        threshold = width_pct.rolling(self.squeeze_lookback).quantile(self.squeeze_percentile)
        # the prior (not current) bar's band — see class docstring
        prior_upper = bands["bb_upper"].shift(1)
        prior_lower = bands["bb_lower"].shift(1)

        squeezed_prior = (width_pct.shift(1) <= threshold.shift(1)) & threshold.shift(1).notna()
        expanding = width_pct > width_pct.shift(1)
        close = df["close"]

        breakout_up = squeezed_prior & expanding & (close > prior_upper)
        breakout_down = squeezed_prior & expanding & (close < prior_lower)

        direction = np.where(breakout_up, "long", np.where(breakout_down, "short", None))
        atr_val = atr(df["high"], df["low"], df["close"], self.atr_period)
        stop_loss = np.where(
            breakout_up, close - self.atr_mult * atr_val,
            np.where(breakout_down, close + self.atr_mult * atr_val, np.nan),
        )
        reason = np.where(
            breakout_up,
            "squeeze then upside expansion breakout",
            np.where(breakout_down, "squeeze then downside expansion breakout", None),
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
        atr_val = atr(df["high"], df["low"], df["close"], self.atr_period).iloc[-1]
        if pd.isna(atr_val):
            return current_stop
        last_close = df["close"].iloc[-1]
        if direction == "long":
            return max(current_stop, last_close - self.atr_mult * atr_val)
        return min(current_stop, last_close + self.atr_mult * atr_val)
