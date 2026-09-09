import pandas as pd

from bot.indicators.indicators import atr, ema
from bot.strategy.base import Direction, Signal, Strategy


class MultiTimeframeTrendPullbackStrategy(Strategy):
    """Multi-timeframe trend + pullback (user's roadmap, Tier 1 #2). A
    higher timeframe establishes trend direction; entries only trigger on
    the daily chart, and only in the direction the higher timeframe
    confirms — the higher timeframe isn't itself the entry signal, it's a
    permission gate for what the daily chart is allowed to act on.

    Long: higher-timeframe close above its own SMA (trend up) AND the daily
    chart recently dipped below its own short EMA (a pullback) AND has now
    reclaimed it (resumption). Short is the mirror image. ATR trailing stop
    from there, same mechanism as ema_cross/donchian/market_structure.

    Resamples the *same* daily data the bot already collects into the
    higher timeframe internally (`htf_rule`, a pandas resample rule, e.g.
    "W" for weekly) rather than requiring a second live timeframe and a
    separate backfill — keeps the Strategy interface, and everything
    downstream (engine, shadow runner), completely unchanged.

    The most recent resampled higher-timeframe bar is always dropped before
    use: pandas resampling buckets the still-in-progress period along with
    completed ones, and treating it as complete would be exactly the kind
    of look-ahead the shadow runner's `drop_incomplete_bar` already guards
    against on the base timeframe."""

    name = "multi_timeframe"

    def __init__(self, params: dict):
        super().__init__(params)
        self.htf_rule = params.get("htf_rule", "W")
        self.htf_sma_period = params.get("htf_sma_period", 10)
        # only used to size min_lookback — how many base-timeframe bars
        # roughly make up one htf_rule period (7 for daily->weekly)
        self.approx_htf_period_bars = params.get("approx_htf_period_bars", 7)
        self.pullback_ema_period = params.get("pullback_ema_period", 10)
        self.pullback_lookback = params.get("pullback_lookback", 5)
        self.atr_period = params.get("atr_period", 14)
        self.atr_mult = params.get("atr_mult", 2.0)
        self.min_lookback = (
            (self.htf_sma_period + 3) * self.approx_htf_period_bars
            + max(self.pullback_ema_period, self.atr_period) * 3
        )

    def _htf_trend(self, df: pd.DataFrame) -> tuple[bool, bool]:
        """Returns (bullish, bearish) using only *completed* higher-timeframe
        bars — the most recently resampled one is dropped since it's very
        likely still in progress relative to df's own last row."""
        htf = df.resample(self.htf_rule).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        htf = htf.iloc[:-1]
        if len(htf) < self.htf_sma_period + 1:
            return False, False
        htf_sma = htf["close"].rolling(self.htf_sma_period).mean()
        last_close = htf["close"].iloc[-1]
        last_sma = htf_sma.iloc[-1]
        if pd.isna(last_sma):
            return False, False
        return bool(last_close > last_sma), bool(last_close < last_sma)

    def _pullback_signal(self, df: pd.DataFrame, direction: Direction) -> bool:
        ltf_ema = ema(df["close"], self.pullback_ema_period)
        if pd.isna(ltf_ema.iloc[-1]) or pd.isna(ltf_ema.iloc[-1 - self.pullback_lookback]):
            return False
        recent_close = df["close"].iloc[-self.pullback_lookback - 1 : -1]
        recent_ema = ltf_ema.iloc[-self.pullback_lookback - 1 : -1]
        if direction == "long":
            was_below = (recent_close < recent_ema).any()
            now_above = df["close"].iloc[-1] > ltf_ema.iloc[-1]
            return bool(was_below and now_above)
        was_above = (recent_close > recent_ema).any()
        now_below = df["close"].iloc[-1] < ltf_ema.iloc[-1]
        return bool(was_above and now_below)

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        if len(df) < self.min_lookback:
            return None

        bullish, bearish = self._htf_trend(df)
        if not bullish and not bearish:
            return None

        atr_val = atr(df["high"], df["low"], df["close"], self.atr_period)
        last_atr = atr_val.iloc[-1]
        if pd.isna(last_atr):
            return None

        entry = df["close"].iloc[-1]
        timestamp = df.index[-1]
        context = {
            "htf_rule": self.htf_rule,
            "htf_trend": "bullish" if bullish else "bearish",
            "atr": round(float(last_atr), 2),
            "atr_pct": round(float(last_atr / entry * 100), 3),
        }

        if bullish and self._pullback_signal(df, "long"):
            return Signal(
                symbol="", timeframe="", direction="long", entry_price=entry,
                stop_loss=entry - self.atr_mult * last_atr, take_profit=None,
                reason=(
                    f"higher-timeframe ({self.htf_rule}) uptrend, daily pullback to "
                    f"EMA{self.pullback_ema_period} reclaimed"
                ),
                timestamp=timestamp,
                context=context,
            )
        if bearish and self._pullback_signal(df, "short"):
            return Signal(
                symbol="", timeframe="", direction="short", entry_price=entry,
                stop_loss=entry + self.atr_mult * last_atr, take_profit=None,
                reason=(
                    f"higher-timeframe ({self.htf_rule}) downtrend, daily pullback to "
                    f"EMA{self.pullback_ema_period} reclaimed"
                ),
                timestamp=timestamp,
                context=context,
            )
        return None

    def diagnose(self, df: pd.DataFrame) -> dict:
        if len(df) < self.min_lookback:
            return {"ready": False}

        bullish, bearish = self._htf_trend(df)
        trend = "bullish" if bullish else "bearish" if bearish else "neutral"

        ltf_ema = ema(df["close"], self.pullback_ema_period)
        last_ema = ltf_ema.iloc[-1]
        last_close = df["close"].iloc[-1]
        if pd.isna(last_ema):
            return {"ready": False, "htf_trend": trend}

        recent_close = df["close"].iloc[-self.pullback_lookback - 1 : -1]
        recent_ema = ltf_ema.iloc[-self.pullback_lookback - 1 : -1]
        dipped_below = bool((recent_close < recent_ema).any())
        bounced_above = bool((recent_close > recent_ema).any())
        dist_to_ema_pct = round(float((last_close - last_ema) / last_ema * 100), 3)

        # "awaiting reclaim": the higher timeframe already confirms a
        # direction and the daily chart has pulled back to/through the EMA
        # recently, but hasn't closed back on the trend side of it yet —
        # exactly the "pullback after trend" setup a human would be watching
        # for by eye.
        near_miss, near_miss_key, near_miss_reason = False, None, None
        if trend == "bullish" and dipped_below and last_close <= last_ema:
            near_miss, near_miss_key = True, "bullish_pullback_awaiting_reclaim"
            near_miss_reason = (
                f"HTF ({self.htf_rule}) bullish, daily dipped below "
                f"EMA{self.pullback_ema_period} recently, still "
                f"{abs(dist_to_ema_pct):.2f}% below it — awaiting a reclaim close"
            )
        elif trend == "bearish" and bounced_above and last_close >= last_ema:
            near_miss, near_miss_key = True, "bearish_pullback_awaiting_reclaim"
            near_miss_reason = (
                f"HTF ({self.htf_rule}) bearish, daily bounced above "
                f"EMA{self.pullback_ema_period} recently, still "
                f"{dist_to_ema_pct:.2f}% above it — awaiting a reclaim close"
            )

        return {
            "ready": True,
            "htf_trend": trend,
            "close": round(float(last_close), 2),
            "ema": round(float(last_ema), 2),
            "dist_to_ema_pct": dist_to_ema_pct,
            "recent_dip_below_ema": dipped_below,
            "recent_bounce_above_ema": bounced_above,
            "near_miss": near_miss,
            "near_miss_key": near_miss_key,
            "near_miss_reason": near_miss_reason,
        }

    def trail_stop(self, df: pd.DataFrame, direction: Direction, current_stop: float) -> float:
        atr_val = atr(df["high"], df["low"], df["close"], self.atr_period)
        last_atr = atr_val.iloc[-1]
        if pd.isna(last_atr):
            return current_stop
        last_close = df["close"].iloc[-1]
        if direction == "long":
            return max(current_stop, last_close - self.atr_mult * last_atr)
        return min(current_stop, last_close + self.atr_mult * last_atr)
