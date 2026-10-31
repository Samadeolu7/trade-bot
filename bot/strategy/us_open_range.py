import pandas as pd

from bot.strategy.base import Signal, Strategy

BAR = pd.Timedelta(minutes=15)


def _clock(value: str) -> pd.Timedelta:
    hours, minutes = value.split(":")
    return pd.Timedelta(hours=int(hours), minutes=int(minutes))


class UsOpenRangeBreakoutStrategy(Strategy):
    """US-open range breakout on 15m candles: the live version of
    bot/research/intraday.py's `orb(..., session="always")`, the best
    hypothesis #7 result (notes/research_log.md), here for a forward paper
    test.

    Weekdays only. The range is the high/low of the 15m candles from
    `range_start` (13:30 UTC, the US cash open in summer, kept fixed all
    year as in the backtest) for `range_minutes`. The first later candle
    that closes outside the range, opening no later than `last_entry`, is
    the day's one entry: long above, short below, stop at the range's
    middle (`stop_at: mid`) or far side (`opposite`). The position is
    closed at the close of the `flat_at` candle (23:30 UTC, so out by
    23:45) if the stop hasn't hit; see exit_due."""

    name = "us_open_range"

    def __init__(self, params: dict):
        super().__init__(params)
        self.range_start = _clock(params.get("range_start", "13:30"))
        self.range_bars = int(params.get("range_minutes", 60)) // 15
        self.last_entry = _clock(params.get("last_entry", "21:30"))
        self.flat_at = _clock(params.get("flat_at", "23:30"))
        self.stop_at = params.get("stop_at", "mid")
        if self.stop_at not in ("mid", "opposite"):
            raise ValueError(f"us_open_range stop_at must be 'mid' or 'opposite', got {self.stop_at!r}")
        if self.range_bars < 1:
            raise ValueError("us_open_range range_minutes must be at least 15")
        # a full day of 15m bars always covers today's range and breakout window
        self.min_lookback = 96

    def _read(self, df: pd.DataFrame) -> dict:
        """Today's range and whether the last candle is the day's first
        close outside it."""
        t = df.index[-1]
        day = t.normalize()
        start = day + self.range_start
        end = start + BAR * self.range_bars  # first candle after the range
        out = {"bar": t, "weekday": day.weekday() < 5, "range_high": None, "range_low": None,
               "in_window": end <= t <= day + self.last_entry, "broke_earlier": False, "direction": None}
        if not out["weekday"]:
            return out
        rng = df[(df.index >= start) & (df.index < end)]
        if len(rng) != self.range_bars:
            return out  # range not finished yet, or candles missing from it
        hi, lo = float(rng["high"].max()), float(rng["low"].min())
        out.update(range_high=hi, range_low=lo)
        since = df[(df.index >= end) & (df.index < t)]["close"]
        out["broke_earlier"] = bool(((since > hi) | (since < lo)).any())
        close = float(df["close"].iloc[-1])
        if out["in_window"] and not out["broke_earlier"]:
            out["direction"] = "long" if close > hi else "short" if close < lo else None
        return out

    def generate_signal(self, df: pd.DataFrame) -> Signal | None:
        r = self._read(df)
        side = r["direction"]
        if side is None:
            return None
        hi, lo = r["range_high"], r["range_low"]
        mid = (hi + lo) / 2
        stop = mid if self.stop_at == "mid" else (lo if side == "long" else hi)
        return Signal(
            symbol="", timeframe="", direction=side, entry_price=float(df["close"].iloc[-1]),
            stop_loss=float(stop), take_profit=None,
            reason=(f"first 15m close {'above' if side == 'long' else 'below'} the US-open range "
                    f"{lo:,.2f}-{hi:,.2f}; closes by {self._flat_close()} UTC"),
            timestamp=df.index[-1],
            context={"range_high": hi, "range_low": lo, "stop_at": self.stop_at},
        )

    def _flat_close(self) -> str:
        t = self.flat_at + BAR
        return f"{t.components.hours:02d}:{t.components.minutes:02d}"

    def exit_due(self, df: pd.DataFrame, entry_time) -> str | None:
        """Called by the feed runner for an open position after the stop
        check: the time exit, at this candle's close. Also fires on any
        later day's candle, in case the engine missed the flat_at one."""
        t = df.index[-1]
        flat = pd.Timestamp(entry_time).tz_convert("UTC").normalize() + self.flat_at
        return "end of day" if t >= flat else None

    def diagnose(self, df: pd.DataFrame) -> dict:
        r = self._read(df)
        if not r["weekday"]:
            state = "weekend: no trades"
        elif r["range_high"] is None or r["bar"] < r["bar"].normalize() + self.range_start + BAR * self.range_bars:
            state = "waiting for the US-open range to finish"
        elif r["broke_earlier"]:
            state = "today's breakout already happened"
        elif not r["in_window"]:
            state = "past today's last entry time"
        else:
            state = "watching for a close outside the range"
        return {"near_miss": False, "near_miss_key": None, "near_miss_reason": None, "state": state,
                "range_high": r["range_high"], "range_low": r["range_low"]}
